#!/usr/bin/env python3
"""Face-sticker benchmark: GPT-Image-2.5 Flare vs classic face swap.

Runs every (selfie x template x method), turns each output into a WhatsApp-
ready sticker (512x512 WebP <100KB, white outline), and writes results.jsonl,
results.csv and an HTML contact sheet with per-method metrics and a 1-5
human rating widget. See README.md.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import html
import io
import json
import os
import statistics
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent


def load_env():
    """Read KEY=VALUE lines from the repo's .env (and bench/.env) without overriding the real env.
    Accepts OPENAI_KEY as an alias for the SDK's OPENAI_API_KEY."""
    for path in (HERE.parent / ".env", HERE / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    if "OPENAI_API_KEY" not in os.environ and os.environ.get("OPENAI_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPENAI_KEY"]

# USD per 1M tokens for gpt-image-2.5-flare (Sep 2026). Update if pricing changes.
FLARE_PRICES = {"text_in": 5.0, "image_in": 8.0, "image_out": 30.0, "text_out": 10.0}
# Rough per-image output cost (GPT Image 2, 1024^2) — only for the --dry-run estimate.
FLARE_EST = {"low": 0.006, "medium": 0.053, "high": 0.211, "xhigh": 0.30, "max": 0.40, "auto": 0.053}

# FFHQ 5-point template at 512px (eyes, nose, mouth corners) for GFPGAN alignment.
FFHQ_512 = np.array(
    [[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
     [201.26117, 371.41043], [313.08905, 371.15118]],
    dtype=np.float32,
)

STYLE_HINTS = {
    "photo": "photorealistic, lighting and color matching the template",
    "cartoon": "bold, clean cartoon illustration with flat colors and crisp linework",
}

PROMPT = """\
Image 1 is a photo of a real person.{template_line}
Create ONE sticker of the person from image 1: {scene}.
Identity: keep their face shape, eyes, nose, mouth, skin tone, facial hair and \
hairstyle so they are instantly recognizable.
Style: {style}.
Format: a single character, centered, fully in frame, transparent background, \
no text, no outline, no border, no shadow on the background."""

TEMPLATE_LINE = (
    " Image 2 is the sticker template: copy its pose, expression, outfit, framing "
    "and art style, but give the character the face of the person in image 1."
)


# ---------------------------------------------------------------- data types


@dataclass
class Template:
    id: str
    style: str
    scene: str
    image: Path | None


@dataclass
class Result:
    key: str
    method: str
    selfie: str
    template: str
    style: str
    status: str  # ok | refused | unsupported | error
    error: str | None = None
    latency_s: float | None = None
    cost_usd: float | None = None
    cost_is_estimate: bool = False
    identity: float | None = None  # ArcFace cosine(selfie, output); None = no face found
    out_faces: int | None = None
    sticker_bytes: int | None = None
    webp_quality: int | None = None
    raw_path: str | None = None
    sticker_path: str | None = None
    tokens: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def onnx_providers() -> list[str]:
    import onnxruntime as ort

    avail = ort.get_available_providers()
    return [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]


def largest_face(faces):
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return img


def rgba_to_bgr_on_white(rgba: Image.Image) -> np.ndarray:
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(bg, rgba.convert("RGBA")).convert("RGB")
    return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def is_opaque(rgba: Image.Image) -> bool:
    a = np.array(rgba.convert("RGBA"))[:, :, 3]
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return border.mean() > 250


def to_sticker(rgba: Image.Image, size=512, outline=10, max_bytes=100_000):
    """Trim, fit to size x size, add a white die-cut outline, encode WebP under max_bytes."""
    arr = np.array(rgba.convert("RGBA"))
    arr[:, :, 3][arr[:, :, 3] < 64] = 0  # drop faint matting ghosts before outlining
    rgba = Image.fromarray(arr, "RGBA")
    a = arr[:, :, 3]
    ys, xs = np.where(a > 8)
    if len(xs) == 0:
        raise ValueError("output is fully transparent")
    crop = rgba.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    inner = size - 2 * (outline + 4)
    scale = inner / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(crop, ((size - crop.width) // 2, (size - crop.height) // 2), crop)

    alpha = (np.array(canvas)[:, :, 3] > 127).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * outline + 1, 2 * outline + 1))
    ring = cv2.GaussianBlur(cv2.dilate(alpha, k), (3, 3), 0)
    base = np.zeros((size, size, 4), np.uint8)
    base[:, :, :3] = 255
    base[:, :, 3] = ring
    sticker = Image.alpha_composite(Image.fromarray(base, "RGBA"), canvas)

    for q in (90, 80, 70, 60, 50, 40, 30, 20):
        buf = io.BytesIO()
        sticker.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= max_bytes:
            break
    return buf.getvalue(), q


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# ---------------------------------------------------------------- models


class Faces:
    """insightface buffalo_l: detection + ArcFace embeddings (also used for metrics)."""

    def __init__(self):
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(name="buffalo_l", providers=onnx_providers())
        self.app.prepare(ctx_id=0, det_size=(640, 640))

    def get(self, bgr):
        return self.app.get(bgr)

    def embed(self, bgr):
        faces = self.get(bgr)
        if not faces:
            return None, 0
        return largest_face(faces).normed_embedding, len(faces)


class ClassicSwapper:
    """inswapper_128 face swap, optionally followed by GFPGAN 1.4 face restoration."""

    def __init__(self, faces: Faces, inswapper: Path, gfpgan: Path | None):
        import insightface
        import onnxruntime as ort

        self.faces = faces
        self.swapper = insightface.model_zoo.get_model(str(inswapper), providers=onnx_providers())
        self.gfpgan = ort.InferenceSession(str(gfpgan), providers=onnx_providers()) if gfpgan else None

    def swap(self, src_face, template_bgr, restore: bool) -> np.ndarray:
        targets = self.faces.get(template_bgr)
        if not targets:
            raise ValueError("no face found in template image")
        target = largest_face(targets)
        out = self.swapper.get(template_bgr.copy(), target, src_face, paste_back=True)
        if restore:
            if self.gfpgan is None:
                raise ValueError("classic_restore needs --gfpgan")
            out = self._restore(out, target.kps)
        return out

    def _restore(self, img, kps):
        h, w = img.shape[:2]
        M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), FFHQ_512, method=cv2.LMEDS)
        crop = cv2.warpAffine(img, M, (512, 512), borderMode=cv2.BORDER_REPLICATE)
        x = (crop[:, :, ::-1].astype(np.float32) / 255.0 - 0.5) / 0.5
        x = x.transpose(2, 0, 1)[None]
        y = self.gfpgan.run(None, {self.gfpgan.get_inputs()[0].name: x})[0][0]
        y = ((np.clip(y, -1, 1) + 1) / 2 * 255).transpose(1, 2, 0)[:, :, ::-1]
        y = y.round().astype(np.uint8)

        mask = np.zeros((512, 512), np.float32)
        cv2.rectangle(mask, (40, 40), (472, 472), 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 16)
        IM = cv2.invertAffineTransform(M)
        back = cv2.warpAffine(y, IM, (w, h))
        m = cv2.warpAffine(mask, IM, (w, h))[:, :, None]
        return (m * back + (1 - m) * img).astype(np.uint8)


def _input_file(path: Path, max_px: int):
    """Open an input image, downscaled to max_px on the long side (input images dominate Flare cost)."""
    if not max_px:
        return open(path, "rb")
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    buf.seek(0)
    buf.name = path.stem + ".jpg"
    return buf


def call_flare(client, model, quality, fidelity, size, selfie: Path, tpl: Template, input_px: int = 0):
    """Runs in a worker thread. Returns (png_bytes, latency_s, cost_usd, tokens)."""
    prompt = PROMPT.format(
        template_line=TEMPLATE_LINE if tpl.image else "",
        scene=tpl.scene,
        style=STYLE_HINTS.get(tpl.style, tpl.style),
    )
    paths = [selfie] + ([tpl.image] if tpl.image else [])
    files = [_input_file(p, input_px) for p in paths]
    # gpt-image-2.5-flare rejects input_fidelity; only send it when asked (e.g. for gpt-image-1.5).
    extra = {"input_fidelity": fidelity} if fidelity != "none" else {}
    t0 = time.perf_counter()
    try:
        r = client.images.edit(
            model=model,
            image=files,
            prompt=prompt,
            size=size,
            quality=quality,
            background="transparent",
            output_format="png",
            n=1,
            **extra,
        )
    finally:
        for f in files:
            f.close()
    latency = time.perf_counter() - t0

    u = r.usage
    tokens, cost = {}, None
    if u is not None:
        idet = getattr(u, "input_tokens_details", None)
        odet = getattr(u, "output_tokens_details", None)
        tokens = {
            "text_in": getattr(idet, "text_tokens", 0) or 0,
            "image_in": getattr(idet, "image_tokens", 0) or 0,
            "image_out": (getattr(odet, "image_tokens", None) if odet else None) or u.output_tokens or 0,
            "text_out": (getattr(odet, "text_tokens", 0) if odet else 0) or 0,
        }
        cost = sum(tokens[k] * FLARE_PRICES[k] for k in tokens) / 1e6
    return base64.b64decode(r.data[0].b64_json), latency, cost, tokens


# ---------------------------------------------------------------- run


def load_templates(path: Path) -> list[Template]:
    data = tomllib.loads(path.read_text())
    out = []
    for t in data.get("template", []):
        img = (path.parent / t["image"]) if t.get("image") else None
        if img and not img.exists():
            print(f"  ! template {t['id']}: {img.name} missing, skipping", file=sys.stderr)
            continue
        out.append(Template(t["id"], t.get("style", "photo"), t["scene"], img))
    return out


def parse_methods(spec: str) -> list[str]:
    methods = [m.strip() for m in spec.split(",") if m.strip()]
    for m in methods:
        if m not in ("classic", "classic_restore") and not m.startswith("flare:"):
            sys.exit(f"unknown method {m!r} (use classic, classic_restore, flare:<quality>)")
    return methods


def cmd_run(args):
    selfie_dir, tpl_file = Path(args.selfies), Path(args.templates)
    selfies = sorted(p for p in selfie_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    templates = load_templates(tpl_file)
    if args.limit_selfies:
        selfies = selfies[: args.limit_selfies]
    if args.limit_templates:
        templates = templates[: args.limit_templates]
    methods = parse_methods(args.methods)
    if not selfies or not templates:
        sys.exit(f"need at least one selfie in {selfie_dir} and one template in {tpl_file}")

    run_dir = Path(args.run_dir or HERE / "runs" / dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (run_dir / "stickers").mkdir(exist_ok=True)
    jsonl = run_dir / "results.jsonl"

    done = {}
    if jsonl.exists():  # resume: keep everything except errors
        for line in jsonl.read_text().splitlines():
            r = json.loads(line)
            if r["status"] != "error":
                done[r["key"]] = r

    jobs = [(m, s, t) for s in selfies for t in templates for m in methods]
    todo = [j for j in jobs if f"{j[0]}__{j[1].stem}__{j[2].id}" not in done]
    flare_todo = [j for j in todo if j[0].startswith("flare:")]

    print(f"{len(selfies)} selfies x {len(templates)} templates x {len(methods)} methods "
          f"= {len(jobs)} jobs ({len(jobs) - len(todo)} already done) -> {run_dir}")
    if flare_todo:
        est = sum(FLARE_EST.get(m.split(":", 1)[1], 0.1) for m, _, _ in flare_todo)
        print(f"Flare: {len(flare_todo)} API calls, rough estimate ~${est:.2f} "
              f"(output only; input images add a little)")
    if args.dry_run:
        return
    if flare_todo and not args.yes:
        if input("Proceed with paid API calls? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")

    print("loading insightface buffalo_l ...")
    faces = Faces()

    selfie_faces = {}
    for s in selfies:
        found = faces.get(read_bgr(s))
        if len(found) != 1:
            print(f"  ! {s.name}: {len(found)} faces detected (need exactly 1), skipping", file=sys.stderr)
            continue
        selfie_faces[s.stem] = found[0]
    todo = [j for j in todo if j[1].stem in selfie_faces]
    flare_todo = [j for j in todo if j[0].startswith("flare:")]

    swapper = None
    if any(m.startswith("classic") for m, _, _ in todo):
        if not args.inswapper or not Path(args.inswapper).exists():
            sys.exit(f"classic methods need --inswapper (not found: {args.inswapper})")
        needs_gfpgan = any(m == "classic_restore" for m, _, _ in todo)
        gfpgan = Path(args.gfpgan) if needs_gfpgan and args.gfpgan else None
        if needs_gfpgan and (gfpgan is None or not gfpgan.exists()):
            sys.exit(f"classic_restore needs --gfpgan (not found: {args.gfpgan})")
        swapper = ClassicSwapper(faces, Path(args.inswapper), gfpgan)

    rembg_session = None

    def remove_bg(img: Image.Image) -> Image.Image:
        nonlocal rembg_session
        from rembg import new_session, remove

        if rembg_session is None:
            rembg_session = new_session(args.rembg_model)
        return remove(img, session=rembg_session)

    out = open(jsonl, "a")

    def finalize(method, selfie: Path, tpl: Template, fn):
        """fn() -> (rgba PIL image, latency, cost, cost_is_estimate, tokens). Records a Result."""
        key = f"{method}__{selfie.stem}__{tpl.id}"
        res = Result(key, method, selfie.name, tpl.id, tpl.style, "ok")
        try:
            rgba, res.latency_s, res.cost_usd, res.cost_is_estimate, res.tokens = fn()
            fname = key.replace(":", "-")  # no colons in file names (URLs, Windows)
            raw = run_dir / "raw" / f"{fname}.png"
            rgba.save(raw)
            res.raw_path = str(raw.relative_to(run_dir))

            emb, n = faces.embed(rgba_to_bgr_on_white(rgba))
            res.out_faces = n
            if emb is not None:
                res.identity = round(float(np.dot(selfie_faces[selfie.stem].normed_embedding, emb)), 4)

            if is_opaque(rgba):
                rgba = remove_bg(rgba)
            data, res.webp_quality = to_sticker(rgba, max_bytes=args.max_bytes)
            st = run_dir / "stickers" / f"{fname}.webp"
            st.write_bytes(data)
            res.sticker_path = str(st.relative_to(run_dir))
            res.sticker_bytes = len(data)
        except Unsupported as e:
            res.status, res.error = "unsupported", str(e)
        except Refused as e:
            res.status, res.error = "refused", str(e)
        except Exception as e:  # noqa: BLE001 — record and keep going
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
        out.write(json.dumps(asdict(res)) + "\n")
        out.flush()
        ident = f"id={res.identity:.3f}" if res.identity is not None else "id=—"
        lat = f"{res.latency_s:.1f}s" if res.latency_s else ""
        print(f"  [{res.status:>11}] {key}  {ident} {lat} {res.error or ''}")

    # Flare calls go to a thread pool; classic runs inline meanwhile.
    pool = ThreadPoolExecutor(max_workers=args.concurrency) if flare_todo else None
    futures = {}
    if flare_todo:
        from openai import OpenAI

        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("Flare needs OPENAI_API_KEY (or OPENAI_KEY in the repo's .env)")
        client = OpenAI(max_retries=6, timeout=300)
        for m, s, t in flare_todo:
            q = m.split(":", 1)[1]
            fut = pool.submit(call_flare, client, args.flare_model, q, args.flare_fidelity, args.flare_size, s, t,
                              args.flare_input_px)
            futures[fut] = (m, s, t)

    for m, s, t in todo:
        if not m.startswith("classic"):
            continue

        def classic(m=m, s=s, t=t):
            if t.image is None:
                raise Unsupported("prompt-only template (classic needs a template image)")
            t0 = time.perf_counter()
            bgr = swapper.swap(selfie_faces[s.stem], read_bgr(t.image), restore=(m == "classic_restore"))
            lat = time.perf_counter() - t0
            rgba = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
            return rgba, lat, lat * args.gpu_usd_per_hour / 3600, True, {}

        finalize(m, s, t, classic)

    for fut in as_completed(futures):
        m, s, t = futures[fut]

        def flare(fut=fut):
            from openai import BadRequestError

            try:
                png, lat, cost, tokens = fut.result()
            except BadRequestError as e:
                body = str(e).lower()
                if "moderation" in body or "safety" in body or getattr(e, "code", None) == "moderation_blocked":
                    raise Refused(str(e)[:300]) from e
                raise
            return Image.open(io.BytesIO(png)).convert("RGBA"), lat, cost, False, tokens

        finalize(m, s, t, flare)

    if pool:
        pool.shutdown()
    out.close()
    write_report(run_dir, selfie_dir, tpl_file.parent)


class Unsupported(Exception):
    pass


class Refused(Exception):
    pass


# ---------------------------------------------------------------- report


def summarize(rows: list[dict]) -> list[dict]:
    by = {}
    for r in rows:
        by.setdefault(r["method"], []).append(r)
    out = []
    for method, rs in by.items():
        ok = [r for r in rs if r["status"] == "ok"]
        ids = [r["identity"] for r in ok if r["identity"] is not None]
        lats = [r["latency_s"] for r in ok if r["latency_s"] is not None]
        costs = [r["cost_usd"] for r in ok if r["cost_usd"] is not None]
        per_style = {}
        for style in sorted({r["style"] for r in rs}):
            sid = [r["identity"] for r in ok if r["style"] == style and r["identity"] is not None]
            per_style[style] = round(statistics.mean(sid), 3) if sid else None
        out.append({
            "method": method,
            "jobs": len(rs),
            "ok": len(ok),
            "refused": sum(r["status"] == "refused" for r in rs),
            "unsupported": sum(r["status"] == "unsupported" for r in rs),
            "errors": sum(r["status"] == "error" for r in rs),
            "face_found_pct": round(100 * sum(r["identity"] is not None for r in ok) / len(ok), 1) if ok else None,
            "identity_mean": round(statistics.mean(ids), 3) if ids else None,
            "identity_median": round(statistics.median(ids), 3) if ids else None,
            "identity_by_style": per_style,
            "latency_p50": round(pct(lats, 50), 2) if lats else None,
            "latency_p95": round(pct(lats, 95), 2) if lats else None,
            "cost_mean": round(statistics.mean(costs), 5) if costs else None,
            "cost_per_1000": round(1000 * statistics.mean(costs), 2) if costs else None,
            "cost_is_estimate": any(r["cost_is_estimate"] for r in ok),
            "sticker_kb_mean": round(statistics.mean(r["sticker_bytes"] for r in ok if r["sticker_bytes"]) / 1024, 1)
            if any(r["sticker_bytes"] for r in ok) else None,
        })
    return sorted(out, key=lambda s: s["method"])


def write_report(run_dir: Path, selfie_dir: Path, tpl_dir: Path, raw_links: bool = True):
    rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    latest = {}
    for r in rows:  # last attempt per key wins (resume re-runs errors)
        latest[r["key"]] = r
    rows = list(latest.values())
    summary = summarize(rows)

    with open(run_dir / "results.csv", "w", newline="") as f:
        cols = [k for k in Result.__dataclass_fields__ if k != "tokens"] + ["tokens"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "tokens": json.dumps(r.get("tokens") or {})})
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    methods = sorted({r["method"] for r in rows})
    pairs = sorted({(r["selfie"], r["template"]) for r in rows})
    cell = {(r["selfie"], r["template"], r["method"]): r for r in rows}
    tpl_images = {}
    try:
        for t in load_templates(tpl_dir / "templates.toml"):
            if t.image:
                tpl_images[t.id] = t.image
    except Exception:  # noqa: BLE001
        pass

    # Small copies of the inputs inside the run dir, so the report is self-contained.
    thumbs = {}
    for kind, src in [("selfies", selfie_dir / s) for s, _ in pairs] + [("templates", p) for p in tpl_images.values()]:
        if src in thumbs or not src.exists():
            continue
        dst = run_dir / "inputs" / kind / (src.stem + ".jpg")
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            im = Image.open(src).convert("RGB")
            im.thumbnail((256, 256))
            im.save(dst, "JPEG", quality=85)
        thumbs[src] = dst

    def rel(p: Path) -> str:
        return html.escape(os.path.relpath(p, run_dir))

    def fmt(v, suffix="", nd=3):
        if v is None:
            return "—"
        return f"{v:.{nd}f}{suffix}" if isinstance(v, float) else f"{v}{suffix}"

    def cost(v, est):
        return "—" if v is None else f"{'~' if est else ''}${v:,.2f}"

    sum_rows = "".join(
        f"<tr><th scope='row'>{html.escape(s['method'])}</th><td>{s['ok']}/{s['jobs']}</td>"
        f"<td>{s['refused'] or ''}</td><td>{s['unsupported'] or ''}</td><td>{s['errors'] or ''}</td>"
        f"<td>{fmt(s['identity_mean'])}</td>"
        + "".join(f"<td>{fmt(s['identity_by_style'].get(st))}</td>" for st in ("photo", "cartoon"))
        + f"<td>{fmt(s['latency_p50'], ' s', 1)}</td><td>{fmt(s['latency_p95'], ' s', 1)}</td>"
        f"<td>{cost(s['cost_per_1000'], s['cost_is_estimate'])}</td>"
        f"<td>{fmt(s['sticker_kb_mean'], ' KB', 1)}</td>"
        f"<td class='rating' data-method='{html.escape(s['method'])}'>—</td></tr>"
        for s in summary
    )

    grid = []
    for selfie, tpl in pairs:
        head = ""
        for src in (selfie_dir / selfie, tpl_images.get(tpl)):
            if src in thumbs:
                head += f"<img class='thumb' src='{rel(thumbs[src])}' alt='' loading='lazy'>"
        if tpl not in tpl_images:
            head += "<span class='thumb prompt'>prompt<br>only</span>"
        head = (f"<div class='inputs'>{head}</div><div class='lbl'>{html.escape(Path(selfie).stem)}"
                f"<br><span>× {html.escape(tpl)}</span></div>")
        tds = [f"<th scope='row' class='head'>{head}</th>"]
        for m in methods:
            r = cell.get((selfie, tpl, m))
            if r is None:
                tds.append("<td></td>")
            elif r["status"] == "ok":
                meta = (f"id {fmt(r['identity'])} · {fmt(r['latency_s'], 's', 1)} · "
                        f"{'~' if r['cost_is_estimate'] else ''}${fmt(r['cost_usd'], '', 4)}")
                btns = "".join(f"<button type='button' data-v='{v}' aria-label='rate {v}'>{v}</button>" for v in range(1, 6))
                img = f"<img class='st' src='{rel(run_dir / r['sticker_path'])}' alt='' loading='lazy'>"
                if raw_links and r.get("raw_path"):
                    img = f"<a href='{rel(run_dir / r['raw_path'])}' target='_blank'>{img}</a>"
                tds.append(f"<td>{img}<div class='meta'>{meta}</div>"
                           f"<div class='rate' data-key='{html.escape(r['key'])}' data-method='{html.escape(m)}'>{btns}</div></td>")
            else:
                tds.append(f"<td><div class='st status {r['status']}'><b>{r['status']}</b>"
                           f"<span>{html.escape((r['error'] or '')[:140])}</span></div></td>")
        grid.append("<tr>" + "".join(tds) + "</tr>")

    run_id = html.escape(run_dir.name)
    n_selfies, n_tpl = len({p[0] for p in pairs}), len({p[1] for p in pairs})
    body = f"""<title>Sticker Benchmark {run_id}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{--bg:#f4f5f7;--surface:#ffffff;--fg:#16181d;--muted:#5d6470;--line:#dde0e6;--accent:#2455c3;--on-accent:#ffffff;
--refused:#a3332b;--unsupported:#6b6f78;--chk-a:#dcdfe5;--chk-b:#ffffff}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#111317;--surface:#1a1d22;--fg:#e7e9ed;
--muted:#99a0ab;--line:#2b2f37;--accent:#86a8ff;--on-accent:#0d1220;--refused:#ff9a90;--unsupported:#9aa0aa;--chk-a:#2a2e35;--chk-b:#20232a}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#111317;--surface:#1a1d22;--fg:#e7e9ed;--muted:#99a0ab;--line:#2b2f37;
--accent:#86a8ff;--on-accent:#0d1220;--refused:#ff9a90;--unsupported:#9aa0aa;--chk-a:#2a2e35;--chk-b:#20232a}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 "IBM Plex Sans",system-ui,sans-serif;padding-inline:16px;padding-block:28px 48px}}
.page{{max-width:1400px;margin:0 auto;display:grid;gap:28px}}
header{{display:grid;gap:6px}}
.eyebrow{{font:500 12px/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
h1{{font-size:26px;line-height:1.2;font-weight:600;margin:0;text-wrap:balance}}
h2{{font-size:16px;font-weight:600;margin:0 0 10px}}
p{{margin:0;max-width:70ch;color:var(--muted)}} p b{{color:var(--fg);font-weight:600}}
.scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface)}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}}
thead th{{font:500 11px/1.3 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);white-space:nowrap}}
.summary td,.summary tbody th{{font:14px/1.4 "IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;white-space:nowrap}}
.summary tbody th{{font-weight:500}}
.summary .rating{{color:var(--accent)}}
tbody tr:last-child>*{{border-bottom:0}}
.grid td,.grid th{{border-right:1px solid var(--line)}} .grid tr>*:last-child{{border-right:0}}
.grid thead th{{position:sticky;top:0;background:var(--surface);z-index:1}}
.head{{min-width:150px;font-weight:400}}
.inputs{{display:flex;gap:4px}}
.thumb{{width:68px;height:68px;object-fit:cover;border-radius:6px;display:grid;place-items:center;text-align:center;
font:11px/1.2 "IBM Plex Mono",ui-monospace,monospace;color:var(--muted);border:1px dashed var(--line)}}
img.thumb{{border:0}}
.lbl{{font:12px/1.4 "IBM Plex Mono",ui-monospace,monospace;margin-top:6px}} .lbl span{{color:var(--muted)}}
.st{{width:176px;height:176px;max-width:100%;display:block;border-radius:6px;
background:repeating-conic-gradient(var(--chk-a) 0 25%,var(--chk-b) 0 50%) 0 0/16px 16px}}
.status{{display:flex;flex-direction:column;justify-content:center;gap:6px;padding:12px;box-sizing:border-box;background:none;
border:1px dashed var(--line);font-size:12px;color:var(--muted);overflow:hidden}}
.status b{{font:500 12px/1 "IBM Plex Mono",ui-monospace,monospace;text-transform:uppercase;letter-spacing:.05em}}
.status.refused b,.status.error b{{color:var(--refused)}} .status.unsupported b{{color:var(--unsupported)}}
.meta{{font:11px/1.4 "IBM Plex Mono",ui-monospace,monospace;color:var(--muted);margin-top:6px;font-variant-numeric:tabular-nums}}
.rate{{display:flex;gap:3px;margin-top:6px}}
button{{font:inherit;color:var(--fg);background:var(--surface);border:1px solid var(--line);border-radius:5px;cursor:pointer}}
button:focus-visible{{outline:2px solid var(--accent);outline-offset:1px}}
.rate button{{width:30px;height:26px;font:12px "IBM Plex Mono",ui-monospace,monospace}}
.rate button.on{{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}}
.bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}} .bar button{{padding:6px 12px}}
.bar output{{font-size:12px;color:var(--muted)}}
#csv{{width:100%;max-width:640px;height:120px;font:12px "IBM Plex Mono",ui-monospace,monospace;background:var(--surface);color:var(--fg);
border:1px solid var(--line);border-radius:6px}}
</style>
<div class="page">
<header>
<span class="eyebrow">Facemask · sticker benchmark · run {run_id}</span>
<h1>Classic face swap vs GPT-Image-2.5 Flare</h1>
<p>{n_selfies} selfies × {n_tpl} templates. Every output went through the same sticker step: background removal if needed, 512×512, white outline, WebP ≤100 KB.</p>
</header>
<section>
<h2>Per method</h2>
<div class="scroll"><table class="summary"><thead><tr><th>method</th><th>ok</th><th>refused</th><th>unsupported</th><th>errors</th>
<th>identity</th><th>photo</th><th>cartoon</th><th>p50</th><th>p95</th><th>per 1,000</th><th>size</th><th>your rating</th></tr></thead>
<tbody>{sum_rows}</tbody></table></div>
</section>
<section>
<p><b>identity</b> is the ArcFace cosine similarity between the selfie and the output face (≳0.35 usually reads as the same person).
inswapper is built on this same embedding, so it flatters the classic methods: they only replace the inner face and keep the template's
hair and head shape. Rate the stickers below for the real comparison. Classic cost (~) is local time × $0.80 per GPU-hour; Flare cost comes
from API token usage. Ratings are kept in this browser only.</p>
</section>
<section>
<h2>Stickers</h2>
<div class="bar"><button type="button" id="copy">Copy ratings as CSV</button><button type="button" id="clear">Clear ratings</button><output id="msg"></output></div>
<textarea id="csv" hidden readonly aria-label="ratings CSV"></textarea>
<div class="scroll" style="margin-top:10px"><table class="grid"><thead><tr><th>selfie × template</th>{''.join(f'<th>{html.escape(m)}</th>' for m in methods)}</tr></thead>
<tbody>{''.join(grid)}</tbody></table></div>
</section>
</div>
<script>
const KEY = "sticker-bench:{run_id}";
let ratings = {{}};
try {{ ratings = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
const msg = document.getElementById("msg"), csv = document.getElementById("csv"), clear = document.getElementById("clear");
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(ratings)); }} catch (e) {{}} }}
function paint() {{
  document.querySelectorAll(".rate").forEach(el => {{
    el.querySelectorAll("button").forEach(b => b.classList.toggle("on", ratings[el.dataset.key]?.v == b.dataset.v));
  }});
  const agg = {{}};
  Object.values(ratings).forEach(r => {{ (agg[r.m] ||= []).push(r.v); }});
  document.querySelectorAll("td.rating").forEach(td => {{
    const a = agg[td.dataset.method];
    td.textContent = a ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(2) + " (" + a.length + ")" : "—";
  }});
}}
document.addEventListener("click", e => {{
  const b = e.target.closest(".rate button"); if (!b) return;
  const el = b.parentElement, v = +b.dataset.v;
  if (ratings[el.dataset.key]?.v === v) delete ratings[el.dataset.key];
  else ratings[el.dataset.key] = {{ v, m: el.dataset.method }};
  save(); paint();
}});
document.getElementById("copy").onclick = () => {{
  const text = ["key,method,rating", ...Object.entries(ratings).map(([k, r]) => `${{k}},${{r.m}},${{r.v}}`)].join("\\n");
  const n = Object.keys(ratings).length;
  navigator.clipboard.writeText(text).then(
    () => {{ csv.hidden = true; msg.textContent = `Copied ${{n}} ratings.`; }},
    () => {{ csv.value = text; csv.hidden = false; csv.select(); msg.textContent = "Copy the selected text below."; }});
}};
let armed = false;
clear.onclick = () => {{
  if (!armed) {{ armed = true; clear.textContent = "Click again to clear all"; setTimeout(() => {{ armed = false; clear.textContent = "Clear ratings"; }}, 3000); return; }}
  armed = false; clear.textContent = "Clear ratings"; ratings = {{}}; save(); paint(); msg.textContent = "Ratings cleared.";
}};
paint();
</script>
"""
    (run_dir / "report.body.html").write_text(body)  # publishable fragment (no doctype/head)
    (run_dir / "report.html").write_text(
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>\n'
        f"<body style=\"margin:0\">\n{body}</body></html>\n"
    )

    print(f"\nreport: {run_dir / 'report.html'}")
    for s in summary:
        print(f"  {s['method']:<16} ok {s['ok']}/{s['jobs']}  id {fmt(s['identity_mean'])}  "
              f"face {fmt(s['face_found_pct'], '%', 1)}  p50 {fmt(s['latency_p50'], 's', 1)}  "
              f"$/1k {'~' if s['cost_is_estimate'] else ''}{fmt(s['cost_per_1000'], '', 2)}")


def cmd_report(args):
    write_report(Path(args.run_dir), Path(args.selfies), Path(args.templates).parent, raw_links=not args.no_raw_links)


# ---------------------------------------------------------------- cli


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--selfies", default=str(HERE / "selfies"))
    common.add_argument("--templates", default=str(HERE / "templates" / "templates.toml"))

    r = sub.add_parser("run", parents=[common], help="run the benchmark")
    r.add_argument("--methods", default="classic,classic_restore,flare:low,flare:medium",
                   help="comma list: classic, classic_restore, flare:<low|medium|high|xhigh|max>")
    r.add_argument("--run-dir", help="output dir (reuse an existing one to resume)")
    r.add_argument("--limit-selfies", type=int)
    r.add_argument("--limit-templates", type=int)
    r.add_argument("--inswapper", default=str(HERE / "models" / "inswapper_128.onnx"))
    r.add_argument("--gfpgan", default=str(HERE / "models" / "gfpgan_1.4.onnx"))
    r.add_argument("--rembg-model", default="isnet-general-use")
    r.add_argument("--flare-model", default="gpt-image-2.5-flare")
    r.add_argument("--flare-fidelity", default="none", choices=["none", "low", "high"],
                   help="input_fidelity (not supported by gpt-image-2.5-flare; for older models)")
    r.add_argument("--flare-size", default="1024x1024")
    r.add_argument("--flare-input-px", type=int, default=0,
                   help="downscale selfie/template to this long side before sending (384 cuts input tokens ~44%%)")
    r.add_argument("--concurrency", type=int, default=2, help="parallel Flare calls (mind your rate-limit tier)")
    r.add_argument("--gpu-usd-per-hour", type=float, default=0.80,
                   help="to project classic cost from latency (e.g. cloud L4 ≈ $0.80/h)")
    r.add_argument("--max-bytes", type=int, default=100_000, help="WhatsApp static sticker limit")
    r.add_argument("--dry-run", action="store_true", help="print the plan and cost estimate only")
    r.add_argument("-y", "--yes", action="store_true", help="don't ask before paid API calls")
    r.set_defaults(fn=cmd_run)

    rp = sub.add_parser("report", parents=[common], help="rebuild report.html from a run dir")
    rp.add_argument("run_dir")
    rp.add_argument("--no-raw-links", action="store_true",
                    help="don't link stickers to raw outputs (for publishing without raw/)")
    rp.set_defaults(fn=cmd_report)

    args = p.parse_args()
    load_env()
    args.fn(args)


if __name__ == "__main__":
    main()

# Face-sticker benchmark

Compares **GPT-Image-2.5 Flare** (OpenAI image edits API) with **classic face
swap** (InsightFace `inswapper_128`, optionally + GFPGAN 1.4 restoration) for
turning a selfie into stickers. Every output goes through the same sticker
post-processing the app will use: background removal if needed → trim → 512×512
→ white die-cut outline → WebP ≤100 KB (WhatsApp's static-sticker limit).

## Setup

```sh
cd bench
uv sync --python 3.12

# classic models (only needed for classic / classic_restore)
curl -L -o models/inswapper_128.onnx https://huggingface.co/facefusion/models-3.0.0/resolve/main/inswapper_128.onnx
curl -L -o models/gfpgan_1.4.onnx    https://huggingface.co/facefusion/models-3.0.0/resolve/main/gfpgan_1.4.onnx
```

The OpenAI key is read from `OPENAI_API_KEY`, or from `OPENAI_KEY` in the repo's
`.env`. insightface's `buffalo_l` and rembg's matting model download on first run.

> `inswapper_128` and `buffalo_l` are licensed for **non-commercial research
> only**. Fine for this internal evaluation; shipping them needs a commercial
> license from InsightFace.

## Inputs

- `selfies/` — one face per image. Use yourself or people who consented; never
  commit these (gitignored). Mix ages, skin tones, glasses/no glasses, lighting.
- `templates/templates.toml` + images in `templates/` — see the comments in that
  file. Templates **without** an image are prompt-only (e.g. cartoon styles);
  classic methods report them as `unsupported`.
- Don't use celebrity photos: Flare won't put someone's face onto a real public
  figure and just returns the template unchanged.

## Run

```sh
uv run python bench.py run --dry-run                 # plan + rough Flare cost
uv run python bench.py run                           # asks before paid calls
uv run python bench.py run --methods classic,flare:medium --limit-selfies 3 -y
uv run python bench.py run --run-dir runs/<name>     # resume (errors are retried)
uv run python bench.py report runs/<name>            # rebuild the HTML report
```

Methods: `classic`, `classic_restore`, `flare:<low|medium|high|xhigh|max>`.
Other flags: `--concurrency` (parallel Flare calls; tier-1 accounts get ~5
images/min), `--flare-model`, `--flare-size`, `--flare-input-px` (downscale
inputs before sending — input images are ~half of Flare's cost; 384 cut input
tokens ~44% in testing, 512 saves nothing), `--gpu-usd-per-hour` (projects
classic cost from local latency), `--rembg-model`.

`make_demo.py` generates fictional demo selfies and photo templates with Flare
(~$0.50) so the benchmark can run without real faces.

## Output (`runs/<timestamp>/`)

| File | |
|---|---|
| `report.html` | Summary table + contact sheet (selfie × template × method) with a 1–5 rating widget per sticker; ratings stay in your browser, "Export ratings CSV" saves them |
| `summary.json` | Per-method: success/refusal/unsupported counts, face-found %, identity mean/median (overall and per style), latency p50/p95, $/1000 stickers, sticker size |
| `results.jsonl` / `results.csv` | One row per job incl. token usage |
| `raw/`, `stickers/` | Raw model output and the final WebP sticker |

**identity** is the ArcFace cosine similarity between the selfie and the output
face. inswapper is conditioned on that same embedding, so **classic scores are
inflated**: it only swaps the inner face and keeps the template's hair, beard
and head shape. Use the human ratings for the final decision.

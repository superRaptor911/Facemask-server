#!/usr/bin/env python3
"""Generate demo inputs of fictional people with gpt-image-2.5-flare.

Writes selfies/demo_*.jpg and the photo templates referenced by
templates/templates.toml. Skips files that already exist. ~$0.04 per image.
"""

import base64
import sys
from concurrent.futures import ThreadPoolExecutor

from bench import HERE, load_env

SELFIE = (
    "Casual front-facing smartphone selfie of a fictional person: {who}. "
    "Head and shoulders, looking at the camera, natural indoor light, slightly imperfect phone photo. "
    "Photorealistic. Not a celebrity or real person."
)
TEMPLATE = (
    "Photo of a fictional {who}, {pose}. Waist-up, centered, plain light-grey studio background, "
    "sharp focus on the face. Photorealistic. Not a celebrity or real person."
)

SELFIES = {
    "demo_01": "South Asian woman, late 20s, curly shoulder-length black hair, round glasses",
    "demo_02": "Black man, early 30s, short fade haircut, full beard",
    "demo_03": "East Asian woman, early 20s, straight long hair with bangs",
    "demo_04": "white man, mid 50s, balding with grey stubble",
    "demo_05": "Latina woman, 40s, wavy brown hair tied back, hoop earrings",
    "demo_06": "Middle Eastern man, mid 20s, thick eyebrows, moustache, rectangular glasses",
    "demo_07": "Black woman, 60s, short grey natural hair, warm smile",
    "demo_08": "white teenager-looking young man, 19, freckles, messy red hair",
}
TEMPLATES = {
    "thumbs_up.jpg": ("middle-aged man with a grey beard in a green hoodie", "giving an enthusiastic thumbs up with a big grin"),
    "facepalm.jpg": ("young woman in a denim jacket", "doing a dramatic facepalm, exasperated"),
    "shocked.jpg": ("man in his 30s in a white t-shirt", "shocked face, mouth wide open, hands on cheeks"),
    "heart_hands.jpg": ("woman in her 40s in a yellow sweater", "making a heart shape with her hands, smiling warmly"),
}

EXTRA_TOML = """
[[template]]
id = "shocked"
image = "shocked.jpg"
style = "photo"
scene = "shocked face, mouth wide open, hands on cheeks"

[[template]]
id = "heart_hands"
image = "heart_hands.jpg"
style = "photo"
scene = "making a heart shape with their hands, smiling warmly"
"""


def main():
    load_env()
    from openai import OpenAI

    client = OpenAI(max_retries=6, timeout=300)
    jobs = [(HERE / "selfies" / f"{k}.jpg", SELFIE.format(who=v)) for k, v in SELFIES.items()]
    jobs += [(HERE / "templates" / f, TEMPLATE.format(who=w, pose=p)) for f, (w, p) in TEMPLATES.items()]
    jobs = [(p, prompt) for p, prompt in jobs if not p.exists()]
    if not jobs:
        print("all demo images already exist")
        return

    def gen(job):
        path, prompt = job
        r = client.images.generate(
            model="gpt-image-2.5-flare", prompt=prompt, size="1024x1024",
            quality="medium", output_format="jpeg",
        )
        path.write_bytes(base64.b64decode(r.data[0].b64_json))
        return path

    print(f"generating {len(jobs)} images ...")
    failed = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for job, fut in [(j, pool.submit(gen, j)) for j in jobs]:
            try:
                print("  wrote", fut.result().relative_to(HERE))
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ! {job[0].name}: {e}", file=sys.stderr)

    toml = HERE / "templates" / "templates.toml"
    if 'id = "shocked"' not in toml.read_text():
        toml.write_text(toml.read_text() + EXTRA_TOML)
        print("  added shocked + heart_hands to templates.toml")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

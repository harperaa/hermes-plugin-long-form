#!/usr/bin/env python3
"""Generate images via xAI Grok, or Google Gemini as the fallback provider.

Provider/auth resolution order (xAI always wins when present):
  1. XAI_API_KEY environment variable, if set.
  2. The hermes xai-oauth access token from ~/.hermes/auth.json
     (credential_pool["xai-oauth"][0]) — present after `hermes auth add xai-oauth`.
  3. ONLY if neither resolves: GEMINI_API_KEY from the environment or
     $HERMES_HOME/.env (set it on the dashboard Keys page). Uses the Gemini
     image model instead of Grok — same CLI, same QA gate.

Usage:
  python3 generate-image.py --prompt "..." --out /path/to/image.jpg \
      [--n 1] [--model grok-imagine-image] [--aspect-ratio 16:9]

Default model: grok-imagine-image. Pass --model grok-imagine-image-quality
for the higher-quality variant. The API returns JPEG bytes — prefer a .jpg
output extension. With --n > 1, outputs are suffixed -1, -2, ...
Non-2xx API responses are printed verbatim so the caller can see the real error.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1")
GEMINI_API_BASE = os.environ.get(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
DEFAULT_MODEL = "grok-imagine-image"
GEMINI_IMAGE_MODEL = os.environ.get("YTI_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_VERIFY_MODEL = os.environ.get("YTI_GEMINI_VERIFY_MODEL", "gemini-2.5-flash")


def _gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def resolve_provider() -> tuple[str, str]:
    """("xai", token) when any xAI credential resolves — xAI is ALWAYS
    preferred; ("gemini", key) only as the fallback when it doesn't."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return ("xai", key)
    auth_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "auth.json"
    try:
        store = json.loads(auth_path.read_text())
        pool = store.get("credential_pool", {}).get("xai-oauth") or []
        entry = pool[0] if pool else {}

        def find_access_token(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("access_token", "accessToken") and isinstance(v, str) and len(v) > 20:
                        return v
                    found = find_access_token(v)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = find_access_token(item)
                    if found:
                        return found
            return None

        token = find_access_token(entry)
        if token:
            return ("xai", token)
    except (OSError, json.JSONDecodeError):
        pass
    gem = _gemini_key()
    if gem:
        return ("gemini", gem)
    sys.exit("ERROR: no image-generation credential — connect xAI (set XAI_API_KEY "
             "or run `hermes auth add xai-oauth`), or set GEMINI_API_KEY on the "
             "dashboard Keys page (fallback provider).")


_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _image_ref(source: str) -> dict:
    """Build the API's image reference for --input: a URL passes through, a
    local file is inlined as a data URL."""
    if source.startswith(("http://", "https://", "data:")):
        return {"url": source, "type": "image_url"}
    path = Path(source)
    if not path.exists():
        sys.exit(f"ERROR: --input file not found: {path}")
    mime = _MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    encoded = base64.b64encode(path.read_bytes()).decode()
    return {"url": f"data:{mime};base64,{encoded}", "type": "image_url"}


def _input_bytes(source: str) -> tuple[bytes, str]:
    """Raw bytes + mime for --input (local path or URL) — Gemini inlines them."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "curl/8.4.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        mime = r.headers.get_content_type() or "image/png"
        return data, mime
    path = Path(source)
    if not path.exists():
        sys.exit(f"ERROR: --input file not found: {path}")
    return path.read_bytes(), _MIME_BY_EXT.get(path.suffix.lower(), "image/png")


def _gemini_call(key: str, model: str, parts: list, gen_config: dict | None = None) -> dict:
    body: dict = {"contents": [{"parts": parts}]}
    if gen_config:
        body["generationConfig"] = gen_config
    req = urllib.request.Request(
        f"{GEMINI_API_BASE}/models/{model}:generateContent",
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json",
                 "User-Agent": "curl/8.4.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"ERROR: Gemini API returned HTTP {exc.code}:\n{detail}")


def _gemini_image_parts(payload: dict) -> list[bytes]:
    out = []
    for cand in payload.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data") or {}
            if blob.get("data"):
                out.append(base64.b64decode(blob["data"]))
    return out


def _generate_gemini(key: str, prompt: str, out: Path, n: int,
                     aspect_ratio: str | None,
                     input_image: str | None) -> list[Path]:
    parts: list = []
    if input_image:
        data, mime = _input_bytes(input_image)
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.b64encode(data).decode()}})
    parts.append({"text": prompt})
    gen_config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
    if aspect_ratio:
        gen_config["imageConfig"] = {"aspectRatio": aspect_ratio}
    out.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i in range(max(1, n)):
        payload = _gemini_call(key, GEMINI_IMAGE_MODEL, parts, gen_config)
        images = _gemini_image_parts(payload)
        if not images:
            sys.exit(f"ERROR: no image in Gemini response:\n{json.dumps(payload)[:2000]}")
        target = out if n <= 1 else out.with_name(f"{out.stem}-{i + 1}{out.suffix}")
        target.write_bytes(images[0])
        written.append(target)
    return written


def generate(prompt: str, out: Path, n: int, model: str,
             aspect_ratio: str | None = None,
             input_image: str | None = None) -> list[Path]:
    provider, token = resolve_provider()
    if provider == "gemini":
        return _generate_gemini(token, prompt, out, n, aspect_ratio, input_image)
    payload_body: dict = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "response_format": "b64_json",
    }
    if aspect_ratio:
        payload_body["aspect_ratio"] = aspect_ratio
    # With a starting image, use the edits endpoint (image-to-image): the
    # source anchors composition/style and the prompt directs the transform.
    endpoint = "images/generations"
    if input_image:
        payload_body["image"] = _image_ref(input_image)
        endpoint = "images/edits"
    body = json.dumps(payload_body).encode()
    req = urllib.request.Request(
        f"{API_BASE}/{endpoint}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.4.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"ERROR: xAI images API returned HTTP {exc.code}:\n{detail}")

    images = payload.get("data") or []
    if not images:
        sys.exit(f"ERROR: no images in response:\n{json.dumps(payload)[:2000]}")

    out.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, img in enumerate(images):
        if len(images) == 1:
            target = out
        else:
            target = out.with_name(f"{out.stem}-{i + 1}{out.suffix}")
        if img.get("b64_json"):
            target.write_bytes(base64.b64decode(img["b64_json"]))
        elif img.get("url"):
            dl = urllib.request.Request(img["url"], headers={"User-Agent": "curl/8.4.0"})
            with urllib.request.urlopen(dl, timeout=300) as r:
                target.write_bytes(r.read())
        else:
            sys.exit(f"ERROR: image entry has neither b64_json nor url: {json.dumps(img)[:500]}")
        written.append(target)
        if img.get("revised_prompt"):
            print(f"revised prompt [{i + 1}]: {img['revised_prompt'][:200]}")
    return written


VERIFY_MODEL = os.environ.get("YTI_VERIFY_MODEL", "grok-4.5")


def verify_image(path: Path, prompt: str, expect_text: str | None) -> dict:
    """Vision-QA a generated image: transcribe rendered text, flag misspellings
    and visual defects. Returns {"pass": bool, "issues": [...], "text": "..."}."""
    provider, token = resolve_provider()
    mime = _MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg")
    data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    checklist = (
        "You are a strict pre-publication QA reviewer for generated images. "
        "1) Transcribe EVERY piece of rendered text in the image exactly as drawn, "
        "including partial or garbled words. Do NOT trust your first reading of any "
        "word — vision models autocorrect misspellings while transcribing, which is "
        "the worst possible QA failure here. Read every word CHARACTER BY CHARACTER "
        "as physically drawn (missing, doubled, swapped, or wrong letters included) "
        "before writing it down. 2) Flag ANY misspelling, duplicated or "
        "missing letters, garbled/pseudo-text, or nonsense glyphs — in ANY rendered "
        "text, not only expected labels (margin notes and annotations count). 3) Flag visual "
        "defects: extra limbs/fingers, broken arrows, cut-off elements, illegible "
        "labels, watermark-like artifacts. "
        f"The image was generated from this prompt: {prompt!r}. "
        + (f"The following text labels MUST appear spelled exactly: {expect_text}. "
           "CRITICAL: do NOT trust your first reading — vision models "
           "autocorrect misspellings while transcribing, which is the worst "
           "possible QA failure. For EACH expected label, read the rendered "
           "word CHARACTER BY CHARACTER as physically drawn (including "
           "missing, doubled, or swapped letters), write out that "
           "letter-by-letter reading in \"text\", and compare it to the "
           "expected spelling before deciding pass/fail. "
           if expect_text else "")
        + 'Reply with ONLY a JSON object: {"pass": true|false, "text": "<all transcribed text>", '
          '"issues": ["<each problem found>"]}. Fail on ANY spelling error or garbled text.'
    )
    try:
        if provider == "gemini":
            payload = _gemini_call(token, GEMINI_VERIFY_MODEL, [
                {"inline_data": {"mime_type": mime,
                                 "data": base64.b64encode(path.read_bytes()).decode()}},
                {"text": checklist},
            ], {"temperature": 0})
            content = "".join(
                part.get("text", "")
                for cand in (payload.get("candidates") or [])
                for part in ((cand.get("content") or {}).get("parts") or []))
        else:
            body = json.dumps({
                "model": VERIFY_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": checklist},
                ]}],
                "temperature": 0,
            }).encode()
            req = urllib.request.Request(
                f"{API_BASE}/chat/completions", data=body,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json",
                         "User-Agent": "curl/8.4.0"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode())
            content = payload["choices"][0]["message"]["content"]
        match = content[content.index("{"):content.rindex("}") + 1]
        return json.loads(match)
    except Exception as exc:  # noqa: BLE001 — verification must not crash generation
        return {"pass": False, "text": "", "issues": [f"verifier error: {exc}"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--aspect-ratio", default=None,
                    help="e.g. 16:9 — verified supported by the xAI API")
    ap.add_argument("--input", default=None, dest="input_image",
                    help="Starting image (local path or URL) — routes to the "
                         "images/edits endpoint for image-to-image; use the "
                         "selected style baseline (see baselines/) to anchor "
                         "the sketchnote style")
    ap.add_argument("--expect-text", default=None,
                    help="Comma-separated labels that must appear spelled exactly")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the vision QA gate (NOT recommended)")
    ap.add_argument("--retries", type=int, default=2,
                    help="Regeneration attempts when QA fails (default 2)")
    args = ap.parse_args()

    # Never clobber previous outputs: if the name carries no -YYYYMMDD-HHMM
    # stamp, add one; if the path still exists, bump a numeric suffix.
    out: Path = args.out
    if not re.search(r"-\d{8}-\d{4}(-\d+)?$", out.stem):
        stamp = time.strftime("%Y%m%d-%H%M")
        out = out.with_name(f"{out.stem}-{stamp}{out.suffix}")
    bump = 1
    while out.exists():
        bump += 1
        base = re.sub(r"-\d+$", "", out.stem) if bump > 2 else out.stem
        out = out.with_name(f"{base}-{bump}{out.suffix}")
    if out != args.out:
        print(f"output path adjusted to avoid overwrite: {out}")

    attempt = 0
    prompt = args.prompt
    while True:
        written = generate(prompt, out, args.n, args.model,
                           aspect_ratio=args.aspect_ratio,
                           input_image=args.input_image)
        if args.no_verify:
            for path in written:
                print(f"saved: {path} (verification skipped)")
            return
        failures: list[tuple[Path, dict]] = []
        for path in written:
            verdict = verify_image(path, args.prompt, args.expect_text)
            if verdict.get("pass"):
                print(f"saved: {path} (QA pass; text: {verdict.get('text', '')[:120]!r})")
            else:
                failures.append((path, verdict))
        if not failures:
            return
        for path, verdict in failures:
            print(f"QA FAIL: {path}: {'; '.join(verdict.get('issues', []))[:400]}")
        attempt += 1
        if attempt > args.retries:
            sys.exit(f"ERROR: image failed QA after {args.retries + 1} attempts — "
                     "fix the prompt (spell critical labels letter-by-letter) and rerun.")
        fixes = "; ".join(i for _, v in failures for i in v.get("issues", []))[:500]
        spell = ""
        if args.expect_text:
            spelled = ", ".join("-".join(w.strip().upper()) for w in args.expect_text.split(","))
            spell = f" Spell these labels exactly, letter by letter: {spelled}."
        prompt = (f"{args.prompt} CRITICAL: previous attempt had these defects, avoid them: "
                  f"{fixes}. Render all text perfectly spelled and fully legible.{spell}")
        print(f"retrying ({attempt}/{args.retries}) with corrective prompt...")


if __name__ == "__main__":
    main()

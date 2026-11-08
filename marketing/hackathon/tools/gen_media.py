"""Generate the film's art, clips, 3D models and music on inference.club (club.lan).

    python3 marketing/hackathon/tools/gen_media.py [--only id,id] [--force]

Reads media.json (committed: every prompt, seed and reference), writes assets/gen/<id>.<ext>
(gitignored) plus <id>.meta.json with the request and the request id the site returned, so any
piece can be traced back to its generation on club.lan. A piece is skipped when its spec is
unchanged since the last run. Token: ~/.config/inference-club/club-lan.token (or CLUB_TOKEN).

Kinds:
  edit   FLUX 3 klein image edit: `refs` (local paths or "gen:<id>") + prompt -> PNG.
         Used for style transfer: the Goltzius plate as "image 1" sets the engraving style.
  gen    text-to-image -> PNG.
  video  LTX-2.5 image-to-video from `image` ("gen:<id>" or a path) -> MP4.
  mesh   TRELLIS-2 image-to-3D from `image` -> GLB.
  music  ACE-Step instrumental -> WAV.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "gen"
BASE = os.environ.get("CLUB_URL", "https://club.lan")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # club.lan uses the LAN mkcert CA


def token() -> str:
    t = os.environ.get("CLUB_TOKEN")
    if t:
        return t.strip()
    return (Path.home() / ".config/inference-club/club-lan.token").read_text().strip()


def request(path: str, *, body: bytes | None = None, ctype: str | None = None, timeout=1800):
    req = urllib.request.Request(BASE + path if path.startswith("/") else path, data=body)
    req.add_header("Authorization", f"Bearer {token()}")
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def multipart(fields: dict, files: list[tuple[str, str, bytes, str]]):
    b = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fname, data, ct in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {ct}\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def resolve(ref: str) -> Path:
    if ref.startswith("gen:"):
        hits = sorted(OUT.glob(ref[4:] + ".*"))
        hits = [h for h in hits if not h.name.endswith(".meta.json")]
        if not hits:
            sys.exit(f"{ref} not generated yet (order media.json so it comes first)")
        return hits[0]
    return (ROOT / ref).resolve()


def img_bytes(p: Path, max_side=1536) -> tuple[bytes, str]:
    """JPEG bytes of an image, scaled down so uploads stay small (sips, macOS)."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        o = Path(td) / "ref.jpg"
        subprocess.run(["sips", "-Z", str(max_side), "-s", "format", "jpeg", str(p), "--out", str(o)],
                       capture_output=True, check=True)
        return o.read_bytes(), "image/jpeg"


def first_url(payload: dict) -> str | None:
    for key in ("data", "output", "result"):
        v = payload.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("url"):
            return v[0]["url"]
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
    for key in ("url", "model_url", "video_url", "audio_url", "glb_url"):
        if payload.get(key):
            return payload[key]
    return None


def fetch_result(raw: bytes, ctype: str) -> tuple[bytes, dict]:
    if "json" not in ctype:
        return raw, {}
    payload = json.loads(raw)
    url = first_url(payload)
    if not url:
        raise RuntimeError(f"no url in response: {str(payload)[:400]}")
    data, _ = request(url)
    return data, payload


def run(item: dict) -> tuple[bytes, str, dict]:
    kind = item["kind"]
    if kind in ("edit", "gen"):
        fields = {"model": item.get("model", "flux-3-image-klein-fp8"), "prompt": item["prompt"],
                  "size": item.get("size", "1536x864"), "seed": str(item.get("seed", 7))}
        if kind == "edit":
            files = []
            for r in item["refs"]:
                data, ct = img_bytes(resolve(r))
                files.append(("image[]" if len(item["refs"]) > 1 else "image", Path(r).name + ".jpg", data, ct))
            body, ct = multipart(fields, files)
            raw, rct = request("/v1/images/edits", body=body, ctype=ct)
        else:
            raw, rct = request("/v1/images/generations", body=json.dumps(fields).encode(),
                               ctype="application/json")
        data, payload = fetch_result(raw, rct)
        return data, "png", payload
    if kind == "video":
        data, _ = img_bytes(resolve(item["image"]), 1280)
        body = {"model": item.get("model", "ltx-2.5"), "prompt": item["prompt"],
                "image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
                "duration": item.get("duration", 5), "width": item.get("width", 1280),
                "height": item.get("height", 704), "seed": item.get("seed", 7)}
        for k in ("negative_prompt", "image_strength", "fps", "num_inference_steps"):
            if k in item:
                body[k] = item[k]
        raw, rct = request("/v1/videos/generations", body=json.dumps(body).encode(),
                           ctype="application/json", timeout=3600)
        data, payload = fetch_result(raw, rct)
        return data, "mp4", payload
    if kind == "mesh":
        data, ct = img_bytes(resolve(item["image"]), 1024)
        fields = {"model": item.get("model", "trellis-2")}
        if item.get("options"):
            fields["options"] = json.dumps(item["options"])
        body, mct = multipart(fields, [("image", "in.jpg", data, ct)])
        raw, rct = request("/v1/3d/generations", body=body, ctype=mct, timeout=3600)
        data, payload = fetch_result(raw, rct)
        return data, "glb", payload
    if kind == "music":
        body = {"model": item.get("model", "acestep-v15-turbo"), "prompt": item["prompt"],
                "lyrics": item.get("lyrics", "[inst]"), "audio_duration": item["duration"],
                "audio_format": "wav", "seed": item.get("seed", 7), "use_random_seed": False}
        for k in ("bpm", "key_scale", "inference_steps", "guidance_scale"):
            if k in item:
                body[k] = item[k]
        raw, rct = request("/v1/music/generations", body=json.dumps(body).encode(),
                           ctype="application/json", timeout=3600)
        data, payload = fetch_result(raw, rct)
        return data, "wav", payload
    raise SystemExit(f"unknown kind {kind}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    only = {x for x in a.only.split(",") if x}
    OUT.mkdir(parents=True, exist_ok=True)
    items = json.loads((ROOT / "media.json").read_text())["items"]
    for item in items:
        if only and item["id"] not in only:
            continue
        spec = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16]
        meta_p = OUT / f"{item['id']}.meta.json"
        if not a.force and meta_p.exists() and json.loads(meta_p.read_text()).get("spec") == spec:
            print(f"  = {item['id']}")
            continue
        t0 = time.time()
        try:
            data, ext, payload = run(item)
        except Exception as e:  # keep going; report at the end
            print(f"  ! {item['id']}: {e}")
            continue
        for old in OUT.glob(item["id"] + ".*"):
            if not old.name.endswith(".meta.json"):
                old.unlink()
        (OUT / f"{item['id']}.{ext}").write_bytes(data)
        meta_p.write_text(json.dumps({"spec": spec, "item": item,
                                      "request_id": payload.get("request_id") or payload.get("id"),
                                      "seconds": round(time.time() - t0, 1)}, indent=2))
        print(f"  + {item['id']}.{ext}  {len(data) // 1024} KB  {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

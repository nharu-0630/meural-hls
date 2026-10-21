"""Meural Canvas MC321 local API client (network-only, no cloud).

Endpoints reverse-engineered from the on-device "Meural Canvas Remote" web UI
(remote.js) plus community implementations (GuySie/ha-meural).
"""
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request

HOST = os.environ.get("MEURAL_HOST", "192.0.2.10")
BASE = f"http://{HOST}"


def _req(path: str, data: bytes | None = None, timeout: float = 30.0,
         headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(BASE + path, data=data)
    if headers:
        req.headers.update(headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _req_json(path: str, data: bytes | None = None, timeout: float = 30.0,
              headers: dict[str, str] | None = None) -> dict:
    return json.loads(_req(path, data, timeout, headers))


# ---- read ----------------------------------------------------------------

def identify() -> dict:
    return _req_json("/remote/identify/")["response"]


def system() -> dict:
    return _req_json("/remote/control_check/system/")["response"]


def sleep_status() -> bool:
    return _req_json("/remote/control_check/sleep/")["response"]


def video_status() -> bool:
    return _req_json("/remote/control_check/video/")["response"]


def galleries() -> list[dict]:
    return _req_json("/remote/get_galleries_json/")["response"]


def gallery_status() -> dict:
    return _req_json("/remote/get_gallery_status_json/")["response"]


def frame_items(gallery_id: int | str) -> list[dict]:
    return _req_json(f"/remote/get_frame_items_by_gallery_json/{gallery_id}/")["response"]


def backlight() -> int:
    return int(_req_json("/remote/get_backlight/")["response"])


# ---- command (GET style) ---------------------------------------------------

def suspend() -> dict:
    return _req_json("/remote/control_command/suspend")


def resume() -> dict:
    return _req_json("/remote/control_command/resume")


def set_backlight(level: int) -> dict:
    return _req_json(f"/remote/control_command/set_backlight/{int(level)}/")


def set_orientation(orient: str) -> dict:
    assert orient in ("portrait", "landscape")
    return _req_json(f"/remote/control_command/set_orientation/{orient}")


def change_gallery(gallery_id: int | str) -> dict:
    return _req_json(f"/remote/control_command/change_gallery/{gallery_id}")


def change_item(item_id: int | str) -> dict:
    return _req_json(f"/remote/control_command/change_item/{item_id}")


def gesture(key: str) -> dict:
    assert key in ("left", "right", "up", "down", "f", "s")
    return _req_json(f"/remote/control_command/set_key/{key}/")


# ---- media ----------------------------------------------------------------

def postcard(filepath: str | os.PathLike, content_type: str | None = None,
             field: str = "photo", filename: str | None = None):
    """Upload a file (image OR video) to display immediately via /remote/postcard/.

    Returns (status_text, elapsed_seconds).
    """
    filepath = str(filepath)
    filename = filename or os.path.basename(filepath)
    if content_type is None:
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        content_type = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
            "mp4": "video/mp4", "mov": "video/quicktime",
        }.get(ext, "application/octet-stream")

    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    with open(filepath, "rb") as f:
        body += f.read()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
               "Connection": "close"}
    t0 = time.time()
    resp = _req_json("/remote/postcard/", body, timeout=300, headers=headers)
    if resp.get("status") != "pass":
        raise RuntimeError(f"postcard failed: {resp}")
    return resp, time.time() - t0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "post" and len(sys.argv) > 2:
        r, dt = postcard(sys.argv[2])
        print(r, f"{dt:.2f}s")
    else:
        print(json.dumps({"identify": identify(), "system": system()}, indent=2, ensure_ascii=False))

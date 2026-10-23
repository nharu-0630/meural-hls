"""Meural Canvas MC321 local API client (network-only, no cloud).

Endpoints reverse-engineered from the on-device "Meural Canvas Remote" web UI
(remote.js) plus community implementations (GuySie/ha-meural).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid

HOST = os.environ.get("MEURAL_HOST", "192.0.2.10")
BASE = f"http://{HOST}"


def _req(
    path: str,
    data: bytes | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> bytes:
    req = urllib.request.Request(BASE + path, data=data)
    if headers:
        req.headers.update(headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return bytes(resp.read())


def _req_json(
    path: str,
    data: bytes | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    return _object(json.loads(_req(path, data, timeout, headers)))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return [_object(item) for item in value]


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Expected a JSON boolean")
    return value


# ---- read ----------------------------------------------------------------


def identify() -> dict[str, object]:
    return _object(_req_json("/remote/identify/")["response"])


def system() -> dict[str, object]:
    return _object(_req_json("/remote/control_check/system/")["response"])


def sleep_status() -> bool:
    return _boolean(_req_json("/remote/control_check/sleep/")["response"])


def video_status() -> bool:
    return _boolean(_req_json("/remote/control_check/video/")["response"])


def galleries() -> list[dict[str, object]]:
    return _objects(_req_json("/remote/get_galleries_json/")["response"])


def gallery_status() -> dict[str, object]:
    return _object(_req_json("/remote/get_gallery_status_json/")["response"])


def frame_items(gallery_id: int | str) -> list[dict[str, object]]:
    return _objects(
        _req_json(f"/remote/get_frame_items_by_gallery_json/{gallery_id}/")["response"]
    )


def backlight() -> int:
    value = _req_json("/remote/get_backlight/")["response"]
    if not isinstance(value, (int, str)):
        raise ValueError("Expected backlight as an integer or numeric string")
    return int(value)


# ---- command (GET style) ---------------------------------------------------


def suspend() -> dict[str, object]:
    return _req_json("/remote/control_command/suspend")


def resume() -> dict[str, object]:
    return _req_json("/remote/control_command/resume")


def set_backlight(level: int) -> dict[str, object]:
    return _req_json(f"/remote/control_command/set_backlight/{int(level)}/")


def set_orientation(orient: str) -> dict[str, object]:
    if orient not in ("portrait", "landscape"):
        raise ValueError("orientation must be portrait or landscape")
    return _req_json(f"/remote/control_command/set_orientation/{orient}")


def change_gallery(gallery_id: int | str) -> dict[str, object]:
    return _req_json(f"/remote/control_command/change_gallery/{gallery_id}")


def change_item(item_id: int | str) -> dict[str, object]:
    return _req_json(f"/remote/control_command/change_item/{item_id}")


def gesture(key: str) -> dict[str, object]:
    if key not in ("left", "right", "up", "down", "f", "s"):
        raise ValueError("invalid gesture")
    return _req_json(f"/remote/control_command/set_key/{key}/")


# ---- media ----------------------------------------------------------------


def postcard(
    filepath: str | os.PathLike,
    content_type: str | None = None,
    field: str = "photo",
    filename: str | None = None,
) -> tuple[dict[str, object], float]:
    """Upload a file (image OR video) to display immediately via /remote/postcard/.

    Returns (status_text, elapsed_seconds).
    """
    filepath = str(filepath)
    filename = filename or os.path.basename(filepath)
    if content_type is None:
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        content_type = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "bmp": "image/bmp",
            "webp": "image/webp",
            "mp4": "video/mp4",
            "mov": "video/quicktime",
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

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Connection": "close",
    }
    t0 = time.monotonic()
    resp = _req_json("/remote/postcard/", body, timeout=300, headers=headers)
    if resp.get("status") != "pass":
        raise RuntimeError(f"postcard failed: {resp}")
    return resp, time.monotonic() - t0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "post" and len(sys.argv) > 2:
        r, dt = postcard(sys.argv[2])
        print(r, f"{dt:.2f}s")
    else:
        print(
            json.dumps(
                {"identify": identify(), "system": system()},
                indent=2,
                ensure_ascii=False,
            )
        )

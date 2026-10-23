#!/usr/bin/env python3
"""Encode closed MPEG-TS segments and push them to the RAM-only device server."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path

DEV = os.environ.get("MEURAL_HOST", "192.0.2.10")
PORT = 8484
HLS_URL = os.environ.get("HLS_URL")
COPY_SOURCE = os.environ.get("HLS_COPY") == "1"
MAX_PENDING_SEGMENTS = 30
SEGMENT_NAME = re.compile(r"seg(\d+)\.ts")


def build_ffmpeg(
    work: Path, source: str | None = HLS_URL, copy: bool = COPY_SOURCE
) -> list[str]:
    if copy and not source:
        raise ValueError("HLS_COPY=1 requires HLS_URL")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    if source:
        args += ["-re", "-i", source]
        if not copy:
            args += ["-vf", "scale=1920:1080,fps=15,format=yuv420p"]
    else:
        args += [
            "-re",
            "-f",
            "lavfi",
            "-i",
            "gradients=s=1920x1080:r=15:speed=0.15:nb_colors=4:"
            "c0=0x14145a:c1=0x33cc66:c2=0x8844aa:c3=0x2244cc,"
            "format=yuv420p,"
            "drawbox=x=0:y=0:w=1920:h=1080:color=white@0.85:t=6,",
        ]
    args += ["-map", "0:v:0"]
    if copy:
        args += ["-c:v", "copy"]
    else:
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "2000k",
            "-g",
            "30",
        ]
    return args + [
        "-an",
        "-f",
        "segment",
        "-segment_time",
        "2",
        "-segment_format",
        "mpegts",
        "-reset_timestamps",
        "0",
        str(work / "seg%06d.ts"),
    ]


def post(url: str, data: bytes, tries: int = 6) -> bool:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
            return True
        except (OSError, urllib.error.URLError) as error:
            print("post fail", attempt + 1, repr(error)[:120], flush=True)
            if attempt + 1 < tries:
                time.sleep(1)
    return False


def closed_segments(work: Path, pushed: int) -> list[tuple[int, Path]]:
    segments = []
    for path in work.iterdir():
        match = SEGMENT_NAME.fullmatch(path.name)
        if match and path.is_file() and int(match[1]) > pushed:
            segments.append((int(match[1]), path))
    segments.sort()
    return segments[:-1]  # The newest segment may still be open in ffmpeg.


def playlist(sequences: list[int]) -> bytes:
    if not sequences:
        return b"#EXTM3U\n"
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:2",
        f"#EXT-X-MEDIA-SEQUENCE:{sequences[0]}",
    ]
    previous = sequences[0] - 1
    for sequence in sequences:
        if sequence != previous + 1:
            lines.append("#EXT-X-DISCONTINUITY")
        lines += ["#EXTINF:2.0,", f"s{sequence}.ts"]
        previous = sequence
    return ("\n".join(lines) + "\n").encode()


def push_loop(
    proc: subprocess.Popen[bytes], work: Path, base_url: str, session: str
) -> None:
    pushed = -1
    confirmed: deque[int] = deque(maxlen=2)
    published: bytes | None = None
    while True:
        code = proc.poll()
        if code is not None:
            raise RuntimeError(f"ffmpeg exited with code {code}")
        ready = closed_segments(work, pushed)
        if len(ready) > MAX_PENDING_SEGMENTS:
            raise RuntimeError("Device is too slow: more than 60s of unsent video")
        for sequence, path in ready:
            if path.stat().st_size < 4096:
                if sequence < ready[-1][0]:
                    path.unlink()
                    pushed = sequence
                    continue
                break
            if not post(
                f"{base_url}/seg?n={sequence}&session={session}", path.read_bytes()
            ):
                # Preserve order: retry this segment next pass before anything later.
                break
            print("pushed seg", sequence, path.stat().st_size, "bytes", flush=True)
            confirmed.append(sequence)
            path.unlink()
            pushed = sequence
        current = playlist(list(confirmed))
        if confirmed and current != published and post(f"{base_url}/pl", current):
            published = current
        time.sleep(0.5)


def main() -> None:
    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    # Each run owns its directory; never erase a shared /tmp/hlslive directory.
    with tempfile.TemporaryDirectory(prefix="meural-hls-") as directory:
        work = Path(directory)
        proc = subprocess.Popen(build_ffmpeg(work), stdout=subprocess.DEVNULL)
        print("ffmpeg pid", proc.pid, flush=True)
        try:
            push_loop(proc, work, f"http://{DEV}:{PORT}", uuid.uuid4().hex)
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()

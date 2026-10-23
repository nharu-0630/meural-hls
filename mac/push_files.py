#!/usr/bin/env python3
"""Mac→device ファイル転送 (rtc/base64チャンク経由).

- base64はURLSAFE(-_)なので payload 内に '/' が出ない
- 全ての $ は \$ にエスケープして L1 展開を回避し、eval(=hw.shレベル)で展開させる
"""
import base64, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtc_shell2 import rtc

ESC = "\\$"

def P(code):                      # \$ (printf '\ooo')
    return ESC + "(printf '" + code + "')"

def sl(*parts):                   # /path/like/this
    return "".join(P("\\057") + x for x in parts)

def co(*parts):
    return "".join(P("\\072") + x for x in parts)

DECODE_CODE = ("import base64,sys;fd=open(sys.argv[2],chr(119)+chr(98));"
               "fd.write(base64.urlsafe_b64decode(open(sys.argv[1]).read()))")

def push_file(local_path, remote_path_str, chunk=2000, verbose=True):
    """remote_path_str は sl()で組み立て済みの文字列."""
    data = base64.urlsafe_b64encode(open(local_path, "rb").read()).decode()
    chunks = [data[i:i + chunk] for i in range(0, len(data), chunk)]
    tmp = sl("tmp", "push_" + base64.urlsafe_b64encode(local_path.encode()).decode()[-8:] + ".b64")
    r, _ = rtc(";rm -f " + tmp)
    n = 0
    for c in chunks:
        lead = "-- " if c.startswith("-") else ""
        r, dt = rtc(";printf " + lead + c + " >>" + tmp)
        if dt < 0.3:
            print(f"  warn: chunk {n} fast ({dt:.2f}s) {r[:20]!r}", file=sys.stderr)
        n += 1
    r, dt = rtc(";python3 -c '" + DECODE_CODE + "' " + tmp + " " + remote_path_str + ";sleep 1")
    if verbose:
        print(f"pushed {local_path} -> {remote_path_str} ({n} chunks, decode {dt:.1f}s) {r[:30]!r}")

if __name__ == "__main__":
    # 1) /dev/shm/hls 作成とクリア
    H = sl("dev", "shm", "hls")
    r, _ = rtc(";rm -r " + H + " 2>&1; mkdir -p " + H + "; sleep 1")
    print("mkdir", r[:30])

    # 2) push m3u8 + ts
    push_file("/tmp/hlsvod/pl.m3u8", H + sl("pl.m3u8"))
    push_file("/tmp/hlsvod/seg0.ts", H + sl("seg0.ts"))
    push_file("/tmp/hlsvod/seg1.ts", H + sl("seg1.ts"))

    # 3) CWD=/dev/shm/hls で http.server 19082 を常駐
    p = (";cd " + H + ";setsid nohup python3 -m http.server 19082 >"
         + sl("tmp", "hls-serve.log") + " 2>&1 & sleep 2")
    r, dt = rtc(p); print("serve", dt, r[:30])

#!/usr/bin/env python3
"""Mac → device HLS pusher.

ffmpeg で MPEG-TS セグメントを連続生成し、デバイス内 ingest server (8484) に
POST する。セグメントは全て端末のメモリ (python バッファ) のみに置かれる。
"""
import os, re, subprocess, sys, time, urllib.request

DEV = "192.0.2.10"
PORT = 8484
WORK = "/tmp/hlslive"

HLS_URL = os.environ.get("HLS_URL")  # 例: 実HLS (m3u8) に差し替えるとき設定。未設定=内蔵デモソース

def build_ffmpeg():
    ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    if HLS_URL:
        # 実HLSソース: Mac側でフェッチ→リエンコード。-re でVODソースも実時間ペース。
        ffmpeg += [
            "-re", "-i", HLS_URL,
            "-vf", "scale=1920:1080,fps=15,format=yuv420p",
        ]
    else:
        # 内蔵デモソース。このffmpeg build (9.0.2) は drawbox のタイムスタンプ変数 t が
        # 常に0になり式ベースの移動が不能なため、設計上アニメーションする gradients を
        # ベースにする。drawbox は静的装飾のみ。
        ffmpeg += [
            "-re", "-f", "lavfi", "-i",
            "gradients=s=1920x1080:r=15:speed=0.15:nb_colors=4:"
            "c0=0x14145a:c1=0x33cc66:c2=0x8844aa:c3=0x2244cc,"
            "format=yuv420p,"
            "drawbox=x=0:y=0:w=1920:h=1080:color=white@0.85:t=6,",
        ]
    ffmpeg += [
        "-c:v", "libx264", "-preset", "superfast",
        "-pix_fmt", "yuv420p", "-b:v", "2000k", "-g", "30",
        "-an",
        "-f", "segment", "-segment_time", "2", "-segment_format", "mpegts",
        "-reset_timestamps", "0", WORK + "/seg%06d.ts",
    ]
    return ffmpeg

FFMPEG = build_ffmpeg()

def post(url, data, tries=6):
    # サーバ再起動等で一時的に接続できない場合も死なない (liberal retry)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            return True
        except Exception as e:
            print("post fail", i, repr(e)[:120])
            time.sleep(1)
    return False

def main():
    os.makedirs(WORK, exist_ok=True)
    for f in os.listdir(WORK):
        os.unlink(os.path.join(WORK, f))
    proc = subprocess.Popen(FFMPEG, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("ffmpeg pid", proc.pid)
    pushed = -1
    try:
        while True:
            names = sorted(f for f in os.listdir(WORK) if f.startswith("seg") and f.endswith(".ts"))
            seqs = [int(re.search(r"(\d+)", f).group(1)) for f in names]
            if seqs:
                latest = seqs[-1]
                # 最新の1個は ffmpeg がまだ書き込み中の可能性 → 除外
                for n in range(pushed + 1, latest):
                    p = f"{WORK}/seg{n:06d}.ts"
                    if not os.path.exists(p):
                        continue
                    if os.path.getsize(p) < 4096:
                        # 実体が書かれていない空ファイル。後続に健全なセグメントが出ていれば
                        # 失われた内容とみなして諦めて進む (詰まり防止)
                        nxt = f"{WORK}/seg{n+1:06d}.ts"
                        if n + 1 < latest and os.path.exists(nxt) and os.path.getsize(nxt) > 4096:
                            os.unlink(p)
                            pushed = n
                        continue
                    data = open(p, "rb").read()
                    if post(f"http://{DEV}:{PORT}/seg?n={n}", data):
                        print("pushed seg", n, len(data), "bytes")
                        os.unlink(p)
                        pushed = n
                # playlist: 直近2セグメントを提示 (端末側バッファから配信)
                if pushed >= 0:
                    first, last = max(0, pushed - 1), pushed
                    pl = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2",
                          f"#EXT-X-MEDIA-SEQUENCE:{first}"]
                    for n in range(first, last + 1):
                        pl += ["#EXTINF:2.0,", f"s{n}.ts"]
                    post(f"http://{DEV}:{PORT}/pl", ("\n".join(pl) + "\n").encode())
            time.sleep(0.5)
    except Exception as e:
        print("loop error:", repr(e)[:200])
        try:
            proc.terminate()
        except Exception:
            pass
        print("stopped")

if __name__ == "__main__":
    main()

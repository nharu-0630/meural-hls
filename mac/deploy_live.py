#!/usr/bin/env python3
"""Deploy the device-side live components into RAM and start them.

Steps
  1. Push device/ingest.py and device/watch.sh into the device's /tmp (RAM).
  2. Stop any previous ingest server / watchdog / mplayer.
  3. Start the ingest server (port 8484), then the mplayer watchdog.

After this, start the Mac-side pusher (separate terminal):

  python3 mac/hls_push.py                      # built-in demo source
  HLS_URL=https://example.invalid/live.m3u8 python3 mac/hls_push.py  # real HLS

Environment:
  MEURAL_HOST   device IP (default: placeholder 192.0.2.10 - set yours)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtc_shell2 import rtc            # noqa: E402
from push_files import push_file, sl  # noqa: E402


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    dev = os.path.abspath(os.path.join(here, "..", "device"))

    ingest = sl("tmp", "ingest.py")
    watch = sl("tmp", "watch.sh")

    push_file(os.path.join(dev, "ingest.py"), ingest)
    push_file(os.path.join(dev, "watch.sh"), watch)

    print("stopping old services ...")
    rtc(";pkill -f 'watch[.]sh'; sleep 1")
    rtc(";pkill -9 -f 'mplaye[r]'; pkill -f 'ingest[.]py'; sleep 1")

    print("starting ingest server (8484, RAM only) ...")
    rtc(";setsid nohup python3 " + ingest + " >" + sl("tmp", "ingest.log") + " 2>&1 & sleep 2")

    print("starting mplayer watchdog ...")
    rtc(";setsid nohup sh " + watch + " >" + sl("dev", "null") + " 2>&1 & sleep 3")

    print("done. Now start the Mac pusher:")
    print("  python3 mac/hls_push.py                       # demo source")
    print("  HLS_URL=https://... python3 mac/hls_push.py   # real HLS source")


if __name__ == "__main__":
    main()

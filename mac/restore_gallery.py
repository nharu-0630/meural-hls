#!/usr/bin/env python3
"""Stop the live pipeline and restore the normal gallery on the frame.

Environment:
  MEURAL_HOST        device IP (must be set / default placeholder)
  MEURAL_GALLERY_ID  gallery id to switch back to (see /remote/get_galleries_json)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mac import meural_api  # noqa: E402
from mac.rtc_shell2 import rtc  # noqa: E402

GALLERY_ID = os.environ.get("MEURAL_GALLERY_ID", "")


def main() -> None:
    if not GALLERY_ID:
        print("Set MEURAL_GALLERY_ID (list ids with this one-liner):")
        print(
            '  python3 -c \'import sys; sys.path.insert(0,"mac");'
            "from mac import meural_api as m;"
            "[print(g) for g in m.galleries()]'"
        )
        sys.exit(1)

    print("stopping watchdog ...")
    rtc(";pkill -f 'watch[.]sh'; sleep 1")
    print("killing mplayer ...")
    rtc(";pkill -9 -f 'mplaye[r]'; sleep 1")

    print("change_gallery ->", GALLERY_ID)
    try:
        print(meural_api.change_gallery(GALLERY_ID))
    except Exception as e:
        print("change_gallery failed:", e)
        print("(the direct HTTP GET equivalent is documented in docs/NOTES.md)")

    time.sleep(3)
    try:
        print("video status:", meural_api.video_status())
    except Exception as e:
        print("status check failed:", e)


if __name__ == "__main__":
    main()

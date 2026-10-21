#!/bin/sh
# mplayer watchdog: 再生プロセスが落ちたら自動で再起動する (RAM上のライブ再生の常駐化)
# 停止する場合は: pkill -f 'watch[.]sh'; pkill -9 -f 'mplaye[r]'
while true; do
  /usr/bin/mplayer -nocorrect-pts -framedrop -nosound \
    -cache 4096 -cache-min 15 -demuxer lavf -vo fbdev2 \
    http://127.0.0.1:8484/live.ts >/tmp/mv.log 2>&1
  sleep 2
done

# Hardware & design notes (Meural Canvas MC321)

Distilled findings from building the RAM-only live pipeline. Each item was
confirmed by measurement on the device.

## Device facts

- Framebuffer `/dev/fb0` is 1920x1080, 32bpp BGRA. `mplayer -vo fbdev2` does
  **not** scale, so the source must be exactly 1920x1080.
- Stock binaries: `mplayer 1.2.1` (libavcodec 56.x), `python3`, and a local
  HTTP API at `http://<device>/remote/...` (reversed from the on-device web UI).
- 4-core ARM (RK3288), ~400 MB free RAM. Software decode of 1080p15 H.264 runs
  at ~33% codec CPU (mplayer status line) — comfortable.
- The device clock may drift by tens of minutes; never use it for A/V sync.
- Device → outside connections may not work; Mac → device + device →
  127.0.0.1 works fine.

## Local remote API

Useful endpoints (GET based, no auth on LAN):

```
/remote/identify/
/remote/control_check/{system,video,sleep}/
/remote/get_galleries_json/          /remote/get_gallery_status_json/
/remote/control_command/change_gallery/<id>/
/remote/control_command/change_item/<id>/
/remote/control_command/set_backlight/<n>/
/remote/control_command/{suspend,resume}/
/remote/postcard/                    (multipart upload: image or video)
```

`video_status()` (control_check/video) reflects the *stock* video player path
only — a direct mplayer run keeps showing video while the status says false.
That is cosmetic; ignore it.

### The rtc "shell" trick

`/remote/control_command/rtc/<payload>/` evaluates the payload roughly as
`sh -c 'eval date -s @<payload>'`. A payload starting with `;` escapes the
`date` context — effectively a root shell one-liner. Hard rules learned the
painful way:

- The URL goes through **double decoding**: every literal `/` must be written
  as `$(printf '\057')` and every `:` as `$(printf '\072')` (helpers `sl()` /
  `co()` in `push_files.py`), otherwise requests 404/400 out.
- There is a request-size limit in front of the PHP layer: base64url chunk PUTs
  above ~4 KB get **HTTP 403 with an HTML page and are silently dropped**
  (a file later materializes as 0 bytes). Keep base64url chunks at ≤ 2000
  bytes (the default in `push_files.py`) and always verify the transferred
  md5/size.
- Bare `$()` / `${}` in the payload are consumed by the first decode level;
  escape **all** `$` as `\$` so they expand at the eval level.
- `%s` anywhere in a payload → HTTP 400. Payload length ≤ ~4 KB.
- `pkill -f <pattern>` must not contain a literal that appears in the payload
  itself (self-match). Use bracket classes: `pkill -f 'mplaye[r]'`,
  `'watch[.]sh'`, `'ingest[.]py'` — and run "kill" and "start" in **separate**
  payloads.

## The mplayer command is load-bearing

Stock mplayer 1.2.1 quirks, each confirmed:

| Option | Why it is required |
|---|---|
| `-demuxer lavf` | The **native** MPEG-TS demuxer stalls after `TS file format detected` with low-bitrate (~30 KB/s) input: 100% CPU spin + thousands of `Cache empty` lines, never starts.The lavf demuxer starts in seconds and is what the whole design depends on. |
| `-vo fbdev2` | The `x11` VO dies after ~4-5 min (`XIO: fatal IO error 11`). fbdev2 runs for hours. |
| `-nocorrect-pts` | Live-streamed TS with lavf demux behaves predictably without PTS correction. |
| `-framedrop -nosound` | No audio in the stream; keeps pacing simple. |
| `-cache 4096 -cache-min 15` | Absorbs the 2-second segment cadence and the initial fill. |

`-lavdopts fast:threads=4` in the payload **kills** this mplayer build almost
immediately — do not add it.

### mplayer status line (turn off `-quiet`)

```
V:  <position s>  <frame>/<frame>  %cpu  %codec ...
```

Wall-clock delta vs. `V:` delta == real-time rate (should be ~1.00x; frames
delta / time == fps). This is the cheapest end-to-end sync probe.

## Mac-side encoder rules

- **Continuous timestamps across segments**: the segment muxer must run with
  `-reset_timestamps 0`. With `1`, every segment restarts PTS and the stream
  shows `DTS ... out of order` — the mosaics many people see come exactly from
  this.
- **Video only (`-an`)**: an audio lavfi input is *not* paced by `-re`, runs
  away, and corrupts the PTS field (the demuxer saw 26 hours of PTS!). With
  `-nosound` on the device, audio is pointless anyway.
- **Bitrate vs content**: smooth 1080p gradients need ~2 Mbps at `veryfast`;
  at 250 kbps / ultrafast you get pictures full of 16x16 blocks. (Those blocks
  were encoder quantization — the earlier "corruption" in transit had been
  fixed separately.)
- **SUPERFAST preset for real sources**: with `-re` pacing a remote HLS input
  and 24 fps content downsampled to 15 fps, `veryfast` runs at ~0.95x realtime
  (slow creep behind live). `superfast` keeps up. For pure content-generation
  sources (gradients) `veryfast` is fine.
- **Close-aware pushing**: never POST the segment ffmpeg is still writing —
  the pusher always excludes the newest file, then deletes pushed files.
  Files under 4 KB (opened but not yet written) are skipped and, once a later
  healthy segment proves them dead, dropped without blocking the counter.
- **ffmpeg 9.x drawbox quirk**: the `t` timestamp variable is stuck at 0
  inside drawbox expressions (both as `-vf` and inside a lavfi input graph).
  The `n` variable is not available at all. Use filters that animate by design
  (`gradients`, `life`, `testsrc2`) for demo sources.

## Ingest server design (`device/ingest.py`)

- Holds segments in Python dicts/queues (`/dev/shm` optional) — nothing
  touches flash.
- `/seg?n=N` stores the body and appends it to every registered `/live.ts`
  client queue under one lock — registration (snapshot of last 3 segments +
  queue append) is atomic, so there are no gaps or duplicates.
- **Real-time paced drip** (`device/ingest.py`): each segment is written in 1 KB
  chunks sleeping `gap*1024/len(seg)` seconds per chunk, where `gap` is the
  **measured arrival interval** of that segment (fallback 2.0 s). The outgoing
  stream therefore follows the *producer's* real-time exactly — even if ffmpeg
  runs at 0.9x realtime fetching a remote HLS source, there are no holes for
  the player. (A fixed nominal 2 s pace leaves ~0.1 s holes per segment and
  drains the cache over minutes.)
- Playlist `/live.m3u8` is served for debugging; playback goes through the
  endless `/live.ts`, not HLS.

## Diagnostics that proved decisive

- **Framebuffer dump**: `dd if=/dev/fb0 of=/tmp/fbx.raw bs=8M count=1`, fetch
  it, then `ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -i fbx.raw frame.png`.
  Comparing two dumps seconds apart separates "not drawing" from "drawing a
  static source".
- **Two-frame md5 of adjacent segments** proves whether the *source* animates
  (identical md5 = the source is frozen even if the whole pipeline works).
- **Stream capture + decode on the Mac** (`curl -N http://<device>:8484/live.ts
  -o cap.ts` then `ffmpeg -v error -i cap.ts -f null -`) localizes corruption
  to transit vs device decode.
- Bit-exact round-trip test: `curl -X POST --data-binary @seg.ts
  'http://<device>:8484/seg?n=9999'` then GET `/s9999.ts` and `cmp` — proves
  the POST path byte-exact.

## Failure modes to remember

- Killing the pusher leaves an **orphan ffmpeg** behind; a blanket
  `pkill -f "[s]eg%06d"`-style cleanup avoids numbering collisions.
- A server restart briefly 500s/404s every rtc payload — payloads with
  retries survive; that is why `post()` retries 6 times.
- Deleting the CWD of a running simple HTTP server breaks it (500s) until
  restarted.

## What "done" looks like

- 300 s of wall time == 300 s of `V:` position advance, 15.0x fps, 0 decode
  errors, 0 `DTS out of order` after the first join.
- Framebuffer dumps differ over seconds (motion), match the current source
  frame (correctness).
- User-visible: smooth full-screen motion, no blocks, no tearing complaints
  at 15 fps.

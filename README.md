# meural-hls

**Full-screen live video on a Meural Canvas (MC321) — fed from your Mac, zero disk writes on the device, no cloud, no firmware modification.**

A Mac encodes any video source (a real HLS `.m3u8` URL, or a built-in animated demo) into 2-second MPEG-TS segments and pushes them over plain HTTP into a tiny ingest server running **in the device's RAM (tmpfs-style: nothing is written to flash)**. A stock `mplayer` on the device plays a seamless endless MPEG-TS stream (`http://127.0.0.1:8484/live.ts`) straight into the framebuffer (`/dev/fb0`) at the panel's native 1920x1080, with a watchdog keeping it alive.

Verified end-to-end: wall-clock == stream PTS (1.0x real time), 15 fps, zero decode errors, framebuffer diff confirms motion.

```
Mac                                       Device (Meural Canvas MC321)
─────────────────────────────────────     ──────────────────────────────────────
ffmpeg (re-encode 1920x1080@15, 2 Mbps)   ingest.py  (python3, port 8484, RAM only)
   │  2s MPEG-TS segments                    │  /seg?n=N  ← segments land in RAM buffers
   └─ HTTP POST ───────────────────────────► │  /live.ts  ← seamless stitched stream
                                             │               (real-time paced drip)
hls_push.py (pusher, retry-safe)             ▼
                                          mplayer -vo fbdev2  →  /dev/fb0  →  panel
                                          watch.sh (watchdog: auto-restart)
```

## Features

- **Real HLS support** — point `HLS_URL` at any `.m3u8` (live or VOD); the Mac fetches it and re-encodes for the panel.
- **Nothing persisted on the device** — both device-side components live in `/tmp` and vanish on reboot. Restoring is one command.
- **No firmware modification** — only stock binaries (`python3`, `mplayer`) plus a userland script pushed to RAM.
- **Self-healing** — watchdog restarts the player; the pusher retries through server hiccups; the ingest drip self-syncs to the source bitrate.
- **Clean exit** — `restore_gallery.py` stops everything and switches the frame back to your normal gallery.

## Repository layout

```
mac/                     Mac-side (master controller)
  hls_push.py            ffmpeg encoder + segment pusher (the only long-running Mac process)
  rtc_shell2.py          tiny client for the device's local rtc command (= remote shell)
  push_files.py          file transfer into device RAM via base64url chunks
  meural_api.py          local remote-API client (identify / status / gallery / postcard ...)
  deploy_live.py         one-shot installer: push device files + start services
  restore_gallery.py     stop live mode and switch back to a gallery
device/                  Device-side components (run entirely from RAM)
  ingest.py              HTTP server on :8484 — holds segments in memory, serves /live.ts
  watch.sh               mplayer watchdog (restarts the player within ~2 s if it dies)
docs/NOTES.md            Hardware/design notes: gotchas, quirks, and why each option is set
```

To use your own gallery id, set `MEURAL_GALLERY_ID` (see `/remote/get_galleries_json` via `meural_api.galleries()`).

## Quick start

Requirements:

- **Mac** (or any Linux/BSD host): Python 3, `ffmpeg` with libx264
- **Device**: Meural Canvas MC321 on the same LAN, running stock firmware (local `http://<device>/remote/...` API)

```bash
export MEURAL_HOST=<your device IP>

# 1) One-time install (pushes ~4 KB of python into device RAM, starts services)
python3 mac/deploy_live.py

# 2) Start the Mac pusher — built-in animated demo source
python3 mac/hls_push.py

#    ...or with a real HLS stream:
HLS_URL=https://example.invalid/path/live.m3u8 python3 mac/hls_push.py

# Screen switches to live video within ~30 s (first GOP must fill the buffer).
```

### From source URLs to the panel — configuration reference

| Variable | Where | Meaning |
|---|---|---|
| `MEURAL_HOST` | all Mac scripts | Device IP on your LAN |
| `HLS_URL` | `hls_push.py` | Real HLS source. Unset = built-in animated gradients demo |
| `MEURAL_GALLERY_ID` | `restore_gallery.py` | Gallery id to restore on exit |

### Ports / files on the device

| Path (all in RAM) | Purpose |
|---|---|
| `/tmp/ingest.py` | ingest server, listens on :8484 (`/seg`, `/pl`, `/live.ts`, `/live.m3u8`) |
| `/tmp/watch.sh` | watchdog loop around `mplayer` |
| `/tmp/mv.log` | mplayer log (status line shows playback position) |

### Restoring normal mode

```bash
MEURAL_GALLERY_ID=<your gallery id> python3 mac/restore_gallery.py
```

Stops the watchdog + player (device-side commands), then calls the local
`/remote/control_command/change_gallery/<id>` API. The device returns to its
normal picture-frame behavior. Nothing needs cleanup — the RAM-only components
die with the next reboot and are inert when not started.

## How it works — the interesting bits

**Seamless stream in RAM (`device/ingest.py`).** The pusher POSTs each closed
segment to `/seg?n=<n>`; the server keeps the last ~14 segments in Python
buffers and appends them to every connected client's queue. `GET /live.ts`
returns an *endless* MPEG-TS: the last 3 buffered segments are drained first
(real-time paced), then the live queue follows. Because segments carry
continuous timestamps and the drip rate follows the *actual* source bitrate
(each segment is spread over its real 2-second duration), the player never
starves and never falls behind.

**Playback (`device/watch.sh`).** mplayer 1.2 (the stock build on the device)
plays the loopback stream with:

```
-nocorrect-pts -framedrop -nosound -cache 4096 -cache-min 15 \
-demuxer lavf -vo fbdev2 http://127.0.0.1:8484/live.ts
```

`-demuxer lavf` is **required**: the native MPEG-TS demuxer stalls at low
bitrates (see docs/NOTES.md). `fbdev2` blits 1080p frames directly into
`/dev/fb0`; the source must therefore be exactly 1920x1080.

**Encoder (`mac/hls_push.py`).** x264 `veryfast`, 2 Mbps, 15 fps, GOP 30,
**video-only** (`-an`) and continuous timestamps across segment boundaries.
The pusher watches the output directory, POSTs every *closed* segment
(never the one being written), retries on failure, and also serves a minimal
HLS playlist endpoint for debugging.

**Remote access (`mac/rtc_shell2.py`).** Deployment uses the device's local
web API `rtc` endpoint, which happens to evaluate a timestamp argument as a
shell command. The payload rules (escape `/` and `:` via printf octal, etc.)
are implemented in the scripts and documented in docs/NOTES.md.

## Persistence & backup

Device-side components are intentionally RAM-only. This repository is the
canonical backup — after any reboot:

```bash
python3 mac/deploy_live.py     # re-pushes files and restarts services
python3 mac/hls_push.py        # Mac pusher (`HLS_URL=...` for a real stream)
```

If you prefer the components to survive reboots on-device, copy them to a
persistent location (e.g. `/home/meural/`) and adjust `deploy_live.py` — but
RAM-only is the recommended, least-invasive configuration.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Panel shows demo/HLS picture but frozen | Check `V:` line in `/tmp/mv.log` advances ~1 s/s and `/dev/fb0` content changes (see NOTES) |
| mplayer stuck after `TS file format detected` | Missing `-demuxer lavf` |
| Mosaic/blocky artifacts | Bitrate too low for the content (2 Mbps works for smooth gradients at 1080p) |
| Player dies every few minutes | Fixed by `-vo fbdev2` (x11 VO is unreliable long-run) and `-demuxer lavf` |
| `HTTP 500` from rtc payloads | Payload contains a literal `/` or bare `$()` — use the escaping helpers in `push_files.py` |
| Watchdog relaunch loop | Stop it first: `pkill -f 'watch[.]sh'`, then `pkill -9 -f 'mplaye[r]'` (separate commands — the watchdog would otherwise restart the player) |

See **docs/NOTES.md** for the full list of hardware quirks discovered along the way.

## Disclaimer

This project operates **your own device** on your LAN using its stock local
API and stock binaries. No firmware is modified and nothing outlives a reboot
by default. You are responsible for complying with the terms of your device
and for what you display on it. Use at your own risk.

## License

MIT

# RAM ingest HTTP contract

The device runs `device/ingest.py` on port 8484. Bodies are raw bytes and all
responses disable caching. Routes are unchanged; the optional producer session
and size limits below make retries and restarts explicit.

| Request | Success | Failure |
|---|---|---|
| `POST /seg?n=N[&session=ID]` | `200`, body `ok`; retains segment in RAM | `400` invalid/missing sequence or incomplete body; `413` empty body or over 4 MiB |
| `POST /pl` | `200`, body `ok`; replaces playlist | `400` invalid/incomplete length; `413` empty body or over 64 KiB |
| `GET /sN.ts` | `200 video/mp2t`, exact stored bytes | `404` missing segment |
| `GET /live.m3u8` | `200 application/vnd.apple.mpegurl`, current playlist | — |
| `GET /live.ts` | `200 video/mp2t`, paced snapshot then live bytes | `503` if eight live viewers are already registered |
| Other paths | — | `404` |

`N` contains 1–12 decimal digits. Requests need a positive `Content-Length`.
Segments are opaque to ingest: the encoder/pusher supplies MPEG-TS and the
playlist supplies HLS syntax. Size checks do not validate video encoding.

Repeated segment sequence numbers in the active retained window are idempotent.
A changed `session` resets history, playlist and arrival timing, and closes
existing live queues so the watchdog reconnects with the new timestamps. The
new pusher sends a fresh UUID for each process run; legacy callers may omit it.

History retains 15 segments. Each viewer receives the latest three at connection
and then an eight-segment queue. A full queue causes disconnection. Live responses
also close after 15 seconds without a producer. Transfers use monotonic deadlines
and 16 KiB chunks; producer arrival gaps are clamped to 0.03–4 seconds.

The pusher defaults to transcoding and only uploads closed files. The newest file
is withheld while ffmpeg writes it. On upload failure, subsequent files wait for
the failed sequence to succeed. Debug playlists only advertise the last two
confirmed uploads and mark gaps as discontinuities. A queue of more than 30 closed
files stops the pusher instead of accumulating video indefinitely. On exit,
ffmpeg is terminated, reaped and its private temporary directory removed.

`postman/mocks/ingest` models only the two upload paths used by the pusher. It
adds `/reset` and `/received` for verification and returns 501 for paths outside
this subset. This is a repository-owned contract; no external workspace mock
lookup is needed. Run its simulation and then `uv run python -m scripts.check_retry`
to verify order across a full six-attempt failure cycle using the real encoder
and HTTP client. This does not validate playback on the physical frame.

"""RAM-only segment ingest. Kept compatible with the device's Python 3.5+."""

import http.server
import queue
import re
import socket
import socketserver
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple, cast  # noqa: F401
from urllib.parse import parse_qs, urlsplit

Segment = Tuple[bytes, float]
MAX_SEGMENT_BYTES = 4 * 1024 * 1024
MAX_PLAYLIST_BYTES = 64 * 1024
HISTORY_SIZE = 15
CLIENT_BACKLOG = 8
MAX_CLIENTS = 8


class StreamState:
    def __init__(self):
        # type: () -> None
        self.lock = threading.Lock()
        self.buffers = OrderedDict()  # type: OrderedDict[int, Segment]
        self.clients = []  # type: List[queue.Queue[Optional[Segment]]]
        self.playlist = b"#EXTM3U\n"
        self.last_arrival = None  # type: Optional[float]
        self.session = None  # type: Optional[str]

    @staticmethod
    def disconnect(client):
        # type: (queue.Queue[Optional[Segment]]) -> None
        while True:
            try:
                client.get_nowait()
            except queue.Empty:
                break
        client.put_nowait(None)

    def publish(self, sequence, body, session=None):
        # type: (int, bytes, Optional[str]) -> None
        with self.lock:
            if session is not None and session != self.session:
                self.session = session
                self.buffers.clear()
                self.playlist = b"#EXTM3U\n"
                self.last_arrival = None
                for client in self.clients:
                    self.disconnect(client)
                self.clients.clear()
            if sequence in self.buffers:
                return  # Retried POSTs must not duplicate playback or change pacing.
            now = time.monotonic()
            gap = (
                2.0
                if self.last_arrival is None
                else max(0.03, min(4.0, now - self.last_arrival))
            )
            self.last_arrival = now
            segment = (body, gap)
            self.buffers[sequence] = segment
            while len(self.buffers) > HISTORY_SIZE:
                self.buffers.popitem(last=False)
            for client in list(self.clients):
                try:
                    client.put_nowait(segment)
                except queue.Full:
                    # Disconnect slow viewers so mplayer can reconnect at live edge.
                    self.disconnect(client)
                    self.clients.remove(client)

    def subscribe(self):
        # type: () -> Tuple[queue.Queue[Optional[Segment]], List[Segment]]
        with self.lock:
            if len(self.clients) >= MAX_CLIENTS:
                raise RuntimeError("too many live viewers")
            client = queue.Queue(maxsize=CLIENT_BACKLOG)  # type: queue.Queue[Optional[Segment]]
            self.clients.append(client)
            return client, list(self.buffers.values())[-3:]

    def unsubscribe(self, client):
        # type: (queue.Queue[Optional[Segment]]) -> None
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)


class IngestHandler(http.server.BaseHTTPRequestHandler):
    # BaseHTTPRequestHandler prevents exposing the server's working directory.
    def state(self):
        # type: () -> StreamState
        return cast(IngestServer, self.server).state

    def setup(self):
        # type: () -> None
        super().setup()
        self.connection.settimeout(10)

    def respond(self, body, status=200, content_type="text/plain"):
        # type: (bytes, int, str) -> None
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_POST(self):
        # type: () -> None
        url = urlsplit(self.path)
        if url.path not in ("/seg", "/pl"):
            self.respond(b"not found", 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(b"invalid Content-Length", 400)
            return
        limit = MAX_SEGMENT_BYTES if url.path == "/seg" else MAX_PLAYLIST_BYTES
        if not 0 < length <= limit:
            self.respond(b"invalid body size", 413)
            return
        params = parse_qs(url.query)
        sequence = None  # type: Optional[int]
        if url.path == "/seg":
            value = params.get("n", [""])[0]
            if not re.match(r"^\d{1,12}$", value):
                self.respond(b"invalid sequence", 400)
                return
            sequence = int(value)
        try:
            body = self.rfile.read(length)
        except (OSError, socket.timeout):
            self.respond(b"incomplete body", 400)
            return
        if len(body) != length:
            self.respond(b"incomplete body", 400)
            return
        state = self.state()
        if sequence is not None:
            state.publish(sequence, body, params.get("session", [None])[0])
        else:
            with state.lock:
                state.playlist = body
        self.respond(b"ok")

    def drip(self, data, gap):
        # type: (bytes, float) -> None
        # Deadline pacing compensates for write/scheduler time and reduces small writes.
        start = time.monotonic()
        chunk_size = 16 * 1024
        for offset in range(0, len(data), chunk_size):
            end = min(offset + chunk_size, len(data))
            self.wfile.write(data[offset:end])
            delay = start + gap * end / len(data) - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def do_GET(self):
        # type: () -> None
        path = urlsplit(self.path).path
        state = self.state()
        if path == "/live.m3u8":
            with state.lock:
                playlist = state.playlist
            self.respond(playlist, content_type="application/vnd.apple.mpegurl")
        elif path == "/live.ts":
            try:
                client, snapshot = state.subscribe()
            except RuntimeError:
                self.respond(b"too many live viewers", 503)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                for data, gap in snapshot:
                    self.drip(data, gap)
                while True:
                    try:
                        segment = client.get(timeout=15)
                    except queue.Empty:
                        break  # No producer: let the watchdog reconnect.
                    if segment is None:
                        break
                    self.drip(*segment)
            except OSError:
                pass
            finally:
                state.unsubscribe(client)
        else:
            match = re.match(r"^/s(\d{1,12})\.ts$", path)
            if match:
                with state.lock:
                    segment = state.buffers.get(int(match.group(1)))
                if segment is not None:
                    self.respond(segment[0], content_type="video/mp2t")
                    return
            self.respond(b"not found", 404)


class IngestServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, state=None):
        # type: (Tuple[str, int], Optional[StreamState]) -> None
        self.state = state if state is not None else StreamState()
        super().__init__(address, IngestHandler)


def main():
    # type: () -> None
    server = IngestServer(("", 8484))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

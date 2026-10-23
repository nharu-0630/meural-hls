from __future__ import annotations

import ast
import http.client
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from device.ingest import CLIENT_BACKLOG, HISTORY_SIZE, IngestServer, StreamState
from mac.hls_push import build_ffmpeg, closed_segments, playlist


class StateTests(unittest.TestCase):
    def test_history_and_viewer_backlog_are_bounded(self) -> None:
        state = StreamState()
        client, _ = state.subscribe()
        for sequence in range(100):
            state.publish(sequence, b"video")
        self.assertEqual(len(state.buffers), HISTORY_SIZE)
        self.assertLessEqual(client.qsize(), CLIENT_BACKLOG)
        self.assertNotIn(client, state.clients)
        self.assertIsNone(client.get_nowait())

    def test_retry_is_idempotent(self) -> None:
        state = StreamState()
        state.publish(0, b"video", "session")
        client, snapshot = state.subscribe()
        arrival = state.last_arrival
        state.publish(0, b"video", "session")
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(state.last_arrival, arrival)
        self.assertTrue(client.empty())

    def test_new_producer_session_clears_old_timestamps_and_viewers(self) -> None:
        state = StreamState()
        state.publish(99, b"old", "old-session")
        client, _ = state.subscribe()
        state.publish(0, b"new", "new-session")
        self.assertEqual(list(state.buffers), [0])
        self.assertIsNone(client.get_nowait())
        self.assertEqual(state.buffers[0], (b"new", 2.0))

    def test_snapshot_and_live_queue_do_not_overlap(self) -> None:
        state = StreamState()
        state.publish(0, b"first")
        client, snapshot = state.subscribe()
        state.publish(1, b"second")
        self.assertEqual(snapshot[0][0], b"first")
        segment = client.get_nowait()
        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment[0], b"second")
        with self.assertRaises(queue.Empty):
            client.get_nowait()

    def test_device_script_keeps_python35_syntax(self) -> None:
        ast.parse(Path("device/ingest.py").read_text(), feature_version=(3, 5))


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = IngestServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_bit_exact_upload_download_and_playlist(self) -> None:
        data = bytes(range(256)) * 20
        request = urllib.request.Request(f"{self.base}/seg?n=1", data=data)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.read(), b"ok")
        with urllib.request.urlopen(f"{self.base}/s1.ts") as response:
            self.assertEqual(response.headers["Content-Type"], "video/mp2t")
            self.assertEqual(response.read(), data)
        body = playlist([1])
        with urllib.request.urlopen(
            urllib.request.Request(f"{self.base}/pl", data=body)
        ):
            pass
        with urllib.request.urlopen(f"{self.base}/live.m3u8") as response:
            self.assertEqual(response.read(), body)

    def test_bad_sequence_and_filesystem_paths_rejected(self) -> None:
        for path, body, code in [
            ("/seg?n=bad", b"x", 400),
            ("/seg?n=-1", b"x", 400),
            ("/pyproject.toml", None, 404),
            ("/", None, 404),
        ]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(
                    urllib.request.Request(self.base + path, data=body)
                )
            self.assertEqual(caught.exception.code, code)
            caught.exception.close()

    def test_oversized_body_rejected_before_reading(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        try:
            connection.putrequest("POST", "/seg?n=1")
            connection.putheader("Content-Length", str(100 * 1024 * 1024))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()


class PusherTests(unittest.TestCase):
    def test_only_closed_segments_in_sequence_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name in [
                "seg000002.ts",
                "seg000000.ts",
                "seg000001.ts",
                "unrelated.ts",
                "seg2.ts.bak",
            ]:
                (work / name).write_bytes(b"video")
            self.assertEqual([n for n, _ in closed_segments(work, -1)], [0, 1])
            self.assertEqual([n for n, _ in closed_segments(work, 0)], [1])

    def test_playlist_only_advertises_confirmed_segments(self) -> None:
        body = playlist([3, 5]).decode()
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:3", body)
        self.assertNotIn("s4.ts", body)
        self.assertIn("#EXT-X-DISCONTINUITY\n#EXTINF:2.0,\ns5.ts", body)

    def test_copy_mode_avoids_reencoding(self) -> None:
        args = build_ffmpeg(Path("/tmp/test"), "http://localhost/live.m3u8", copy=True)
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertNotIn("-vf", args)
        self.assertNotIn("libx264", args)
        with self.assertRaises(ValueError):
            build_ffmpeg(Path("/tmp/test"), None, copy=True)


if __name__ == "__main__":
    unittest.main()

import threading
import time
import unittest
from types import SimpleNamespace
from musicdl.modules.sources.base import BaseMusicClient


class Session:
    def close(self):
        pass


class Client:
    default_headers = {}
    default_cookies = {}
    disable_print = True
    logger_handle = SimpleNamespace(warning=lambda *args, **kwargs: None)
    _parse_playlist_tracks = BaseMusicClient._parse_playlist_tracks

    def _initsession(self):
        self.session = Session()
        self.audio_link_tester = SimpleNamespace(session=Session())
        self.quark_audio_link_tester = SimpleNamespace(session=Session())


class PlaylistConcurrencyTest(unittest.TestCase):
    def test_concurrency_progress_failures_order_and_session_isolation(self):
        """慢歌不阻塞其他结果，并发不超过三首，失败仍计入进度且保留顺序。"""
        client = Client()
        lock = threading.Lock()
        release = threading.Event()
        active = maximum = 0
        sessions = []
        progress = []

        def parse(worker, track):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                sessions.append(worker.session)
            try:
                if track == 0:
                    assert release.wait(5)
                time.sleep(0.01)
                if track == 4:
                    raise ValueError('模拟单首失败')
                return track
            finally:
                with lock:
                    active -= 1

        def report(song, processed, total):
            progress.append((song, processed, total))
            if processed == 2:
                release.set()

        result = client._parse_playlist_tracks(list(range(7)), parse, report)
        self.assertEqual(result, [0, 1, 2, 3, 5, 6])
        self.assertEqual(maximum, 3)
        self.assertEqual(len({id(session) for session in sessions}), 7)
        self.assertEqual([item[1] for item in progress], list(range(8)))
        self.assertTrue(all(item[2] == 7 for item in progress))
        self.assertNotEqual(progress[1][0], 0)

    def test_empty_playlist(self):
        """空歌单正常完成并上报零进度。"""
        progress = []
        self.assertEqual(Client()._parse_playlist_tracks([], None, lambda *args: progress.append(args)), [])
        self.assertEqual(progress, [(None, 0, 0)])

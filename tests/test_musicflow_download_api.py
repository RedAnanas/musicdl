import hashlib
import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from musicdl.modules.sources.netease import NeteaseMusicClient


APP_PATH = Path(__file__).resolve().parents[1] / 'examples' / 'claudeai-modern-web-music-player' / 'app.py'
spec = importlib.util.spec_from_file_location('musicdl_web_app', APP_PATH)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class FakeResponse:
    def __init__(self, data, fail=False):
        self.data = data
        self.fail = fail
        self.headers = {'Content-Length': str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError('下载失败')

    def iter_content(self, chunk_size):
        yield self.data


class MusicFlowDownloadApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = web.app.test_client()
        self.staging = Path(self.temp.name) / 'staging'
        self.old_staging = web.V1_STAGING_DIR
        web.V1_STAGING_DIR = str(self.staging)
        self.addCleanup(setattr, web, 'V1_STAGING_DIR', self.old_staging)
        self.normal_dir = Path(self.temp.name) / 'normal'
        self.normal_dir.mkdir()
        self.normal_file = self.normal_dir / '已有歌曲.mp3'
        self.normal_file.write_bytes(b'original')
        self.old_settings = web.SETTINGS['download_directories']
        web.SETTINGS['download_directories'] = [{'name': '普通目录', 'path': str(self.normal_dir)}]
        self.addCleanup(web.SETTINGS.__setitem__, 'download_directories', self.old_settings)
        self.song = SimpleNamespace(download_url='https://example.test/audio', ext='mp3',
                                    song_name='测试歌曲', singers='测试歌手', file_size_bytes=0)
        self.entry = {'song_info': self.song, 'headers': {}, 'cookies': {}}
        self.registry = patch.object(web.REGISTRY, 'get', side_effect=lambda token: self.entry if token == 'valid' else None)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.metadata = patch.object(web, '_embed_metadata')
        self.metadata.start()
        self.addCleanup(self.metadata.stop)
        web.DOWNLOADS.clear()

    def wait_status(self, download_id):
        for _ in range(100):
            result = self.client.get(f'/api/v1/downloads/{download_id}')
            if result.json['status'] in ('done', 'error'):
                return result.json
            time.sleep(0.01)
        self.fail('下载任务未结束')

    def test_resolve_playlist_and_song_links(self):
        song = SimpleNamespace(with_valid_download_url=True, source='NeteaseMusicClient',
                               song_name='测试歌曲', singers='测试歌手', album='', ext='mp3',
                               file_size='', duration='', cover_url='')
        playlist_urls = []
        def fake_playlist(url, progress_callback=None):
            playlist_urls.append(url)
            if progress_callback:
                progress_callback(None, 0, 2)
                progress_callback(song, 1, 2)
                progress_callback(song, 2, 2)
            return [song]
        source = SimpleNamespace(parseplaylist=fake_playlist,
                                 _getsongmetainfo=lambda _id: {'id': '123'},
                                 _parsewiththirdpartapis=lambda **_kwargs: song,
                                 _parsewithofficialapiv1=lambda **_kwargs: song)
        def resolve(url):
            started = self.client.post('/api/resolve-link', json={'url': url})
            self.assertEqual(started.status_code, 202)
            for _ in range(100):
                result = self.client.get(f"/api/resolve-link/{started.json['job_id']}").json
                if result['status'] != 'resolving':
                    return result
                time.sleep(0.01)
            self.fail('链接解析任务未结束')
        with patch.object(web.MANAGER, 'client', return_value=source), \
             patch.object(web.REGISTRY, 'add', return_value='resolved-token'):
            playlist = resolve('https://music.163.com/#/playlist?id=123')
            single = resolve('https://music.163.com/#/song?id=123')
            self.assertEqual(playlist['tracks'][0]['token'], 'resolved-token')
            self.assertEqual(single['tracks'][0]['song_name'], '测试歌曲')
            self.assertEqual(resolve('https://example.com/song?id=123')['status'], 'error')
            for url in ('https://y.qq.com/n/ryqq/songDetail/ABC123',
                        'https://www.kugou.com/song/#hash=ABC123',
                        'https://www.kuwo.cn/play_detail/123'):
                with self.subTest(url=url):
                    self.assertEqual(resolve(url)['status'], 'completed')
            for url in ('https://y.qq.com/n3/other/pages/details/playlist.html?id=9784311226&appshare=android_qq',
                        'https://www.kugou.com/yy/special/single/123.html',
                        'https://www.kuwo.cn/playlist_detail/123'):
                with self.subTest(url=url):
                    result = resolve(url)
                    self.assertEqual(result['status'], 'completed')
                    self.assertEqual(len(result['tracks']), 1)
        self.assertIn('https://y.qq.com/n/ryqq/playlist/9784311226', playlist_urls)

    def test_playlist_final_order_matches_original_after_parallel_progress(self):
        """并发结果可先到先显示，最终结果恢复歌单顺序且复用已有令牌。"""
        songs = [SimpleNamespace(identifier=str(index), with_valid_download_url=True, source='NeteaseMusicClient',
                                 song_name=str(index), singers='歌手', album='', ext='mp3',
                                 file_size='', duration='', cover_url='') for index in range(3)]
        progress = []

        def parse_playlist(url, progress_callback):
            for completed, index in enumerate((2, 0, 1), 1):
                progress_callback(songs[index], completed, 3)
            return songs

        source = SimpleNamespace(parseplaylist=parse_playlist)
        with patch.object(web.MANAGER, 'client', return_value=source), patch.object(web.REGISTRY, 'add', side_effect=lambda song, _source: song.identifier):
            result = web.resolve_music_link('https://music.163.com/playlist?id=123', lambda track, processed, total: progress.append(track['token']))
        self.assertEqual(progress, ['2', '0', '1'])
        self.assertEqual([track['token'] for track in result], ['0', '1', '2'])

    def test_playlist_progress_is_visible_before_completion(self):
        """歌单逐首解析时，已完成的曲目应立即可见。"""
        ready, release = threading.Event(), threading.Event()
        track = {'token': 'resolved-token', 'song_name': '测试歌曲'}
        def fake_resolve(_url, callback):
            callback(track, 1, 2)
            ready.set()
            release.wait(2)
            callback(None, 2, 2)
            return [track]
        with patch.object(web, 'resolve_music_link', side_effect=fake_resolve):
            started = self.client.post('/api/resolve-link', json={'url': 'https://y.qq.com/playlist/123'})
            job_id = started.json['job_id']
            self.assertTrue(ready.wait(1))
            pending = self.client.get(f'/api/resolve-link/{job_id}').json
            self.assertEqual((pending['status'], pending['processed'], pending['total']), ('resolving', 1, 2))
            self.assertEqual(pending['tracks'], [track])
            release.set()
            for _ in range(100):
                done = self.client.get(f'/api/resolve-link/{job_id}').json
                if done['status'] == 'completed':
                    break
                time.sleep(0.01)
            self.assertEqual(done['tracks'], [track])

    def test_shared_link_formats_are_recognized(self):
        """支持移动端分享地址、转义参数及附带分享文案的链接。"""
        song = SimpleNamespace(with_valid_download_url=True, source='QQMusicClient',
                               identifier='track-1', song_name='测试歌曲', singers='测试歌手',
                               album='', ext='mp3', file_size='', duration='', cover_url='')
        playlist_urls, song_ids = [], []
        def playlist(url):
            playlist_urls.append(url)
            return [song]
        def metadata(song_id):
            song_ids.append(song_id)
            return {}
        source = SimpleNamespace(parseplaylist=playlist, _getsongmetainfo=metadata,
                                 default_cookies={}, _parsewiththirdpartapis=lambda **_kwargs: song,
                                 _parsewithofficialapiv1=lambda **_kwargs: song)
        with patch.object(web.MANAGER, 'client', return_value=source), \
             patch.object(web.REGISTRY, 'add', return_value='resolved-token'):
            web.resolve_music_link('分享歌单：https://i.y.qq.com/n2/m/share/details/taoge.html?id=9784311226')
            web.resolve_music_link('https://y.qq.com/n3/other/pages/details/playlist.html?platform=11\\&id=9784311226')
            web.resolve_music_link('https://y.qq.com/n3/other/pages/details/song.html?mid=ABC123')
            web.resolve_music_link('http://m.music.163.com/#/song?id=186016')
            web.resolve_music_link('https://h5.kugou.com/song/#hash=KUGOU123')
            web.resolve_music_link('http://mobile.kuwo.cn/play_detail/456')
            web.resolve_music_link('https://i2.y.qq.com/n3/other/pages/details/playlist.html?id=9784311226')
            web.resolve_music_link('https://music.163.com/m/playlist?id=18421020344&creatorId=277760282')
            redirects = {
                'c6.y.qq.com': 'https://i.y.qq.com/v8/playsong.html?songmid=ABC123',
                '163cn.tv': 'https://y.music.163.com/m/song?id=186016',
            }
            def redirect(url, **_kwargs):
                return SimpleNamespace(status_code=302, headers={'Location': redirects[url.split('/')[2]]}, close=lambda: None)
            with patch.object(web.requests, 'get', side_effect=redirect):
                web.resolve_music_link('https://c6.y.qq.com/base/fcgi-bin/u?__=9QCaKqU79dSt')
                web.resolve_music_link('https://163cn.tv/bhgTis0f')
        self.assertEqual(playlist_urls[:3], ['https://y.qq.com/n/ryqq/playlist/9784311226'] * 3)
        self.assertEqual(playlist_urls[3], 'https://music.163.com/m/playlist?id=18421020344&creatorId=277760282')
        self.assertEqual(song_ids, ['ABC123', '186016', 'KUGOU123', '456', 'ABC123', '186016'])

    def test_netease_mobile_playlist_uses_query_id(self):
        """网易云移动端歌单跳转后仍从查询参数读取歌单 ID。"""
        class CapturedId(Exception):
            pass
        def post(_url, data, **_kwargs):
            self.assertEqual(data['id'], '18421020344')
            raise CapturedId
        source = SimpleNamespace(
            session=SimpleNamespace(head=lambda *_args, **_kwargs: SimpleNamespace(
                url='https://music.163.com/playlist?id=18421020344')),
            post=post,
        )
        with self.assertRaises(CapturedId):
            NeteaseMusicClient.parseplaylist.__wrapped__(source, 'https://music.163.com/m/playlist?id=18421020344')

    def test_short_link_rejects_external_redirect(self):
        """分享短链跳到非音乐平台时不继续访问目标地址。"""
        response = SimpleNamespace(status_code=302, headers={'Location': 'https://example.com/song?id=1'}, close=lambda: None)
        with patch.object(web.requests, 'get', return_value=response) as get:
            with self.assertRaises(ValueError):
                web.resolve_music_link('https://163cn.tv/bhgTis0f')
        self.assertEqual(get.call_count, 1)

    def test_netease_song_link_prefers_search_quality(self):
        """同一网易云曲目的链接解析应采用搜索时的无损优先策略。"""
        def song(ext):
            return SimpleNamespace(with_valid_download_url=True, source='NeteaseMusicClient',
                                   song_name='赛丽亚的旅馆(ACT.1-ACT.4) - gate', singers='Dungeon and Fighter',
                                   album='', ext=ext, file_size='', duration='', cover_url='')
        mp3, flac = song('mp3'), song('flac')
        source = SimpleNamespace(default_cookies={},
                                 _getsongmetainfo=lambda _id: {'id': 37240594, 'name': mp3.song_name},
                                 _parsewiththirdpartapis=lambda **_kwargs: flac,
                                 _parsewithofficialapiv1=lambda **kwargs: kwargs.get('song_info_flac') or mp3)
        with patch.object(web.MANAGER, 'client', return_value=source), \
             patch.object(web.REGISTRY, 'add', return_value='resolved-token'):
            tracks = web.resolve_music_link('https://music.163.com/song?id=37240594')
        self.assertEqual(tracks[0]['ext'], 'flac')

    def test_complete_flow_and_isolation(self):
        data = b'fake mp3 bytes'
        with patch.object(web.requests, 'get', return_value=FakeResponse(data)):
            self.assertEqual(self.client.options('/api/v1/downloads').status_code, 204)
            self.assertEqual(self.client.post('/api/v1/downloads', json={'token': 'expired'}).status_code, 404)
            self.assertEqual(self.client.post('/api/v1/downloads', json={}).status_code, 404)
            download_id = self.client.post('/api/v1/downloads', json={'token': 'valid'}).json['download_id']
            result = self.wait_status(download_id)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['checksum_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['filename'], '测试歌曲 - 测试歌手.mp3')
        response = self.client.get(f'/api/v1/downloads/{download_id}/file')
        self.assertEqual(response.data, data)
        response.close()
        self.assertEqual(self.client.get(f'/api/file/{download_id}').status_code, 404)
        self.assertEqual(self.normal_file.read_bytes(), b'original')
        self.assertEqual(list(self.normal_dir.iterdir()), [self.normal_file])
        task_dir = Path(web._get_dl(download_id)['task_dir'])
        self.assertEqual(self.client.delete(f'/api/v1/downloads/{download_id}/file').json, {'deleted': True})
        self.assertFalse(task_dir.exists())
        self.assertTrue(self.normal_file.exists())
        self.assertEqual(self.client.get(f'/api/v1/downloads/{download_id}').status_code, 404)

    def test_failure_and_legacy_directory(self):
        with patch.object(web.requests, 'get', return_value=FakeResponse(b'', fail=True)):
            download_id = self.client.post('/api/v1/downloads', json={'token': 'valid'}).json['download_id']
            result = self.wait_status(download_id)
        self.assertEqual(result['status'], 'error')
        self.assertIn('下载失败', result['message'])
        self.assertEqual(self.client.get(f'/api/v1/downloads/{download_id}/file').status_code, 404)
        self.assertTrue(self.client.delete(f'/api/v1/downloads/{download_id}/file').json['deleted'])
        self.assertEqual(self.client.post('/api/download', json={'token': 'valid'}).status_code, 400)
        with patch.object(web.requests, 'get', return_value=FakeResponse(b'legacy')):
            legacy_id = self.client.post('/api/download', json={'token': 'valid', 'directory_name': '普通目录'}).json['download_id']
            for _ in range(100):
                if web._get_dl(legacy_id).get('status') == 'done':
                    break
                time.sleep(0.01)
        self.assertEqual(web._get_dl(legacy_id)['status'], 'done')
        self.assertEqual(self.client.get(f'/api/v1/downloads/{legacy_id}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/v1/downloads/{legacy_id}/file').status_code, 404)
        response = self.client.get(f'/api/file/{legacy_id}')
        self.assertEqual(response.data, b'legacy')
        response.close()
        self.assertTrue(self.normal_file.exists())


if __name__ == '__main__':
    unittest.main()

import hashlib
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

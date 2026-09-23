'''
Function:
    A modern web-based music search / download / player powered by musicdl.

    The hard, slow part of musicdl is search: for every track it resolves the real
    audio URL (network round-trips), which is why a naive blocking call feels frozen.
    This server never blocks on that. It drives musicdl's per-result `_search` itself,
    watches the shared result list grow, and streams every track to the browser the
    instant it is resolved (Server-Sent Events). A per-source watchdog abandons any
    source that hangs, so one stuck platform can never freeze the whole UI.

Author:
    Built on top of CharlesPikachu/musicdl.
'''
import os
import re
import time
import uuid
import json
import queue
import threading
import requests
from pathlib import Path
from flask import Flask, request, Response, jsonify, send_from_directory, stream_with_context

from musicdl import __version__, musicdl
from musicdl.modules import MusicClientBuilder, SongInfoUtils
from musicdl.modules.utils.neteaseutils import MUSIC_QUALITIES


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, 'static')
DOWNLOAD_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get('MUSICDL_DOWNLOAD_DIR', os.path.join(HERE, 'downloads'))
))
SETTINGS_PATH = os.path.abspath(os.path.expanduser(
    os.environ.get('MUSICDL_SETTINGS_PATH', os.path.join(HERE, 'settings.json'))
))


def _load_settings():
    settings = {'download_directories': []}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as fp:
            saved = json.load(fp)
        directories = saved.get('download_directories')
        if isinstance(directories, list):
            for directory in directories:
                if not isinstance(directory, dict):
                    continue
                name = directory.get('name')
                path = directory.get('path')
                if isinstance(name, str) and name.strip() and isinstance(path, str) and os.path.isabs(path):
                    settings['download_directories'].append({
                        'name': name.strip(),
                        'path': os.path.abspath(os.path.expanduser(path.strip())),
                    })
    except (OSError, ValueError, TypeError):
        pass
    return settings


SETTINGS = _load_settings()

# 网易云从无损开始尝试；不可用时才降级到极高和标准品质。
MUSIC_QUALITIES[:] = ['lossless', 'exhigh', 'standard']

SOURCE_LABELS = {
    'MiguMusicClient': '咪咕音乐', 'NeteaseMusicClient': '网易云音乐',
    'KuwoMusicClient': '酷我音乐', 'QQMusicClient': 'QQ音乐',
    'KugouMusicClient': '酷狗音乐', 'QianqianMusicClient': '千千音乐',
    'BilibiliMusicClient': '哔哩哔哩', 'AppleMusicClient': 'Apple Music',
    'YouTubeMusicClient': 'YouTube 音乐', 'SpotifyMusicClient': 'Spotify',
}
SOURCE_ORDER = [
    'NeteaseMusicClient', 'QQMusicClient', 'KuwoMusicClient', 'KugouMusicClient',
    'MiguMusicClient', 'BilibiliMusicClient',
]
missing_sources = [source for source in SOURCE_ORDER if source not in MusicClientBuilder.REGISTERED_MODULES]
if missing_sources:
    raise RuntimeError(f'未注册的音乐源: {", ".join(missing_sources)}')
SUPPORTED_SOURCES = {
    source: {
        'label': SOURCE_LABELS.get(source, source.removesuffix('MusicClient')),
        'short': source.removesuffix('MusicClient'),
        'default': source in {'NeteaseMusicClient', 'QQMusicClient', 'KuwoMusicClient', 'KugouMusicClient'},
    }
    for source in SOURCE_ORDER
}

SEARCH_SIZE_PER_SOURCE = 8       # how many tracks to try to resolve per source
PER_SOURCE_TIMEOUT = 35          # seconds before a hanging source is abandoned
RESULT_EXT_TO_MIME = {
    'mp3': 'audio/mpeg', 'flac': 'audio/flac', 'wav': 'audio/wav',
    'm4a': 'audio/mp4', 'aac': 'audio/aac', 'ape': 'audio/x-ape', 'ogg': 'audio/ogg',
}


# ---------------------------------------------------------------------------
# musicdl client manager (one MusicClient, lazily built, holds all sources)
# ---------------------------------------------------------------------------
class ClientManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._clients = {}

    def _build(self, source):
        return musicdl.MusicClient(
            music_sources=[source],
            init_music_clients_cfg={source: {'search_size_per_source': SEARCH_SIZE_PER_SOURCE, 'disable_print': True}},
        )

    def client(self, source):
        with self._lock:
            if source not in self._clients:
                self._clients[source] = self._build(source).music_clients[source]
        return self._clients[source]


MANAGER = ClientManager()


# ---------------------------------------------------------------------------
# in-memory registry: token -> resolved track (so play/download need no re-search)
# ---------------------------------------------------------------------------
class TrackRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._tracks = {}

    def add(self, song_info, source):
        token = uuid.uuid4().hex[:16]
        client = MANAGER.client(source)
        headers = dict(getattr(client, 'default_download_headers', {}) or {})
        headers.update(dict(getattr(song_info, 'default_download_headers', {}) or {}))
        cookies = dict(getattr(client, 'default_download_cookies', {}) or {})
        cookies.update(dict(getattr(song_info, 'default_download_cookies', {}) or {}))
        with self._lock:
            self._tracks[token] = {
                'song_info': song_info,
                'source': source,
                'headers': headers,
                'cookies': cookies,
            }
        return token

    def get(self, token):
        with self._lock:
            return self._tracks.get(token)


REGISTRY = TrackRegistry()


class _NullProgress:
    '''A no-op stand-in for rich.Progress so we can call musicdl's `_search`
    without rendering anything to a terminal.'''
    def add_task(self, *a, **k): return 0
    def update(self, *a, **k): pass
    def advance(self, *a, **k): pass
    def __getattr__(self, _): return lambda *a, **k: None


def _track_payload(song_info, token):
    '''Serialize a SongInfo into the minimal JSON the frontend needs.'''
    def s(v):
        return '' if v is None else str(v)
    ext = s(song_info.ext).lower().lstrip('.')
    return {
        'token': token,
        'source': SUPPORTED_SOURCES.get(s(song_info.source), {}).get('short', s(song_info.source)),
        'source_label': SUPPORTED_SOURCES.get(s(song_info.source), {}).get('label', s(song_info.source)),
        'song_name': s(song_info.song_name) or '未知曲目',
        'singers': s(song_info.singers) or '未知艺人',
        'album': s(song_info.album),
        'ext': ext,
        'format': ext.upper() or '未知',
        'file_size': s(song_info.file_size),
        'duration': s(song_info.duration),
        'cover_url': s(song_info.cover_url),
        'has_lyric': bool(getattr(song_info, 'lyric', None)),
        'lossless': ext in {'flac', 'wav', 'ape', 'alac'},
    }


# ---------------------------------------------------------------------------
# streaming search: drive musicdl per result, emit each track as it resolves
# ---------------------------------------------------------------------------
def search_stream(keyword, sources):
    '''Generator yielding SSE messages. Every source runs concurrently; each
    resolved track is pushed the moment musicdl appends it to its result list.'''
    out = queue.Queue()
    seen_identifiers = set()
    seen_lock = threading.Lock()
    active = {'n': 0}

    def emit(event, data):
        out.put(f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n')

    def run_source(source):
        try:
            client = MANAGER.client(source)
            progress = _NullProgress()
            try:
                search_urls = client._constructsearchurls(keyword=keyword, rule={}, request_overrides={})
            except Exception as err:
                emit('source_error', {'source': source, 'message': str(err)})
                return
            buckets = [[] for _ in search_urls]
            threads = []
            for i, url in enumerate(search_urls):
                t = threading.Thread(
                    target=_safe_search,
                    args=(client, keyword, url, buckets[i], progress),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            deadline = time.time() + PER_SOURCE_TIMEOUT
            cursors = [0] * len(buckets)
            count = 0
            while True:
                drained = _drain(buckets, cursors, source, seen_identifiers, seen_lock, emit)
                count += drained
                alive = any(t.is_alive() for t in threads)
                if not alive or time.time() > deadline:
                    break
                time.sleep(0.12)
            # final flush of anything that landed at the very end
            count += _drain(buckets, cursors, source, seen_identifiers, seen_lock, emit)
            timed_out = any(t.is_alive() for t in threads)
            emit('source_done', {'source': source, 'count': count, 'timed_out': timed_out})
        except Exception as err:
            emit('source_error', {'source': source, 'message': str(err)})
        finally:
            with seen_lock:
                active['n'] -= 1
                if active['n'] == 0:
                    out.put(None)  # sentinel: all sources finished

    valid = [s for s in SOURCE_ORDER if s in sources]
    if not valid:
        yield 'event: done\ndata: {"count": 0}\n\n'
        return

    active['n'] = len(valid)
    for source in valid:
        emit('source_start', {'source': source,
                              'label': SUPPORTED_SOURCES[source]['label']})
        threading.Thread(target=run_source, args=(source,), daemon=True).start()

    total = 0
    while True:
        msg = out.get()
        if msg is None:
            break
        if msg.startswith('event: result'):
            total += 1
        yield msg
    yield f'event: done\ndata: {{"count": {total}}}\n\n'


def _safe_search(client, keyword, url, bucket, progress):
    try:
        client._search(keyword=keyword, search_url=url, request_overrides={},
                        song_infos=bucket, progress=progress)
    except Exception:
        pass


def _drain(buckets, cursors, source, seen, lock, emit):
    '''Emit every newly-appeared track across all page buckets; dedup by id.'''
    emitted = 0
    for i, bucket in enumerate(buckets):
        while cursors[i] < len(bucket):
            song_info = bucket[cursors[i]]
            cursors[i] += 1
            try:
                ident = str(getattr(song_info, 'identifier', None))
                with lock:
                    if ident in seen:
                        continue
                    seen.add(ident)
                token = REGISTRY.add(song_info, source)
                emit('result', _track_payload(song_info, token))
                emitted += 1
            except Exception:
                continue
    return emitted


# ---------------------------------------------------------------------------
# downloads: chunked, with live progress, using the musicdl-resolved URL
# ---------------------------------------------------------------------------
DOWNLOADS = {}
DL_LOCK = threading.Lock()
ACTIVE_DOWNLOAD_PATHS = set()


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]', '_', name or 'track').strip()
    return name[:120] or 'track'


def _embed_metadata(song):
    audio_path = Path(song.save_path)
    lyrics = SongInfoUtils.normalizetext(getattr(song, 'lyric', None))
    title = SongInfoUtils.normalizetext(getattr(song, 'song_name', None))
    album = SongInfoUtils.normalizetext(getattr(song, 'album', None))
    artists = SongInfoUtils.normalizetext(getattr(song, 'singers', None))
    cover = SongInfoUtils.normalizetext(getattr(song, 'cover_url', None))
    if lyrics:
        SongInfoUtils.safeeditaudio(audio_path, SongInfoUtils.embedlyrics,
                                    overwrite=False, lyrics_text=lyrics)
    if title or album or artists:
        SongInfoUtils.safeeditaudio(audio_path, SongInfoUtils.embedbasictags,
                                    overwrite=False, title=title, album=album, artists=artists)
    if cover and SongInfoUtils.lookslikecoversource(cover):
        SongInfoUtils.safeeditaudio(audio_path, SongInfoUtils.embedcover,
                                    overwrite=False, cover_source=cover)


def run_download(download_id, token, download_dir):
    entry = REGISTRY.get(token)
    if not entry:
        _set_dl(download_id, status='error', message='曲目已过期，请重新搜索')
        return
    song = entry['song_info']
    url = getattr(song, 'download_url', None)
    if not isinstance(url, str) or not url.startswith('http'):
        _set_dl(download_id, status='error', message='该曲目没有可用的下载地址')
        return

    os.makedirs(download_dir, exist_ok=True)
    ext = (str(song.ext) or 'mp3').lstrip('.')
    fname = f"{_safe_name(str(song.song_name))} - {_safe_name(str(song.singers))}.{ext}"
    with DL_LOCK:
        path = os.path.join(download_dir, fname)
        suffix = 2
        while os.path.exists(path) or path in ACTIVE_DOWNLOAD_PATHS:
            path = os.path.join(download_dir, f"{os.path.splitext(fname)[0]} ({suffix}).{ext}")
            suffix += 1
        ACTIVE_DOWNLOAD_PATHS.add(path)
    fname = os.path.basename(path)

    try:
        with requests.get(url, headers=entry['headers'], cookies=entry['cookies'],
                          stream=True, timeout=(10, 30), verify=False) as resp:
            resp.raise_for_status()
            total = int(float(resp.headers.get('Content-Length', 0) or 0))
            if total <= 0:
                total = int(getattr(song, 'file_size_bytes', 0) or 0)
            _set_dl(download_id, status='downloading', total=total, downloaded=0,
                    name=fname, path=path)
            done = 0
            last = time.time()
            last_bytes = 0
            tmp = path + '.part'
            with open(tmp, 'wb') as fp:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    fp.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last >= 0.25:
                        speed = (done - last_bytes) / (now - last)
                        _set_dl(download_id, downloaded=done, total=total, speed=speed)
                        last, last_bytes = now, done
            os.replace(tmp, path)
            song._save_path = path
            song.work_dir = download_dir
            try:
                _embed_metadata(song)
            except Exception:
                pass
            _set_dl(download_id, status='done', downloaded=done,
                    total=total or done, speed=0, name=fname, path=path)
    except Exception as err:
        _set_dl(download_id, status='error', message=str(err))
    finally:
        with DL_LOCK:
            ACTIVE_DOWNLOAD_PATHS.discard(path)


def _set_dl(download_id, **fields):
    with DL_LOCK:
        rec = DOWNLOADS.setdefault(download_id, {})
        rec.update(fields)
        rec['updated'] = time.time()


def _get_dl(download_id):
    with DL_LOCK:
        return dict(DOWNLOADS.get(download_id, {}))


# ---------------------------------------------------------------------------
# Flask app + routes
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=None)
requests.packages.urllib3.disable_warnings()


@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/static/<path:fname>')
def static_files(fname):
    return send_from_directory(STATIC_DIR, fname)


@app.route('/api/sources')
def api_sources():
    return jsonify([
        {'id': sid, 'label': SUPPORTED_SOURCES[sid]['label'],
         'short': SUPPORTED_SOURCES[sid]['short'], 'default': SUPPORTED_SOURCES[sid]['default']}
        for sid in SOURCE_ORDER
    ])


@app.route('/api/version')
def api_version():
    return jsonify({'version': __version__})


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        directories = data.get('download_directories', [])
        if not isinstance(directories, list):
            return jsonify({'error': '目录配置格式错误'}), 400
        normalized = []
        names = set()
        for directory in directories:
            if not isinstance(directory, dict):
                return jsonify({'error': '目录配置格式错误'}), 400
            name = directory.get('name')
            path = directory.get('path')
            if not isinstance(name, str) or not name.strip() or not isinstance(path, str) or not path.strip():
                return jsonify({'error': '目录名称和路径均不能为空'}), 400
            name = name.strip()
            path = os.path.abspath(os.path.expanduser(path.strip()))
            if not os.path.isabs(path):
                return jsonify({'error': '目录路径必须是绝对路径'}), 400
            if name in names:
                return jsonify({'error': '目录名称不能重复'}), 400
            names.add(name)
            normalized.append({'name': name, 'path': path})
        if not normalized:
            return jsonify({'error': '请至少添加一个下载目录'}), 400
        SETTINGS['download_directories'] = normalized
        try:
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as fp:
                json.dump(SETTINGS, fp, ensure_ascii=False, indent=2)
        except OSError as err:
            return jsonify({'error': f'无法保存设置: {err}'}), 500
    return jsonify(SETTINGS)


@app.route('/api/search')
def api_search():
    keyword = (request.args.get('q') or '').strip()
    raw_sources = (request.args.get('sources') or '').strip()
    sources = [s for s in raw_sources.split(',') if s in SUPPORTED_SOURCES]
    if not sources:
        sources = [s for s in SOURCE_ORDER if SUPPORTED_SOURCES[s]['default']]
    if not keyword:
        return jsonify({'error': '请输入搜索关键词'}), 400

    @stream_with_context
    def generate():
        yield 'retry: 10000\n\n'
        for msg in search_stream(keyword, sources):
            yield msg

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/stream/<token>')
def api_stream(token):
    '''Proxy the upstream audio with Range support so <audio> can seek.'''
    entry = REGISTRY.get(token)
    if not entry:
        return 'expired', 404
    song = entry['song_info']
    url = getattr(song, 'download_url', None)
    if not isinstance(url, str) or not url.startswith('http'):
        return 'no audio url', 404

    upstream_headers = dict(entry['headers'])
    range_header = request.headers.get('Range')
    if range_header:
        upstream_headers['Range'] = range_header

    try:
        up = requests.get(url, headers=upstream_headers, cookies=entry['cookies'],
                          stream=True, timeout=(10, 30), verify=False)
    except Exception as err:
        return f'upstream error: {err}', 502

    ext = (str(song.ext) or 'mp3').lstrip('.').lower()
    resp_headers = {
        'Content-Type': RESULT_EXT_TO_MIME.get(ext, 'application/octet-stream'),
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-cache',
    }
    for h in ('Content-Length', 'Content-Range'):
        if h in up.headers:
            resp_headers[h] = up.headers[h]

    def generate():
        try:
            for chunk in up.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            up.close()

    return Response(stream_with_context(generate()), status=up.status_code,
                    headers=resp_headers)


@app.route('/api/cover/<token>')
def api_cover(token):
    '''Proxy cover art (some hosts block hotlinking / need referer).'''
    entry = REGISTRY.get(token)
    if not entry:
        return '', 404
    url = getattr(entry['song_info'], 'cover_url', None)
    if not isinstance(url, str) or not url.startswith('http'):
        return '', 404
    try:
        up = requests.get(url, headers={'User-Agent': entry['headers'].get('User-Agent', 'Mozilla/5.0')},
                          timeout=(10, 20), verify=False)
        return Response(up.content, status=up.status_code,
                        headers={'Content-Type': up.headers.get('Content-Type', 'image/jpeg'),
                                 'Cache-Control': 'public, max-age=86400'})
    except Exception:
        return '', 502


@app.route('/api/lyric/<token>')
def api_lyric(token):
    entry = REGISTRY.get(token)
    if not entry:
        return jsonify({'lyric': ''})
    return jsonify({'lyric': getattr(entry['song_info'], 'lyric', '') or ''})


@app.route('/api/download', methods=['POST'])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get('token')
    entry = REGISTRY.get(token)
    if not entry:
        return jsonify({'error': '曲目已过期，请重新搜索'}), 404
    directory_name = data.get('directory_name')
    directory = next((item for item in SETTINGS['download_directories']
                      if item['name'] == directory_name), None)
    if not directory:
        return jsonify({'error': '请选择下载目录'}), 400
    download_dir = directory['path']
    download_id = uuid.uuid4().hex[:16]
    _set_dl(download_id, status='starting', downloaded=0, total=0,
            name=str(entry['song_info'].song_name))
    threading.Thread(target=run_download, args=(download_id, token, download_dir), daemon=True).start()
    return jsonify({'download_id': download_id})


@app.route('/api/download/<download_id>/progress')
def api_download_progress(download_id):
    @stream_with_context
    def generate():
        yield 'retry: 10000\n\n'
        while True:
            rec = _get_dl(download_id)
            if not rec:
                yield 'event: error\ndata: {"message":"unknown download"}\n\n'
                return
            yield f'event: progress\ndata: {json.dumps(rec, ensure_ascii=False)}\n\n'
            if rec.get('status') in ('done', 'error'):
                return
            time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/file/<download_id>')
def api_file(download_id):
    rec = _get_dl(download_id)
    if not rec or rec.get('status') != 'done' or not rec.get('path'):
        return 'not ready', 404
    path = rec['path']
    return send_from_directory(os.path.dirname(path), os.path.basename(path), as_attachment=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('MUSICDL_HOST', '127.0.0.1')
    print(f'\n  🎵  Music player running at  http://{host}:{port}\n')
    app.run(host=host, port=port, threaded=True, debug=False)

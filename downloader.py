"""萬能影片下載器 · 下載核心（yt-dlp 包裝）

負責：格式選擇、ffmpeg 定位、背景執行緒下載、進度回報、取消。
UI 端透過 emit(event, payload) 收到事件：
  progress / phase / log / done / error / cancelled
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

# 下載格式（key, 顯示名稱）
FORMATS = [
    ('best', 'MP4 影片・最高畫質'),
    ('1080', 'MP4 影片・1080p 以下'),
    ('720', 'MP4 影片・720p 以下'),
    ('mp3', 'MP3 音訊'),
]
FORMAT_KEYS = {k for k, _ in FORMATS}

# 同解析度優先挑 h264 / aac，最能相容 Windows 播放器；更高解析度仍優先（4K 不會被降成 1080p）。
_SORT = ['lang', 'quality', 'res', 'fps', 'hdr:12', 'vcodec:h264', 'channels', 'acodec:aac',
         'size', 'br', 'asr', 'proto', 'ext', 'hasaud', 'source', 'id']


def app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def ffmpeg_path() -> str | None:
    """依序尋找 ffmpeg：bin/ffmpeg.exe → imageio-ffmpeg 內建 → PATH。"""
    local = os.path.join(app_dir(), 'bin', 'ffmpeg.exe')
    if os.path.isfile(local):
        return local
    try:
        import imageio_ffmpeg  # noqa: WPS433
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return shutil.which('ffmpeg')


def ytdlp_version() -> str:
    try:
        return yt_dlp.version.__version__
    except Exception:
        return '?'


# ───────── JavaScript 執行環境（YouTube 高畫質需要） ─────────
DENO_URL = 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip'


def js_runtimes() -> dict:
    """回傳可用的 JS 執行環境設定，交給 yt-dlp 的 js_runtimes；依序：bin/deno.exe → PATH 上的 deno / node / bun。"""
    found: dict = {}
    local = os.path.join(app_dir(), 'bin', 'deno.exe')
    if os.path.isfile(local):
        found['deno'] = {'path': local}
    elif shutil.which('deno'):
        found['deno'] = {}
    for name in ('node', 'bun'):
        if shutil.which(name):
            found[name] = {}
    return found


def js_runtime_name() -> str | None:
    rt = js_runtimes()
    if not rt:
        return None
    if 'deno' in rt:
        return 'deno'
    return next(iter(rt))


def ensure_deno(log) -> str | None:
    """沒有任何 JS 執行環境時，下載官方 deno 到 bin/deno.exe。回傳路徑或 None。"""
    import io
    import urllib.request
    import zipfile

    bin_dir = os.path.join(app_dir(), 'bin')
    target = os.path.join(bin_dir, 'deno.exe')
    if os.path.isfile(target):
        return target
    os.makedirs(bin_dir, exist_ok=True)
    log(f'正在下載 deno（YouTube 解析需要）：{DENO_URL}')
    req = urllib.request.Request(DENO_URL, headers={'User-Agent': 'universal-video-downloader'})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        total = int(resp.headers.get('Content-Length') or 0)
        buf = io.BytesIO()
        got = 0
        last = 0
        while True:
            chunk = resp.read(1 << 18)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if total and got - last > (4 << 20):
                last = got
                log(f'  deno 下載中 {got / 1048576:.0f} / {total / 1048576:.0f} MB')
    buf.seek(0)
    with zipfile.ZipFile(buf) as z:
        name = next((n for n in z.namelist() if n.lower().endswith('deno.exe')), None)
        if not name:
            raise RuntimeError('deno 壓縮檔裡找不到 deno.exe')
        with z.open(name) as src, open(target + '.part', 'wb') as dst:
            shutil.copyfileobj(src, dst)
    os.replace(target + '.part', target)
    log(f'deno 已安裝：{target}')
    return target


class _Logger:
    """把 yt-dlp 的訊息轉成 UI 紀錄；濾掉進度列與除錯雜訊。"""

    def __init__(self, emit):
        self._emit = emit

    def _send(self, level, msg):
        msg = (msg or '').strip()
        if not msg:
            return
        self._emit('log', {'level': level, 'text': msg})

    def debug(self, msg):
        if msg.startswith('[debug]'):
            return
        if msg.startswith('[download]') and ('%' in msg or 'ETA' in msg):
            return
        self._send('info', msg)

    def info(self, msg):
        self._send('info', msg)

    def warning(self, msg):
        self._send('warn', msg.replace('WARNING: ', '', 1))

    def error(self, msg):
        self._send('error', msg.replace('ERROR: ', '', 1))


def _friendly(msg: str) -> tuple[str, str | None]:
    """把 yt-dlp 錯誤訊息整理成一句話 + 可能的提示。"""
    m = msg.replace('ERROR: ', '').strip()
    low = m.lower()
    hint = None
    if 'unsupported url' in low:
        hint = '這個網址不是支援的影片頁面，請確認貼的是影片／Reel／貼文本身的連結。'
    elif 'sign in' in low or 'login' in low or 'log in' in low or 'private' in low or 'cookies' in low:
        hint = '這支影片需要登入才能看（非公開）。可在 settings.json 設定 cookies_from_browser: "firefox" 後重試。'
    elif 'http error 403' in low or 'http error 429' in low:
        hint = '被網站暫時擋下（403/429），稍等幾分鐘再試，或按「更新下載核心」。'
    elif 'video unavailable' in low or 'not available' in low or 'removed' in low:
        hint = '影片已被移除、設為私人，或你所在地區無法觀看。'
    elif 'ffmpeg' in low:
        hint = '找不到 ffmpeg，請把 ffmpeg.exe 放進程式的 bin 資料夾。'
    elif 'is not a valid url' in low:
        hint = '請貼上完整網址（以 https:// 開頭）。'
    first = m.splitlines()[0] if m else '未知錯誤'
    if len(first) > 180:
        first = first[:180] + '…'
    return first, hint


class Downloader:
    def __init__(self, emit):
        self.emit = emit
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last = 0.0
        self.busy = False

    # ───────── 對外 ─────────
    def start(self, url: str, fmt: str, save_dir: str, cookies_browser: str | None = None) -> dict:
        if self.busy:
            return {'ok': False, 'error': '目前已有下載進行中。'}
        url = (url or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            return {'ok': False, 'error': '請貼上完整的影片網址。'}
        if fmt not in FORMAT_KEYS:
            fmt = 'best'
        try:
            os.makedirs(save_dir, exist_ok=True)
        except OSError as e:
            return {'ok': False, 'error': f'無法建立儲存資料夾：{e}'}
        self._cancel.clear()
        self.busy = True
        self._thread = threading.Thread(
            target=self._run, args=(url, fmt, save_dir, cookies_browser), daemon=True)
        self._thread.start()
        return {'ok': True}

    def cancel(self):
        self._cancel.set()

    # ───────── 內部 ─────────
    def _opts(self, fmt: str, save_dir: str, cookies_browser: str | None) -> dict:
        ff = ffmpeg_path()
        opts = {
            'outtmpl': {'default': '%(title).150B [%(id)s].%(ext)s'},
            'paths': {'home': save_dir},
            'noplaylist': True,
            'windowsfilenames': True,
            'quiet': True,
            'no_warnings': False,
            'noprogress': True,
            'logger': _Logger(self.emit),
            'progress_hooks': [self._hook],
            'postprocessor_hooks': [self._pp_hook],
            'retries': 5,
            'fragment_retries': 5,
            'concurrent_fragment_downloads': 4,
            'format_sort': _SORT,
            # 允許在本機 yt-dlp-ejs 版本不合時，從官方 GitHub 抓 JS 解題元件
            'remote_components': ['ejs:github'],
        }
        rt = js_runtimes()
        if rt:
            opts['js_runtimes'] = rt
        else:
            self.emit('log', {'level': 'warn',
                              'text': '未找到 JavaScript 執行環境（deno / node），YouTube 可能只剩低畫質；請按「更新下載核心」自動安裝 deno。'})
        if ff:
            opts['ffmpeg_location'] = ff
        if cookies_browser:
            opts['cookiesfrombrowser'] = (cookies_browser,)

        if fmt == 'mp3':
            opts['format'] = 'ba/b'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '0',
            }]
            return opts

        height = {'best': None, '1080': 1080, '720': 720}[fmt]
        if ff:
            if height:
                opts['format'] = (f'bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b')
            else:
                opts['format'] = 'bv*+ba/b'
            # 能放進 mp4 就用 mp4；碰到 mp4 裝不下的編碼（如 opus）就退回 mkv，避免合併失敗。
            opts['merge_output_format'] = 'mp4/mkv'
        else:
            # 沒有 ffmpeg 就只能拿「影音已合一」的單檔
            opts['format'] = f'b[height<={height}]/b' if height else 'b'
        return opts

    def _hook(self, d: dict):
        if self._cancel.is_set():
            raise DownloadCancelled('使用者取消下載')
        st = d.get('status')
        if st == 'downloading':
            t = time.time()
            if t - self._last < 0.15:
                return
            self._last = t
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            self.emit('progress', {
                'phase': 'downloading',
                'percent': (done / total * 100.0) if total else 0.0,
                'downloaded': done,
                'total': total,
                'speed': d.get('speed'),
                'eta': d.get('eta'),
                'filename': os.path.basename(d.get('filename') or ''),
            })
        elif st == 'finished':
            self.emit('progress', {'phase': 'finished_part', 'percent': 100.0})

    def _pp_hook(self, d: dict):
        if self._cancel.is_set():
            raise DownloadCancelled('使用者取消下載')
        if d.get('status') == 'started':
            name = d.get('postprocessor') or ''
            text = {
                'Merger': '下載完成，正在合併影音…',
                'ExtractAudio': '下載完成，正在轉成 MP3…',
                'VideoRemuxer': '正在轉換容器格式…',
            }.get(name, '正在處理檔案…')
            self.emit('phase', {'phase': 'processing', 'name': name, 'text': text})

    @staticmethod
    def _final_paths(info) -> list[str]:
        out: list[str] = []

        def walk(i):
            if not i:
                return
            if 'entries' in i:
                for e in i.get('entries') or []:
                    walk(e)
                return
            for rd in i.get('requested_downloads') or []:
                fp = rd.get('filepath')
                if fp:
                    out.append(fp)

        walk(info)
        return out

    def _run(self, url: str, fmt: str, save_dir: str, cookies_browser: str | None):
        try:
            self.emit('phase', {'phase': 'extracting', 'text': '正在解析影片資訊…'})
            with yt_dlp.YoutubeDL(self._opts(fmt, save_dir, cookies_browser)) as ydl:
                info = ydl.extract_info(url, download=True)
            if self._cancel.is_set():
                raise DownloadCancelled('使用者取消下載')
            paths = self._final_paths(info)
            size = 0
            for p in paths:
                try:
                    size += os.path.getsize(p)
                except OSError:
                    pass
            self.emit('done', {
                'paths': paths,
                'path': paths[-1] if paths else '',
                'title': (info or {}).get('title', ''),
                'size': size,
            })
        except DownloadCancelled:
            self.emit('cancelled', {})
        except DownloadError as e:
            msg, hint = _friendly(str(e))
            self.emit('error', {'message': msg, 'detail': str(e).replace('ERROR: ', ''), 'hint': hint})
        except Exception as e:  # noqa: BLE001
            msg, hint = _friendly(f'{type(e).__name__}: {e}')
            self.emit('error', {'message': msg, 'detail': f'{type(e).__name__}: {e}', 'hint': hint})
        finally:
            self.busy = False

"""萬能影片下載器 · 桌面視窗（pywebview + WebView2）

啟動：run.bat（第一次會自動建立 .venv 並安裝套件）
除錯：debug.bat（顯示主控台與開發者工具）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading

import webview

from downloader import (FORMATS, Downloader, app_dir, ensure_deno, ffmpeg_path, js_runtime_name,
                        ytdlp_version)

APP_NAME = '萬能影片下載器'
BASE_DIR = app_dir()
RES_DIR = getattr(sys, '_MEIPASS', BASE_DIR)
UI_FILE = os.path.join(RES_DIR, 'ui', 'index.html')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
DEBUG = '--debug' in sys.argv

_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.I)


# ───────── 設定 ─────────
def _default_save_dir() -> str:
    return os.path.join(os.path.expanduser('~'), 'Downloads', '下載影片')


def load_settings() -> dict:
    s = {'save_dir': _default_save_dir(), 'format': 'best', 'cookies_from_browser': None}
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in s})
    except (OSError, ValueError):
        pass
    if s['format'] not in {k for k, _ in FORMATS}:
        s['format'] = 'best'
    return s


def save_settings(s: dict) -> None:
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ───────── 剪貼簿（Win32，無額外套件） ─────────
def read_clipboard_text() -> str:
    if sys.platform != 'win32':
        return ''
    CF_UNICODETEXT = 13
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ''
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ''
        p = k32.GlobalLock(h)
        if not p:
            return ''
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


# ───────── JS 可呼叫的 API ─────────
class Api:
    def __init__(self):
        self._window: webview.Window | None = None
        self._settings = load_settings()
        self._dl = Downloader(emit=self.emit)
        self._updating = False

    # Python → JS
    def emit(self, event: str, payload: dict | None = None) -> None:
        if self._window is None:
            return
        js = f'window.__push({json.dumps(event)}, {json.dumps(payload or {}, ensure_ascii=True)})'
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    # JS → Python
    def get_state(self) -> dict:
        return {
            'app': APP_NAME,
            'save_dir': self._settings['save_dir'],
            'format': self._settings['format'],
            'formats': [{'key': k, 'label': v} for k, v in FORMATS],
            'ytdlp_version': ytdlp_version(),
            'ffmpeg': bool(ffmpeg_path()),
            'js_runtime': js_runtime_name(),
            'frozen': bool(getattr(sys, 'frozen', False)),
        }

    def read_clipboard(self) -> str:
        try:
            text = read_clipboard_text()
        except Exception:
            return ''
        m = _URL_RE.search(text or '')
        return m.group(0) if m else ''

    def set_format(self, key: str) -> bool:
        if key in {k for k, _ in FORMATS}:
            self._settings['format'] = key
            save_settings(self._settings)
            return True
        return False

    def choose_folder(self) -> str | None:
        if self._window is None:
            return None
        dialog = getattr(webview, 'FOLDER_DIALOG', None)
        if dialog is None:
            dialog = webview.FileDialog.FOLDER
        start = self._settings['save_dir'] if os.path.isdir(self._settings['save_dir']) else ''
        try:
            result = self._window.create_file_dialog(dialog, directory=start)
        except Exception:
            return None
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        if path:
            self._settings['save_dir'] = path
            save_settings(self._settings)
        return path

    def open_folder(self) -> bool:
        d = self._settings['save_dir']
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
            return True
        except OSError:
            return False

    def start_download(self, url: str, fmt: str) -> dict:
        if self._updating:
            return {'ok': False, 'error': '正在更新下載核心，請稍候再下載。'}
        if fmt != self._settings['format']:
            self.set_format(fmt)
        return self._dl.start(url, fmt, self._settings['save_dir'], self._settings.get('cookies_from_browser'))

    def cancel_download(self) -> bool:
        self._dl.cancel()
        return True

    def update_core(self) -> dict:
        if self._updating:
            return {'ok': False, 'error': '更新已在進行中。'}
        if self._dl.busy:
            return {'ok': False, 'error': '請先等下載完成再更新。'}
        self._updating = True
        threading.Thread(target=self._update_worker, daemon=True).start()
        return {'ok': True}

    def _log(self, text: str, level: str = 'info') -> None:
        self.emit('log', {'level': level, 'text': text})

    def _update_worker(self) -> None:
        """更新下載核心：1) pip 升級 yt-dlp（打包版略過）2) 沒有 JS 執行環境就安裝 deno。"""
        old = ytdlp_version()
        new = old
        frozen = bool(getattr(sys, 'frozen', False))
        try:
            if frozen:
                self._log('打包版無法線上升級 yt-dlp，請重新下載新版程式；仍會檢查 deno。', 'warn')
            else:
                self._log(f'目前 yt-dlp {old}，正在檢查更新…')
                cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', '--no-input',
                       '--disable-pip-version-check', 'yt-dlp[default]']
                flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', creationflags=flags)
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    if line and not line.startswith(('[notice]', 'WARNING: Cache')):
                        self._log(line)
                code = proc.wait()
                if code != 0:
                    self.emit('core_update_failed', {'message': f'pip 結束代碼 {code}，請檢查網路後再試。'})
                    return
                importlib.invalidate_caches()
                try:
                    new = importlib.metadata.version('yt-dlp')
                except importlib.metadata.PackageNotFoundError:
                    new = old

            runtime = js_runtime_name()
            if runtime:
                self._log(f'JavaScript 執行環境：{runtime}（已可解析 YouTube 全部畫質）', 'ok')
            else:
                ensure_deno(self._log)
                runtime = js_runtime_name()
            self.emit('core_updated', {'version': new, 'old': old, 'changed': new != old, 'js_runtime': runtime})
        except Exception as e:  # noqa: BLE001
            self.emit('core_update_failed', {'message': f'{type(e).__name__}: {e}'})
        finally:
            self._updating = False


def _work_area_logical() -> tuple[int, int]:
    """螢幕可用工作區（邏輯像素）。不論程序目前是否 DPI-aware，回傳值都會是邏輯尺寸。"""
    try:
        u32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        u32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        try:
            dpi = u32.GetDpiForSystem()
        except AttributeError:
            dpi = 96
        scale = (dpi or 96) / 96.0
        return int((rect.right - rect.left) / scale), int((rect.bottom - rect.top) / scale)
    except Exception:
        return 1280, 800


def main() -> None:
    api = Api()
    storage = os.path.join(os.environ.get('LOCALAPPDATA', BASE_DIR), 'UniversalVideoDownloader', 'webview')
    os.makedirs(storage, exist_ok=True)
    avail_w, avail_h = _work_area_logical()
    width = max(720, min(960, avail_w - 40))
    height = max(620, min(900, avail_h - 60))
    # 在主螢幕工作區置中（pywebview 會把邏輯座標換算成實體像素；預設的 CenterScreen 在視窗較高時會退回串接位置）
    x = max(0, (avail_w - width) // 2)
    y = max(0, (avail_h - height) // 2)
    window = webview.create_window(
        APP_NAME, UI_FILE, js_api=api,
        width=width, height=height, x=x, y=y, min_size=(700, 560),
        background_color='#e7e4de', text_select=True,
    )
    api._window = window  # noqa: SLF001
    webview.start(debug=DEBUG, private_mode=False, storage_path=storage)


if __name__ == '__main__':
    main()

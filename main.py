"""
Milwaukee Job Finder — Windows Desktop Entry Point
Runs Flask in a background thread, shows a system tray icon,
and opens the browser automatically on launch.
"""
import os
import sys
import socket
import threading
import time
import webbrowser

# ── Persistent data directory (%APPDATA%\MKEJobFinder) ───────────────────────
DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MKEJobFinder')
os.makedirs(DATA_DIR, exist_ok=True)
os.environ['MKE_DATA_DIR'] = DATA_DIR

# When bundled by PyInstaller, tell Flask where templates were extracted
if getattr(sys, 'frozen', False):
    os.environ['MKE_BASE_DIR'] = sys._MEIPASS  # type: ignore[attr-defined]

# ── Flask server ──────────────────────────────────────────────────────────────
PORT = 5000

def _run_flask():
    from app import app
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

def _wait_for_flask(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False

# ── Windows startup (registry) ────────────────────────────────────────────────
APP_NAME   = 'MKEJobFinder'
_REG_PATH  = r'Software\Microsoft\Windows\CurrentVersion\Run'

def _startup_cmd() -> str:
    """Return the registry value that launches this app on Windows startup."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

def is_in_startup() -> bool:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(k, APP_NAME)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False

def _set_startup(enabled: bool) -> None:
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE)
    if enabled:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _startup_cmd())
    else:
        try:
            winreg.DeleteValue(k, APP_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(k)

def _toggle_startup(_icon, _item) -> None:
    _set_startup(not is_in_startup())

# ── Tray icon image ───────────────────────────────────────────────────────────
def _make_icon_image(size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    m   = size // 10
    # Blue rounded background
    d.rounded_rectangle([m, m, size - m, size - m], radius=size // 5, fill=(37, 99, 235))
    # White briefcase body
    bx  = size // 5
    by  = size * 2 // 5
    bw  = size * 3 // 5
    bh  = size * 2 // 5
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=3, fill=(255, 255, 255))
    # Handle
    hx = size * 3 // 8
    hy = by - size // 7
    d.rectangle([hx, hy, size - hx, by], outline=(255, 255, 255), width=max(2, size // 20))
    # Centre divider line
    d.line([(bx + 2, by + bh // 2), (bx + bw - 2, by + bh // 2)],
           fill=(37, 99, 235), width=max(2, size // 20))
    return img

# ── Tray actions ──────────────────────────────────────────────────────────────
def _open_browser():
    webbrowser.open(f'http://127.0.0.1:{PORT}')

def _run_search_now():
    """Trigger a scheduled search from the tray menu."""
    try:
        import requests as req
        req.post(f'http://127.0.0.1:{PORT}/api/run-now', timeout=5)
    except Exception as exc:
        print(f'[tray] run-now failed: {exc}')

def _quit(icon, _item):
    icon.stop()

# ── System tray ───────────────────────────────────────────────────────────────
def _run_tray():
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem('Open Milwaukee Job Finder', lambda i, it: _open_browser(), default=True),
        pystray.MenuItem('Run Search Now',            lambda i, it: threading.Thread(
                                                          target=_run_search_now, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Start with Windows', _toggle_startup,
                         checked=lambda _item: is_in_startup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit', _quit),
    )
    icon = pystray.Icon(APP_NAME, _make_icon_image(), 'Milwaukee Job Finder', menu)
    icon.run()

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    # Launch Flask in a background daemon thread
    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    # Open the browser once Flask is ready
    if _wait_for_flask():
        _open_browser()
    else:
        print('[startup] Warning: Flask server did not respond in time — opening browser anyway')
        _open_browser()

    # Block on the system tray (exits when user clicks "Exit")
    _run_tray()

if __name__ == '__main__':
    main()

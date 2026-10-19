"""Start YOUSEF AI:  python -m yousef  [--port 8765] [--no-browser]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler

from . import APP_TITLE, __version__
from .app import build_app
from .server import make_server


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _running_version(port: int) -> str | None:
    """The version of a YOUSEF AI already serving this port, or None."""
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/api/status", timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8")).get("version")
    except Exception:
        return None


def _ask_to_stop(port: int) -> bool:
    """Ask an older copy (0.6.1 and later) to stop, so the new one can take the port."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/system/stop", data=b"{}", method="POST",
                                 headers={"X-Yousef-Client": "1", "Content-Type": "application/json"})
    try:
        with _LOCAL.open(req, timeout=3):
            pass
    except Exception:
        return False
    for _ in range(20):
        time.sleep(0.25)
        if _running_version(port) is None:
            return True
    return False


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="yousef", description=APP_TITLE)
    parser.add_argument("--port", type=int, help="port for the local dashboard")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)

    app = build_app()
    log_file = app.data_dir / "yousef.log"
    handlers = [logging.StreamHandler(), RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)

    port = args.port or app.settings.get()["port"]
    url = f"http://127.0.0.1:{port}/"
    try:
        server = make_server(app, port)
    except OSError:
        running = _running_version(port)
        if running == __version__:
            app.close()
            print(f"{APP_TITLE} is already running. Opening {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        if running is None:
            app.close()
            print(f"Port {port} is busy. Start with another port:  start.bat --port 8780")
            return 1
        # an older copy (from the previous folder) still holds the port: the browser would show the old version
        print(f"  An older {APP_TITLE} (v{running}) is still running; this is v{__version__}.")
        server = None
        if _ask_to_stop(port):
            for _ in range(20):
                try:
                    server = make_server(app, port)
                    break
                except OSError:
                    time.sleep(0.25)
        if server is not None:
            print("  Stopped it. Starting the new version...")
        else:
            app.close()
            print("  Close the window of the old version (or press Ctrl+C in it), then run start.bat again.")
            print(f"  توجد نسخة أقدم من يوسف (v{running}) تعمل الآن. أغلق نافذتها السوداء، ثم شغّل start.bat من جديد.")
            return 1

    print(f"\n  {APP_TITLE} v{__version__}")
    print(f"  Dashboard: {url}")
    print(f"  Data:      {app.data_dir}")
    if not app.keystore.secure:
        print("  Note: API keys are only base64-encoded on this OS (DPAPI encryption is Windows-only).")
    print("  Close this window or press Ctrl+C to stop.")

    app.publisher.start()  # publishes Instagram posts scheduled for later
    if app.voice.installed:
        if app.settings.get()["voice"]["preload"]:
            app.voice.load()
            print("  Voice: loading the speech model in the background.")
    else:
        print("  Voice: not installed (run install-voice.bat once to enable it).")
    print()

    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        print(f"{APP_TITLE} stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

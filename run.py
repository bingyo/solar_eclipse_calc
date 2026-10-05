"""Start the eclipse calculator web UI.

    python run.py            # http://127.0.0.1:8765 をブラウザで開く
    python run.py --port 9000 --no-browser

If the port is already used by a running instance of this tool, the browser
is simply opened on it; if it is used by another program, the next free
port is chosen automatically.

``--app`` is used by the macOS app (日食計算機.app), which runs this without a console
window: errors are shown in a dialog and the page gets a "終了" button.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

import uvicorn


def _port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _is_running_here(host, port):
    """True if this calculator already answers on host:port."""
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/api/info', timeout=2) as r:
            info = json.loads(r.read().decode('utf-8'))
        return 'ephemerides' in info and 'version' in info
    except Exception:
        return False


def _open_when_ready(host, port, url, timeout=30.0):
    def worker():
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.3)
        webbrowser.open(url)
    threading.Thread(target=worker, daemon=True).start()


def _alert(message):
    """Show an error dialog on macOS (the app has no console window)."""
    try:
        subprocess.run(['osascript', '-e', 'on run argv', '-e',
                        'display alert "日食計算機" message (item 1 of argv) as critical',
                        '-e', 'end run', message], timeout=3600)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description='日食・太陽面通過 精密計算機')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--app', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass
    if not args.app:
        return _run(args, lambda message: print(message, flush=True))

    def fail(message):
        print(message, flush=True)
        _alert(message)

    os.environ['ECLIPSECALC_APP'] = '1'  # read by eclipsecalc.server
    try:
        return _run(args, fail)
    except Exception:
        traceback.print_exc()
        fail('予期しないエラーで終了しました。詳しくはログ '
             f'{os.environ.get("ECLIPSECALC_LOG", "")} をご覧ください。')
        return 1


def _run(args, fail):
    host, port = args.host, args.port
    if not _port_free(host, port):
        if _is_running_here(host, port):
            url = f'http://{host}:{port}/'
            print(f'日食・太陽面通過 精密計算機はすでに {url} で起動しています。', flush=True)
            if not args.no_browser:
                print('ブラウザで開きます。', flush=True)
                webbrowser.open(url)
            return 0
        for cand in range(port + 1, port + 50):
            if _port_free(host, cand):
                print(f'ポート {port} は別のプログラムが使用中のため、ポート {cand} で起動します。', flush=True)
                port = cand
                break
        else:
            fail(f'ポート {port}〜{port + 49} がすべて使用中です。--port で空いているポートを指定してください。')
            return 1

    from eclipsecalc.context import DATA_DIR, ensure_ephemeris
    try:
        ensure_ephemeris(log=lambda m: print(m, flush=True))
    except Exception as exc:
        fail(f'JPL 暦をダウンロードできませんでした: {exc}\n'
             f'インターネット接続を確認するか、de440s.bsp を手動で {DATA_DIR} に置いてください。')
        return 1

    url = f'http://{host}:{port}/'
    if args.no_browser:
        how_to_quit = 'Ctrl+C で終了'
    else:
        # the browser was opened for the user, so closing it ends the calculator too
        os.environ['ECLIPSECALC_QUIT_WHEN_CLOSED'] = '1'  # read by eclipsecalc.server
        how_to_quit = ('ブラウザの画面を閉じるか右上の「終了」で終了' if args.app else
                       'ブラウザの画面をすべて閉じると、このウィンドウも閉じて終了します')
    from eclipsecalc import server as web
    server = uvicorn.Server(uvicorn.Config(web.app, host=host, port=port, log_level='warning',
                                           timeout_graceful_shutdown=2))
    web.request_quit = lambda: setattr(server, 'should_exit', True)
    if not args.no_browser:
        _open_when_ready(host, port, url)
    print(f'日食・太陽面通過 精密計算機: {url}  ({how_to_quit})', flush=True)
    server.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())

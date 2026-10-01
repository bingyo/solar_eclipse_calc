"""Start the eclipse calculator web UI.

    python run.py            # http://127.0.0.1:8765 をブラウザで開く
    python run.py --port 9000 --no-browser

If the port is already used by a running instance of this tool, the browser
is simply opened on it; if it is used by another program, the next free
port is chosen automatically.
"""
import argparse
import json
import socket
import sys
import threading
import time
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


def main():
    ap = argparse.ArgumentParser(description='日食・太陽面通過 精密計算機')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass

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
            print(f'ポート {port}〜{port + 49} がすべて使用中です。--port で空いているポートを指定してください。', flush=True)
            return 1

    from eclipsecalc.context import ensure_ephemeris
    try:
        ensure_ephemeris(log=lambda m: print(m, flush=True))
    except Exception as exc:
        print(f'JPL 暦をダウンロードできませんでした: {exc}\n'
              'インターネット接続を確認するか、de440s.bsp を手動で data/ フォルダに置いてください。', flush=True)
        return 1

    url = f'http://{host}:{port}/'
    if not args.no_browser:
        _open_when_ready(host, port, url)
    print(f'日食・太陽面通過 精密計算機: {url}  (このウィンドウを閉じるか Ctrl+C で終了)', flush=True)
    uvicorn.run('eclipsecalc.server:app', host=host, port=port, log_level='warning')
    return 0


if __name__ == '__main__':
    sys.exit(main())

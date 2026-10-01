"""HTTP helpers shared by the TLE / Horizons / ephemeris downloads."""
import os
import urllib.request


def ssl_context():
    """Verify TLS with the operating system's certificate store when the
    optional ``truststore`` package is installed (needed behind corporate
    TLS inspection); otherwise use Python's default context."""
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None


def urlopen(url, timeout, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'eclipsecalc/1.0', **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def download_file(url, path, log=print):
    """Download ``url`` to ``path`` atomically (via a .part file)."""
    part = str(path) + '.part'
    with urlopen(url, 120) as resp, open(part, 'wb') as out:
        total = int(resp.headers.get('Content-Length') or 0)
        done, next_report = 0, 0.1
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total and done / total >= next_report:
                log(f'  {done / total:4.0%}  ({done / 1e6:.1f} / {total / 1e6:.1f} MB)')
                next_report += 0.1
    if total and os.path.getsize(part) != total:
        os.remove(part)
        raise IOError(f'ダウンロードが途中で切れました（{done} / {total} バイト）')
    os.replace(part, path)

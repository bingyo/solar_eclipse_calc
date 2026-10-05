"""Build ready-to-run packages that already contain Python, the libraries and the JPL ephemeris.

    python tools/build_bundles.py                                  # Windows (x64) と macOS
    python tools/build_bundles.py --target macos --sign signing.json   # Developer ID 署名＋公証
    python tools/build_bundles.py --target linux-x86_64

Windows / Linux: a ZIP of the tool folder; start.bat / start.command find the bundled Python
in .runtime/python-<os>-<arch>/ and run the tool with it.

macOS: a ZIP of 日食計算機.app (Apple silicon + Intel).  With ``--sign`` it is signed with a
Developer ID certificate and notarized by Apple, so it opens with a double-click; without it the
app is only ad-hoc signed (users must allow it in System Settings → Privacy & Security).
See packaging/macos/signing.example.json and the README for the signing set-up.

The build runs on any OS: the target platform's wheels are installed with
``uv pip install --python-platform``, the macOS launcher is compiled with zig (PyPI ``ziglang``)
and signing / notarization use rcodesign (https://github.com/indygreg/apple-platform-rs).
Needs uv (https://github.com/astral-sh/uv) on PATH, or ``--uv``, and git.

Output: dist/solar_eclipse_calc-<version>-<target>.zip
"""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from eclipsecalc import __version__  # noqa: E402
from eclipsecalc.context import DEFAULT_EPHEMERIS, ensure_ephemeris  # noqa: E402
from eclipsecalc.net import download_file  # noqa: E402

# python-build-standalone (the CPython builds uv uses); checksums from the release's SHA256SUMS
PYTHON_VERSION, PYTHON_BUILD = '3.12.14', '20260929'
PYTHON_MINOR = PYTHON_VERSION.rsplit('.', 1)[0]
PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/'
              '{build}/cpython-{version}%2B{build}-{triple}-install_only_stripped.tar.gz')
RUNTIMES = {
    # name (python-<name>/): (target triple, SHA-256 of the archive)
    'windows-x86_64': ('x86_64-pc-windows-msvc', 'f38e68f4d612ade6dd50c894fc80b14c0be0c3b5201145d6fff5f20b9323204d'),
    'macos-aarch64': ('aarch64-apple-darwin', '1bb3e53d231ee2c8881e8daf6426f4dd95bff0dda496af0f3af300357aa998d0'),
    'macos-x86_64': ('x86_64-apple-darwin', '62891cf4a32ed18b6b4174def999184b5186371422c52dd92d941401db0f0e41'),
    'linux-x86_64': ('x86_64-unknown-linux-gnu', 'ef605200f8174e87ecfc308e52a88127543f85dd5c940dc5e92cab244b98a003'),
}
TARGETS = {
    # package: (launcher, runtimes); no launcher = the macOS app
    'windows-x64': ('start.bat', ['windows-x86_64']),
    'macos': (None, ['macos-aarch64', 'macos-x86_64']),
    'linux-x86_64': ('start.command', ['linux-x86_64']),
}
DEFAULT_TARGETS = ['windows-x64', 'macos']
LAUNCHERS = {'start.bat', 'start.command'}

MACOS_APP_NAME = '日食計算機'
MACOS_MIN_VERSION = '11.0'
MACOS_DEFAULT_BUNDLE_ID = 'local.solareclipsecalc'
ZIGLANG = 'ziglang==0.16.0'
RCODESIGN_VERSION = '0.29.0'
RCODESIGN_URL = ('https://github.com/indygreg/apple-platform-rs/releases/download/'
                 'apple-codesign/{version}/apple-codesign-{version}-{suffix}')
RCODESIGN = {
    # build host: (archive suffix, SHA-256)
    'win32': ('x86_64-pc-windows-msvc.zip', '54bb500e2da7a8de02fcae0f331d1cac6e6d7173b4281042ff9c528ba3159aaa'),
    'darwin': ('macos-universal.tar.gz', 'd98372d5524226ccf9dc0eda03d4e4f5826182dabb2fc3f2bd303ed9113a748d'),
    'linux': ('x86_64-unknown-linux-musl.tar.gz', 'dbe85cedd8ee4217b64e9a0e4c2aef92ab8bcaaa41f20bde99781ff02e600002'),
}

# Parts of the Python distribution the tool never uses (GUI, pip, headers, docs)
PYTHON_EXCLUDE = (
    'include/', 'share/', 'Scripts/', 'tcl/', 'lib/tcl', 'lib/tk', 'lib/itcl', 'lib/thread',
    'lib/libtcl', 'lib/pkgconfig/', 'DLLs/tcl', 'DLLs/tk', 'DLLs/_tkinter',
    'bin/2to3', 'bin/idle', 'bin/pip', 'bin/pydoc', 'bin/python3.12-config',
)
STDLIB_EXCLUDE = ('site-packages/pip', 'ensurepip/', 'idlelib/', 'tkinter/', 'turtledemo/', 'lib-dynload/_tkinter')


def log(msg):
    print(msg, flush=True)


def find_uv(arg):
    if arg:
        return arg
    found = shutil.which('uv')
    if found:
        return found
    for exe in sorted(ROOT.glob('.runtime/uv-*/uv.exe')) + sorted(ROOT.glob('.runtime/uv-*/uv')):
        return str(exe)
    sys.exit('uv が見つかりません。https://github.com/astral-sh/uv をインストールするか --uv で指定してください。')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, path, digest):
    """Download ``url`` to ``path`` (cached) and check its SHA-256."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log(f'ダウンロードしています: {url}')
        download_file(url, path, log)
    if sha256(path) != digest:
        path.unlink()
        sys.exit(f'{path.name} のチェックサムが一致しません。もう一度実行してください。')
    return path


def python_archive(name, cache):
    triple, digest = RUNTIMES[name]
    url = PYTHON_URL.format(version=PYTHON_VERSION, build=PYTHON_BUILD, triple=triple)
    return fetch(url, cache / f'cpython-{PYTHON_VERSION}+{PYTHON_BUILD}-{triple}.tar.gz', digest)


def compile_bytecode(uv, path):
    """Precompile with a CPython of the same minor version (bytecode is platform independent).
    "unchecked-hash" .pyc files stay valid even though unzipping changes the source timestamps,
    so the first launch does not spend ~15 s compiling."""
    subprocess.run([uv, 'run', '--no-project', '--python', PYTHON_MINOR, '--', 'python', '-m', 'compileall',
                    '-qq', '-j', '0', '--invalidation-mode', 'unchecked-hash', str(path)], check=False)


def stage_runtime(uv, name, archive, stage):
    """Unpack the Python distribution, install the libraries into it and precompile every
    module.  Returns {path relative to ``stage``: permission bits} for the ZIP."""
    triple = RUNTIMES[name][0]
    stdlib = 'Lib/' if name.startswith('windows') else f'lib/python{PYTHON_MINOR}/'
    if stage.exists():
        shutil.rmtree(stage)
    modes = {}
    with tarfile.open(archive) as tf:
        for m in tf:
            # Only the version aliases (python3 -> python3.12 ...) are symlinks; the launchers
            # call python3.12 directly, so the ZIP needs no symlinks.
            if not m.isfile() or not m.name.startswith('python/'):
                continue
            rel = m.name[len('python/'):]
            if (rel.startswith(PYTHON_EXCLUDE) or '__pycache__' in rel
                    or rel.startswith(stdlib) and rel[len(stdlib):].startswith(STDLIB_EXCLUDE)):
                continue
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(m) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out)
            modes[rel] = m.mode & 0o777

    log(f'ライブラリ（{triple}）をインストールしています')
    site = stage / stdlib / 'site-packages'
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MACOS_MIN_VERSION)
    env.pop('VIRTUAL_ENV', None)
    subprocess.run([uv, 'pip', 'install', '--quiet', '--target', str(site), '--python-platform', triple,
                    '--python-version', PYTHON_MINOR, '--only-binary', ':all:',
                    '-r', str(ROOT / 'requirements.txt')], check=True, env=env)
    # console scripts (made for this machine) and the libraries' own test suites are not needed
    for d in [site / 'bin'] + sorted(site.glob('**/tests'), reverse=True):
        if d.is_dir():
            shutil.rmtree(d)
    log('バイトコードを事前にコンパイルしています')
    compile_bytecode(uv, stage / stdlib)
    return modes


def app_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
    names = [n for n in out.decode('utf-8').split('\0') if n]
    return [n for n in names if not n.startswith(('.', 'packaging/')) and n != 'data/.gitkeep'] + \
        [f'data/{DEFAULT_EPHEMERIS}']


class Bundle:
    """ZIP whose entries carry Unix permission bits (honoured by macOS Archive Utility and unzip)."""

    def __init__(self, path, top):
        self.zf = zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED)
        self.top = top
        self.count = 0
        self.longest = ''

    def add_file(self, arcname, path, mode=0o644):
        path = Path(path)
        mtime = max(path.stat().st_mtime, 315619200)  # ZIP cannot store dates before 1980
        info = zipfile.ZipInfo(f'{self.top}/{arcname}', time.localtime(mtime)[:6])
        info.create_system = 3  # Unix, so that the permission bits below are honoured
        info.external_attr = (stat.S_IFREG | mode) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        self.zf.writestr(info, path.read_bytes())
        self.count += 1
        if len(arcname) > len(self.longest):
            self.longest = arcname

    def add_tree(self, prefix, directory, modes):
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                rel = path.relative_to(directory).as_posix()
                mode = modes.get(rel, 0o755 if path.suffix in ('.so', '.dylib') else 0o644)
                self.add_file(f'{prefix}{rel}', path, mode)

    def close(self):
        self.zf.close()


def build_folder(target, uv, out, work):
    """Windows / Linux: the tool folder with the bundled Python in .runtime/."""
    launcher, runtimes = TARGETS[target]
    stages = {}
    for name in runtimes:
        stage = work / 'stage' / name
        stages[name] = (stage, stage_runtime(uv, name, python_archive(name, work / 'cache'), stage))
    log(f'{out.name} を作成しています')
    bundle = Bundle(out.with_suffix('.zip.part'), 'solar_eclipse_calc')
    for name in app_files():
        if name not in LAUNCHERS or name == launcher:
            bundle.add_file(name, ROOT / name, 0o755 if name in LAUNCHERS else 0o644)
    for name, (stage, modes) in stages.items():
        bundle.add_tree(f'.runtime/python-{name}/', stage, modes)
    return bundle


# ---------------------------------------------------------------------------------------------
# macOS app

def rcodesign(work):
    suffix, digest = RCODESIGN[sys.platform if sys.platform in RCODESIGN else 'linux']
    archive = fetch(RCODESIGN_URL.format(version=RCODESIGN_VERSION, suffix=suffix),
                    work / 'cache' / f'apple-codesign-{RCODESIGN_VERSION}-{suffix}', digest)
    dest = work / 'cache' / f'rcodesign-{RCODESIGN_VERSION}'
    exe = dest / ('rcodesign.exe' if sys.platform == 'win32' else 'rcodesign')
    if not exe.exists():
        if suffix.endswith('.zip'):
            with zipfile.ZipFile(archive) as zf:
                data = zf.read(next(n for n in zf.namelist() if n.endswith('/' + exe.name)))
        else:
            with tarfile.open(archive) as tf:
                data = tf.extractfile(next(m for m in tf if m.name.endswith('/' + exe.name))).read()
        dest.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(data)
        exe.chmod(0o755)
    return str(exe)


def compile_launcher(uv, codesign, work):
    """Contents/MacOS/launcher (arm64 + x86_64): Apple requires a Mach-O main executable."""
    slices = []
    for arch in ('aarch64', 'x86_64'):
        out = work / f'launcher-{arch}'
        subprocess.run([uv, 'run', '--no-project', '--with', ZIGLANG, '--', 'python', '-m', 'ziglang', 'cc',
                        '-target', f'{arch}-macos.{MACOS_MIN_VERSION}', '-Os',
                        '-Wl,-headerpad_max_install_names',  # room for the code signature load command
                        '-o', str(out),
                        str(ROOT / 'packaging' / 'macos' / 'launcher.c')], check=True)
        slices.append(str(out))
    universal = work / 'launcher'
    subprocess.run([codesign, 'macho-universal-create', '--output', str(universal), *slices],
                   check=True, capture_output=True)
    return universal


def _png(rgba):
    h, w, _ = rgba.shape
    raw = b''.join(b'\0' + rgba[i].tobytes() for i in range(h))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def make_icns(path):
    """App icon: a total solar eclipse (dark Moon with the corona) on the macOS rounded square."""
    n = 1024
    y, x = np.mgrid[0:n, 0:n] + 0.5
    c = n / 2
    half, corner = 412, 185  # macOS icon grid: 824 px square with rounded corners
    dx = np.maximum(np.abs(x - c) - (half - corner), 0)
    dy = np.maximum(np.abs(y - c) - (half - corner), 0)
    alpha = np.clip(corner - np.hypot(dx, dy) + 0.5, 0, 1)
    t = ((y - c) / (2 * half) + 0.5)[..., None]
    rgb = np.array([12, 20, 44]) * (1 - t) + np.array([30, 46, 88]) * t
    r = np.hypot(x - c, y - c)
    moon_r = 210
    glow = np.where(r > moon_r, 0.85 * np.exp(-(r - moon_r) / 75) + 0.6 * np.exp(-(r - moon_r) / 14), 0)
    glow = np.clip(glow, 0, 1)[..., None]
    rgb = rgb * (1 - glow) + np.array([255, 222, 160]) * glow
    moon = np.clip(moon_r - r + 0.5, 0, 1)[..., None]
    rgb = rgb * (1 - moon) + np.array([6, 8, 14]) * moon
    img = np.dstack([rgb, alpha[..., None] * 255]).astype(np.float64)

    entries = b''
    for kind, size in ((b'icp4', 16), (b'icp5', 32), (b'icp6', 64), (b'ic07', 128), (b'ic08', 256),
                       (b'ic09', 512), (b'ic10', 1024), (b'ic11', 32), (b'ic12', 64), (b'ic13', 256),
                       (b'ic14', 512)):
        f = n // size
        small = img.reshape(size, f, size, f, 4).mean(axis=(1, 3))
        data = _png(np.clip(small + 0.5, 0, 255).astype(np.uint8))
        entries += kind + struct.pack('>I', len(data) + 8) + data
    path.write_bytes(b'icns' + struct.pack('>I', len(entries) + 8) + entries)


def load_signing(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    base = Path(path).resolve().parent
    need = ('p12_file', 'p12_password_file', 'api_issuer_id', 'api_key_id', 'api_private_key_file')
    missing = [k for k in need if not cfg.get(k)]
    if missing:
        sys.exit(f'{path} に {", ".join(missing)} がありません（packaging/macos/signing.example.json を参照）')
    for k in ('p12_file', 'p12_password_file', 'api_private_key_file'):
        cfg[k] = str((base / cfg[k]).resolve())
        if not Path(cfg[k]).is_file():
            sys.exit(f'{cfg[k]} が見つかりません')
    return cfg


def _is_macho(path):
    with open(path, 'rb') as f:
        return f.read(4) in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


def sign_and_notarize(codesign, app, work, signing, notarize):
    entitlements = work / 'entitlements.plist'
    # ctypes (used by truststore) may need writable+executable memory under the hardened runtime
    entitlements.write_bytes(plistlib.dumps({'com.apple.security.cs.allow-unsigned-executable-memory': True}))
    cmd = [codesign, 'sign']
    if signing:
        cmd += ['--p12-file', signing['p12_file'], '--p12-password-file', signing['p12_password_file'],
                '--for-notarization']
        # Notarization needs the hardened runtime on every Mach-O; an unscoped flag only reaches
        # the main executable, not the Python binaries in Contents/Resources
        for path in sorted(app.rglob('*')):
            if path.is_file() and _is_macho(path):
                cmd += ['--code-signature-flags', f'{os.path.relpath(path, app)}:runtime']
    for arch in ('aarch64', 'x86_64'):
        # scoped setting "<path in the bundle>:<file>": rcodesign matches the path with the OS
        # separator, and the file is given relative to cwd so that it has no drive-letter colon
        python = os.path.join('Contents', 'Resources', f'python-macos-{arch}', 'bin', f'python{PYTHON_MINOR}')
        cmd += ['--entitlements-xml-file', f'{python}:{entitlements.name}']
    log('Developer ID で署名しています' if signing else 'アドホック署名しています（Developer ID なし）')
    result = subprocess.run(cmd + [str(app)], cwd=work, capture_output=True, text=True, errors='replace')
    if result.returncode:
        print(result.stderr[-3000:], file=sys.stderr)
        sys.exit('署名できませんでした。\n'
                 '・"not signed by Apple" / "Developer ID" と出る場合: '
                 '「Developer ID Application」証明書の .p12 を指定してください。\n'
                 '・そのほかは docs/macos_signing.md の「うまくいかないとき」を参照してください。')
    if not (signing and notarize):
        return
    log('Apple の公証を受けています（数分かかります）')
    key_json = work / 'app-store-connect-api-key.json'
    try:
        subprocess.run([codesign, 'encode-app-store-connect-api-key', '--output-path', str(key_json),
                        signing['api_issuer_id'], signing['api_key_id'], signing['api_private_key_file']],
                       check=True, stdout=subprocess.DEVNULL)
        subprocess.run([codesign, 'notary-submit', '--api-key-file', str(key_json), '--staple', str(app)],
                       check=True)
    finally:
        key_json.unlink(missing_ok=True)


def check_certificate(codesign, signing):
    """Stop before the long build if the .p12 cannot be opened."""
    result = subprocess.run([codesign, 'analyze-certificate', '--p12-file', signing['p12_file'],
                             '--p12-password-file', signing['p12_password_file']],
                            capture_output=True, text=True, errors='replace')
    if result.returncode == 0:
        return
    print((result.stdout + result.stderr)[-2000:], file=sys.stderr)
    if 'incorrect password' in result.stdout + result.stderr:
        sys.exit(f'{signing["p12_password_file"]} のパスワードで {signing["p12_file"]} を開けません。\n'
                 '.p12 を書き出したときに設定したパスワード（Mac のログインパスワードではありません）を書いてください。\n'
                 'パスワードが正しいのにこのエラーが出る場合は、docs/macos_signing.md の「うまくいかないとき」を参照してください。')
    sys.exit(f'{signing["p12_file"]} を読み込めませんでした。')


def build_macos_app(target, uv, out, work, signing, notarize):
    """macOS: 日食計算機.app with the tool and both Pythons in Contents/Resources."""
    codesign = rcodesign(work)
    if signing:
        check_certificate(codesign, signing)
    stages = {}
    for name in TARGETS[target][1]:
        stage = work / 'stage' / name
        stages[name] = (stage, stage_runtime(uv, name, python_archive(name, work / 'cache'), stage))

    log(f'{MACOS_APP_NAME}.app を作成しています')
    app = work / 'macos' / f'{MACOS_APP_NAME}.app'
    if app.parent.exists():
        shutil.rmtree(app.parent)
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    res = contents / 'Resources'
    modes = {'Contents/MacOS/launcher': 0o755, 'Contents/Resources/launch.sh': 0o755}
    shutil.copy(compile_launcher(uv, codesign, work), contents / 'MacOS' / 'launcher')
    shutil.copytree(ROOT / 'packaging' / 'macos', res, ignore=shutil.ignore_patterns('*.c', '*.json'))
    make_icns(res / 'AppIcon.icns')
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleDevelopmentRegion': 'ja',
        'CFBundleDisplayName': MACOS_APP_NAME,
        'CFBundleExecutable': 'launcher',
        'CFBundleIconFile': 'AppIcon',
        'CFBundleIdentifier': (signing or {}).get('bundle_id') or MACOS_DEFAULT_BUNDLE_ID,
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleName': MACOS_APP_NAME,
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': __version__,
        'CFBundleVersion': __version__,
        'LSApplicationCategoryType': 'public.app-category.education',
        'LSMinimumSystemVersion': MACOS_MIN_VERSION,
        'LSUIElement': True,  # no Dock icon: the launcher starts the server and exits
    }))
    for name in app_files():
        if name == 'run.py' or name.startswith(('eclipsecalc/', 'static/', 'data/')):
            (res / 'app' / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, res / 'app' / name)
    compile_bytecode(uv, res / 'app' / 'eclipsecalc')  # the app runs Python with -B (bundle is read-only)
    for name, (stage, stage_modes) in stages.items():
        shutil.copytree(stage, res / f'python-{name}')
        modes.update({f'Contents/Resources/python-{name}/{k}': v for k, v in stage_modes.items()})

    sign_and_notarize(codesign, app, work, signing, notarize)
    bundle = Bundle(out.with_suffix('.zip.part'), app.name)
    bundle.add_tree('', app, modes)
    return bundle


def main():
    ap = argparse.ArgumentParser(description='Python・ライブラリ・JPL 暦を同梱した配布用 ZIP を作成します')
    ap.add_argument('--target', action='append', choices=sorted(TARGETS),
                    help='作成するパッケージ（複数指定可。省略時は windows-x64 と macos）')
    ap.add_argument('--sign', metavar='JSON',
                    help='macOS 版を Developer ID で署名して公証する設定（packaging/macos/signing.example.json 参照）')
    ap.add_argument('--no-notarize', action='store_true', help='--sign で署名だけ行い、公証を省く（確認用）')
    ap.add_argument('--uv', help='uv の実行ファイル（省略時は PATH から探します）')
    ap.add_argument('--out', default=str(ROOT / 'dist'), help='出力先フォルダ（既定: dist/）')
    args = ap.parse_args()
    uv = find_uv(args.uv)
    signing = load_signing(args.sign) if args.sign else None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'build'
    ensure_ephemeris(log=log)
    for target in args.target or DEFAULT_TARGETS:
        out = out_dir / f'solar_eclipse_calc-{__version__}-{target}.zip'
        if TARGETS[target][0]:
            bundle = build_folder(target, uv, out, work)
        else:
            bundle = build_macos_app(target, uv, out, work, signing, not args.no_notarize)
        bundle.close()
        os.replace(out.with_suffix('.zip.part'), out)
        log(f'  {out}  {out.stat().st_size / 1e6:.0f} MB・{bundle.count} ファイル'
            f'（最長のパス {len(bundle.top) + 1 + len(bundle.longest)} 文字）')
        if target == 'macos' and not (signing and not args.no_notarize):
            log('  ※ 公証していないため、Mac では「プライバシーとセキュリティ」での許可が必要です')
    return 0


if __name__ == '__main__':
    sys.exit(main())

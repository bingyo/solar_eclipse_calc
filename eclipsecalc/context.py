"""Ephemeris / timescale context shared by all computations."""
import os
import threading
from pathlib import Path

from skyfield.api import Loader

from .i18n import tr

ROOT_DIR = Path(__file__).resolve().parent.parent
BUNDLED_DATA_DIR = ROOT_DIR / 'data'
# Downloads and caches go here.  The macOS app keeps the tool inside its signed (read-only)
# bundle and points this at ~/Library/Application Support/SolarEclipseCalc instead.
DATA_DIR = Path(os.environ.get('ECLIPSECALC_DATA_DIR') or BUNDLED_DATA_DIR)
CACHE_DIR = DATA_DIR / 'cache'

DEFAULT_EPHEMERIS = 'de440s.bsp'

_contexts = {}
_contexts_lock = threading.Lock()


JPL_EPHEMERIS_URL = 'https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/'
DOWNLOADABLE = {'de440s.bsp': '1849〜2150年・約 32 MB', 'de440.bsp': '1550〜2650年・約 114 MB'}


def _ephemeris_path(name):
    for directory in (DATA_DIR, BUNDLED_DATA_DIR):
        if (directory / name).is_file():
            return directory / name
    return None


def available_ephemerides():
    return sorted({p.name for d in (DATA_DIR, BUNDLED_DATA_DIR) for p in d.glob('*.bsp')})


def ensure_ephemeris(name=DEFAULT_EPHEMERIS, log=print):
    """Download a JPL ephemeris into data/ if it is not there yet."""
    from .net import download_file
    found = _ephemeris_path(name)
    if found:
        return found
    path = DATA_DIR / name
    if name not in DOWNLOADABLE:
        raise ValueError(f'{name} は自動ダウンロードに対応していません（対応: {", ".join(DOWNLOADABLE)}）')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = JPL_EPHEMERIS_URL + name
    log(f'JPL 暦 {name}（{DOWNLOADABLE[name]}）をダウンロードしています: {url}')
    download_file(url, path, log)
    log(f'保存しました: {path}')
    return path


class Context:
    """Loaded JPL ephemeris + Skyfield timescale.

    ``delta_t`` (seconds) overrides Skyfield's built-in Delta T model
    (IERS observations + long-term spline) with a constant value.
    """

    def __init__(self, ephemeris=DEFAULT_EPHEMERIS, delta_t=None):
        path = _ephemeris_path(ephemeris)
        if path is None:
            raise ValueError(tr('暦ファイル {ephemeris} が {dir} にありません', ephemeris=ephemeris, dir=DATA_DIR))
        loader = Loader(str(path.parent), verbose=False)
        self.ephemeris_name = ephemeris
        self.eph = loader(ephemeris)
        if delta_t is None:
            self.ts = loader.timescale()
        else:
            self.ts = loader.timescale(delta_t=float(delta_t))
        self.delta_t_override = delta_t
        self.earth = self.eph['earth']
        self.sun = self.eph['sun']
        self.moon = self.eph['moon']
        self.bodies = {
            'sun': self.sun,
            'moon': self.moon,
            'mercury': self.eph['mercury'],
            'venus': self.eph['venus'],
        }
        starts, ends = [], []
        for seg in self.eph.segments:
            spk = getattr(seg, 'spk_segment', None)
            if spk is not None:
                starts.append(spk.start_jd)
                ends.append(spk.end_jd)
        # Keep a small margin so that light-time iterations stay inside.
        self.jd_min = max(starts) + 1.0
        self.jd_max = min(ends) - 1.0

    def time(self, jd0, x):
        """Skyfield Time for TT Julian date ``jd0 + x`` (x may be an array)."""
        return self.ts.tt_jd(jd0, x)

    def coverage_utc(self):
        a = self.ts.tt_jd(self.jd_min).utc_strftime('%Y-%m-%d')
        b = self.ts.tt_jd(self.jd_max).utc_strftime('%Y-%m-%d')
        return a, b


def get_context(ephemeris=DEFAULT_EPHEMERIS, delta_t=None):
    key = (ephemeris, None if delta_t is None else float(delta_t))
    with _contexts_lock:
        ctx = _contexts.get(key)
        if ctx is None:
            ctx = Context(ephemeris, delta_t)
            _contexts[key] = ctx
        return ctx

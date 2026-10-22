"""The Earth's shadow on a spacecraft: the times at which the Earth hides the Sun, orbit by orbit.

The test is that of the visibility of the eclipses and transits (local.py, Evaluator.visibility):
the margin

    m(t) = sep_e - asin((R_limb + h) / r)

between the Sun's centre and the Earth's limb raised by an absorbing layer of the atmosphere of
height h (the setting earth_atm_km: ~0 km in visible light, ~140 km in soft X-rays, ~250 km in the
EUV), with the ellipsoidal Earth, its pole of date and the aberration by the Earth's velocity.
The Sun is partly hidden while m < rho_sun, its centre while m < 0 (what the eclipses call "hidden
by the Earth") and all of it while m < -rho_sun.

The roots are bracketed as the contacts of the eclipses (local.scan_brackets, a branch and bound
with a Lipschitz bound of m from the speed of the spacecraft, so that no shadow longer than ~0.5 s
is missed between the samples), then refined by bisection and regula falsi to ~1 ms.
"""
import math

import numpy as np
from skyfield.api import wgs84

from .constants import DAY_S, WGS84_A, WGS84_B
from .i18n import tr
from .local import LocalSearch, scan_brackets
from .observers import ObserverError
from .timeutil import iso_utc

DEFAULT_LEVELS_KM = (0.0, 140.0, 250.0)     # visible light, soft X-rays, EUV
MAX_LEVELS = 6
MAX_LEVEL_KM = 3000.0
MAX_PASSES = 20000
MAX_ORBITS = 8000                           # the period: ~1.5 years in a low orbit, ~22 years in GEO
SCAN_TOL_S = 0.5
ROOT_TOL_S = 1e-3
SUN_RATE = 2.0e-7 * DAY_S                   # rad/day: the Sun's direction (the Earth's orbit)
FLAT_RATE = WGS84_A - WGS84_B               # km per radian: the change of the limb's radius
CRITERIA = ('partial', 'center', 'total')   # some of the Sun, its centre, all of it hidden
OFFSET = {'partial': -1.0, 'center': 0.0, 'total': 1.0}     # times rho_sun added to the margin


def atm_levels(params):
    """The heights of the absorbing layer to compare (km, sorted, each once), with that of the
    settings (earth_atm_km) among them."""
    levels = {round(float(h), 6) for h in params.shadow_atm_levels_km}
    levels.add(round(float(params.earth_atm_km), 6))
    return sorted(levels)


def check_period(observer, jd_a, jd_b):
    """ObserverError unless the observer is a spacecraft and the period has at most MAX_ORBITS orbits
    (about a year in a low orbit; the orbital period of a spacecraft from NASA SSCWeb is known once its
    orbit data are surveyed)."""
    if observer.kind != 'space':
        raise ObserverError(tr('地球の影は人工衛星・探査機から見たときだけ計算できます（観測者を「人工衛星」にしてください）'))
    period_d = observer.char_time_s() * 2 * math.pi / DAY_S
    if (jd_b - jd_a) / period_d > MAX_ORBITS:
        raise ObserverError(tr('地球の影を計算する期間は、この衛星では {days} 日以内（約 {n} 周まで）にしてください',
                               days=int(MAX_ORBITS * period_d), n=f'{MAX_ORBITS:,}'))


def check_levels(values):
    """The heights of the setting shadow_atm_levels_km -> list of floats; ValueError (translated)."""
    if values is None or values == '':
        return list(DEFAULT_LEVELS_KM)
    if isinstance(values, (int, float)) and not isinstance(values, bool):
        values = [values]
    if isinstance(values, str):
        values = [v for v in values.replace('、', ',').replace(' ', ',').split(',') if v]
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError):
        out = None
    if not out or len(out) > MAX_LEVELS or not all(math.isfinite(h) and 0 <= h <= MAX_LEVEL_KM for h in out):
        raise ValueError(tr('比べる遮蔽高度は 0〜{max:g} km の数値を {n} 個までカンマ区切りで指定してください',
                            max=MAX_LEVEL_KM, n=MAX_LEVELS))
    return out


class ShadowSearch:
    """The Earth's shadow on a spacecraft observer over [jd_a, jd_b] (TT JDs) for the heights
    ``levels`` (km) of the absorbing atmosphere; every criterion for the height of the settings
    (params.earth_atm_km), the Sun's centre for the others."""

    def __init__(self, ctx, observer, params, jd_a, jd_b, levels):
        self.ctx = ctx
        self.observer = observer
        self.p = params
        self.levels = list(levels)
        self.primary = float(params.earth_atm_km)
        self.ls = LocalSearch(ctx, observer, 'moon', params, jd_a, jd_b)
        self.E = self.ls.E
        self.jd0 = self.ls.jd0
        self.warnings = self.ls.warnings
        self.period_d = observer.char_time_s() * 2 * math.pi / DAY_S
        self._cache = {}
        rho = min(math.asin(min(1.0, (WGS84_A + max(self.levels + [self.primary])) / max(observer.r_min_km, 1.0))), 1.56)
        self._tan, self._sec = math.tan(rho), 1.0 / math.cos(rho)

    # -- geometry -------------------------------------------------------------
    def _base(self, x):
        """(sep_e, R_limb, r, rho_sun, V) at jd0 + x, each time computed once (the scans of all the
        heights and criteria share the samples).  V bounds |dm/dt| (rad/day): the Earth's direction
        turns at |v_t| / r (v_t: the velocity across the line of sight), its angular radius changes
        at tan(rho_earth) |dr/dt| / r and by the flattening (the limb's radius changes by at most
        a - b per radian that the line of sight turns)."""
        keys = np.asarray(x, float).ravel().tolist()
        new = [k for k in dict.fromkeys(keys) if k not in self._cache]
        if new:
            g = self.E.sun_only(np.array(new))
            sep_e, reff, rn, st = self.E.earth_view(g)
            r, v = st.position.km, st.velocity.km_per_s
            vr = np.sum(r * v, axis=0) / rn
            vt = np.sqrt(np.maximum(np.sum(v * v, axis=0) - vr * vr, 0.0))
            V = ((vt + self._tan * np.abs(vr)) / rn * (1.0 + FLAT_RATE * self._sec / rn) * DAY_S
                 + SUN_RATE)
            for row in zip(new, sep_e.tolist(), reff.tolist(), rn.tolist(), g['rs'].tolist(), V.tolist()):
                self._cache[row[0]] = row[1:]
        return np.array([self._cache[k] for k in keys]).reshape(-1, 5).T

    @staticmethod
    def _margin(base, h, crit):
        sep_e, reff, rn, rs, _ = base
        return sep_e - np.arcsin(np.minimum((reff + h) / rn, 1.0)) + OFFSET[crit] * rs

    def _fn(self, h, crit):
        def f(x):
            b = self._base(x)
            return {'m': self._margin(b, h, crit), 'V': b[4]}
        return f

    # -- roots ----------------------------------------------------------------
    def _roots(self, f, A, B, FA, FB):
        """The roots in the brackets [A, B] (x-days, f(A) and f(B) of opposite signs): bisection to
        2 s, then regula falsi (Illinois) to ROOT_TOL_S."""
        A, B, FA, FB = (np.asarray(v, float).copy() for v in (A, B, FA, FB))
        if A.size == 0:
            return A
        while np.max(B - A) > 2.0 / DAY_S:
            M = 0.5 * (A + B)
            FM = f(M)['m']
            left = (FM < 0) == (FA < 0)
            A, FA = np.where(left, M, A), np.where(left, FM, FA)
            B, FB = np.where(left, B, M), np.where(left, FB, FM)
        X = 0.5 * (A + B)
        side = np.zeros(A.size)
        for _ in range(10):
            d = FB - FA
            Xn = np.where(d != 0, (A * FB - B * FA) / np.where(d != 0, d, 1.0), 0.5 * (A + B))
            done = np.abs(Xn - X) < ROOT_TOL_S / DAY_S
            X = Xn
            if np.all(done):
                break
            FX = f(X)['m']
            at_b = (FX < 0) == (FB < 0)
            # Illinois: when the same end is replaced twice, halve the value at the other one
            FA = np.where(at_b & (side == -1), 0.5 * FA, FA)
            FB = np.where(~at_b & (side == 1), 0.5 * FB, FB)
            B, FB = np.where(at_b, X, B), np.where(at_b, FX, FB)
            A, FA = np.where(at_b, A, X), np.where(at_b, FA, FX)
            side = np.where(at_b, -1, 1)
        return X

    def _intervals(self, windows, h, crit):
        """The intervals [x_in, x_out, cut_in, cut_out] in which the margin is negative."""
        f = self._fn(h, crit)
        br = scan_brackets(f, 'm', windows, SCAN_TOL_S / DAY_S)
        roots = self._roots(f, br['A'], br['B'], br['GA'], br['GB'])
        per = {}
        for x, w, ga in sorted(zip(roots.tolist(), br['W'].tolist(), br['GA'].tolist())):
            per.setdefault(w, []).append((x, ga >= 0))
        out = []
        for k, (a, b, _) in enumerate(windows):
            inside = bool(len(br['start']) > k and br['start'][k] < 0)
            x_in = a if inside else None
            for x, entering in per.get(k, []):
                if entering:
                    x_in, inside = x, True
                elif inside:
                    out.append([x_in, x, x_in == a, False])
                    inside = False
            if inside:
                out.append([x_in, b, x_in == a, True])
        return out

    # -- search ---------------------------------------------------------------
    def run(self):
        ctx, ls = self.ctx, self.ls
        check_period(self.observer, ls.jd_a, ls.jd_b)
        if ls.jd_b <= ls.jd_a:
            return self._result([], {}, [])
        ls.limit_period(0.02)
        self.period_d = self.observer.char_time_s() * 2 * math.pi / DAY_S      # ~ the orbital period
        check_period(self.observer, ls.jd_a, ls.jd_b)
        wins = self.observer.clip_windows([(ls.jd_a, ls.jd_b)])
        self.observer.prepare(ctx, wins)
        step = min(3600.0, max(30.0, self.period_d * DAY_S / 50.0)) / DAY_S
        windows = [(a - self.jd0, b - self.jd0, step) for a, b in wins]
        primary = {c: self._intervals(windows, self.primary, c) for c in CRITERIA}
        centers = {h: (primary['center'] if h == self.primary else self._intervals(windows, h, 'center'))
                   for h in self.levels}
        return self._result(windows, centers, self._passes(primary))

    def _passes(self, primary):
        """One pass per interval in which some of the Sun is hidden (for the height of the
        settings), with the intervals of the centre and of all of the Sun in it."""
        parts = primary['partial']
        if len(parts) > MAX_PASSES:
            self.warnings.append(tr('地球の影に入る回数が多すぎるため、最初の {n} 回だけを一覧にしました', n=MAX_PASSES))
            parts = parts[:MAX_PASSES]
        if not parts:
            return []
        mids = np.array([0.5 * (p[0] + p[1]) for p in parts])
        g = self.E.sun_only(mids)
        sep_e, reff, rn, st = self.E.earth_view(g)
        depth = -np.degrees(sep_e - np.arcsin(np.minimum((reff + self.primary) / rn, 1.0)))
        sub = wgs84.geographic_position_of(st)
        lat, lon, alt = sub.latitude.degrees, sub.longitude.degrees, sub.elevation.km
        inner = {}
        for c in ('center', 'total'):           # the intervals do not overlap: sorted by both ends
            ivs = sorted(primary[c])
            inner[c] = (ivs, np.array([iv[0] for iv in ivs]), np.array([iv[1] for iv in ivs]))
        out = []
        for k, (a, b, cut_a, cut_b) in enumerate(parts):
            p = {'partial': [a, b], 'cut_start': cut_a, 'cut_end': cut_b, 'mid': mids[k],
                 'depth_deg': float(depth[k]), 'sat_lat': float(lat[k]), 'sat_lon': float(lon[k]),
                 'sat_alt_km': float(alt[k])}
            for c, (ivs, lo, hi) in inner.items():
                i, j = int(np.searchsorted(hi, a - 1e-9)), int(np.searchsorted(lo, b + 1e-9, 'right'))
                p[c] = [ivs[i][0], ivs[j - 1][1]] if j > i else None
            out.append(p)
        return out

    # -- output ---------------------------------------------------------------
    def _iso(self, xs):
        xs = list(xs)
        return iso_utc(self.ctx.time(self.jd0, np.array(xs, float))) if xs else []

    def _seasons(self, ivs):
        """Groups of shadows not more than a few orbits (at least 3 days) apart."""
        gap = max(3.0, 3.0 * self.period_d)
        groups = []
        for iv in ivs:
            if groups and iv[0] - groups[-1][-1][1] <= gap:
                groups[-1].append(iv)
            else:
                groups.append([iv])
        return groups

    def _level(self, h, ivs):
        dur = [(b - a) * DAY_S for a, b, _, _ in ivs]
        starts = self._iso(iv[0] for iv in ivs)
        ends = self._iso(iv[1] for iv in ivs)
        daily = {}
        for s, d in zip(starts, dur):
            n, mx, tot = daily.get(s[:10], (0, 0.0, 0.0))
            daily[s[:10]] = (n + 1, max(mx, d), tot + d)
        seasons, k = [], 0
        lo, hi = self._span
        near = 1.5 * self.period_d          # a season that goes on beyond the period: a shadow within an orbit of its ends
        for grp in self._seasons(ivs):
            n = len(grp)
            dd = dur[k:k + n]
            j = int(np.argmax(dd))
            seasons.append({'start': starts[k], 'end': ends[k + n - 1], 'n': n, 'max_s': dd[j],
                            'max_at': starts[k + j], 'min_s': min(dd), 'total_s': sum(dd),
                            'cut_start': bool(grp[0][2]) or grp[0][0] - lo < near,
                            'cut_end': bool(grp[-1][3]) or hi - grp[-1][1] < near,
                            'days': (grp[-1][1] - grp[0][0])})
            k += n
        j = int(np.argmax(dur)) if dur else None
        return {'atm_km': h, 'n': len(ivs), 'total_s': sum(dur),
                'max_s': dur[j] if dur else None, 'max_at': starts[j] if dur else None,
                'seasons': seasons, 'daily': [[d, n, mx, tot] for d, (n, mx, tot) in sorted(daily.items())]}

    def _result(self, windows, centers, passes):
        span_s = sum(b - a for a, b, _ in windows) * DAY_S
        self._span = (windows[0][0], windows[-1][1]) if windows else (0.0, 0.0)
        levels = []
        for h in self.levels:
            lv = self._level(h, centers.get(h, []))
            lv['fraction'] = lv['total_s'] / span_s if span_s > 0 else 0.0
            levels.append(lv)
        xs = [x for p in passes for c in CRITERIA if p[c] for x in p[c]]
        iso = dict(zip(xs, self._iso(xs)))
        mid = self._iso(p['mid'] for p in passes)
        rows = []
        for p, m in zip(passes, mid):
            r = {'mid': m, 'depth_deg': p['depth_deg'], 'sat_lat': p['sat_lat'], 'sat_lon': p['sat_lon'],
                 'sat_alt_km': p['sat_alt_km'], 'cut_start': p['cut_start'], 'cut_end': p['cut_end']}
            for c, (k_in, k_out, k_s) in {'center': ('start', 'end', 'duration_s'),
                                          'partial': ('partial_start', 'partial_end', 'partial_s'),
                                          'total': ('total_start', 'total_end', 'total_s')}.items():
                iv = p[c]
                r[k_in], r[k_out] = (iso[iv[0]], iso[iv[1]]) if iv else (None, None)
                r[k_s] = (iv[1] - iv[0]) * DAY_S if iv else None
            rows.append(r)
        a, b = (windows[0][0], windows[-1][1]) if windows else (self.ls.jd_a - self.jd0, self.ls.jd_b - self.jd0)
        return {'atm_km': self.primary, 'levels': levels, 'passes': rows,
                'period_start': self._iso([a])[0], 'period_end': self._iso([b])[0]}

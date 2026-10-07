"""Local circumstances of solar eclipses and planetary transits for an
arbitrary observer (ground station, geocentre or spacecraft).

Algorithm
---------
1. Geocentric conjunctions are used to build candidate time windows.  The
   window width accounts for the maximum parallax the observer can have
   (for observers far from Earth the whole interval is scanned instead).
2. In each window the overlap function
       g_ext(t) = sep(t) - (rho_sun + rho_body)
   is sampled.  A Lipschitz bound obtained from the true angular velocities
   of both bodies is used in a branch-and-bound subdivision, so that no
   overlap interval longer than ~0.5 s can be missed between samples.
3. Contacts are refined by batched bisection to ~0.1 ms; the instant of
   maximum (minimum centre distance) by batched golden-section search.
4. Visibility (Sun above the horizon for a ground station, Sun not hidden
   by the Earth for a spacecraft) is evaluated and its crossings refined.

Positions are astrometric (light-time corrected, BCRS directions).  Because
stellar aberration acts on all directions as a conformal map, disk tangency
- and therefore every contact time - is invariant under it; apparent places
are only used for altitude/azimuth and position angles.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from skyfield.api import wgs84
from skyfield.framelib import true_equator_and_equinox_of_date as TOD

from .constants import (AU_KM, DAY_S, MERCURY_RADIUS, MOON_K_EXTERNAL, MOON_K_INTERNAL,
                        RAD2ARCSEC, SUN_RADIUS_IAU2015, VENUS_RADIUS, WGS84_A, WGS84_B)
from .conjunctions import BODY_MIN_DISTANCE_KM, find_conjunctions, geocentric_min_separation
from .geometry import angle_between, magnitude, norm, obscuration
from .i18n import tr
from .observers import HorizonsObserver, ObserverError, SSCWebObserver
from .saros import saros_number
from .timeutil import iso_utc

SAFETY = 1.6
DEG = math.pi / 180.0


@dataclass
class Params:
    sun_radius_km: float = SUN_RADIUS_IAU2015
    moon_radius_ext_km: float = MOON_K_EXTERNAL * WGS84_A
    moon_radius_int_km: float = MOON_K_INTERNAL * WGS84_A
    mercury_radius_km: float = MERCURY_RADIUS
    venus_radius_km: float = VENUS_RADIUS
    min_sun_alt_deg: float = 0.0
    refraction: bool = True
    earth_atm_km: float = 0.0
    include_invisible: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        p = cls()
        for k in ('sun_radius_km', 'moon_radius_ext_km', 'moon_radius_int_km',
                  'mercury_radius_km', 'venus_radius_km', 'min_sun_alt_deg', 'earth_atm_km'):
            if d.get(k) is not None and d.get(k) != '':
                setattr(p, k, float(d[k]))
        for k in ('refraction', 'include_invisible'):
            if k in d and d[k] is not None:
                setattr(p, k, bool(d[k]))
        return p

    def body_radii(self, body):
        if body == 'moon':
            return self.moon_radius_ext_km, self.moon_radius_int_km
        r = self.mercury_radius_km if body == 'mercury' else self.venus_radius_km
        return r, r

    def as_dict(self):
        return {k: getattr(self, k) for k in (
            'sun_radius_km', 'moon_radius_ext_km', 'moon_radius_int_km', 'mercury_radius_km',
            'venus_radius_km', 'min_sun_alt_deg', 'refraction', 'earth_atm_km',
            'include_invisible')}


def earth_disk(r_obs_km, sun_vec):
    """Angular separation between the Sun centre and the Earth centre, and
    the angular radius of the (ellipsoidal) Earth limb toward the Sun, as
    seen from an observer at geocentric position ``r_obs_km``."""
    rn = norm(r_obs_km)
    su = sun_vec / norm(sun_vec)
    sep_e = angle_between(sun_vec, -r_obs_km)
    lam = -np.sum(r_obs_km * su, axis=0)
    q = r_obs_km + np.maximum(lam, 0.0) * su
    qn = np.maximum(norm(q), 1e-9)
    sphi = np.clip(q[2] / qn, -1, 1)
    cphi = np.sqrt(1 - sphi * sphi)
    reff = WGS84_A * WGS84_B / np.sqrt((WGS84_B * cphi) ** 2 + (WGS84_A * sphi) ** 2)
    return sep_e, reff, rn


class Evaluator:
    """Evaluates Sun/body geometry for an observer at TT JD jd0 + x."""

    def __init__(self, ctx, observer, body, params, jd0):
        self.ctx = ctx
        self.observer = observer
        self.body = body
        self.p = params
        self.jd0 = float(jd0)
        # Skyfield skips the Earth-limb deflection test only when the observer
        # is the plain geocentre vector, so do not wrap it in a zero offset.
        self.obs = ctx.earth if observer.kind == 'geocenter' else ctx.earth + observer.vf
        self.target = ctx.bodies[body]
        self.Rs = params.sun_radius_km
        self.Rbe, self.Rbi = params.body_radii(body)
        self.n_eval = 0

    def time(self, x):
        return self.ctx.time(self.jd0, x)

    def geom(self, x, need_vis=False):
        x = np.atleast_1d(np.asarray(x, float))
        t = self.time(x)
        o = self.obs.at(t)
        sa = o.observe(self.ctx.sun)
        ba = o.observe(self.target)
        S, B = sa.position.km, ba.position.km
        Sv, Bv = sa.velocity.km_per_s, ba.velocity.km_per_s
        ds, db = norm(S), norm(B)
        sep = angle_between(S, B)
        rs = np.arcsin(self.Rs / ds)
        rbe = np.arcsin(np.minimum(self.Rbe / db, 1.0))
        rbi = np.arcsin(np.minimum(self.Rbi / db, 1.0))
        su, bu = S / ds, B / db
        sdot = np.sum(Sv * su, axis=0)
        bdot = np.sum(Bv * bu, axis=0)
        ws = norm(Sv - sdot * su) / ds
        wb = norm(Bv - bdot * bu) / db
        V = (ws + wb + rs * np.abs(sdot) / ds + rbe * np.abs(bdot) / db) * DAY_S
        self.n_eval += x.size
        g = dict(x=x, t=t, o=o, sa=sa, ba=ba, S=S, B=B, ds=ds, db=db, sep=sep, rs=rs,
                 rbe=rbe, rbi=rbi, g_ext=sep - rs - rbe, g_int=sep - np.abs(rs - rbi), V=V)
        if need_vis:
            g['vis'] = self.visibility(g)
        return g

    def visibility(self, g):
        """Visibility margin in degrees (>0: Sun centre visible)."""
        kind = self.observer.kind
        n = g['x'].size
        if kind == 'geocenter':
            return np.full(n, 90.0)
        if kind == 'ground':
            app = g['sa'].apparent()
            if self.p.refraction:
                alt, _, _ = app.altaz(temperature_C='standard')
            else:
                alt, _, _ = app.altaz()
            return alt.degrees - self.p.min_sun_alt_deg
        r = self.observer.vf.at(g['t']).position.km
        sep_e, reff, rn = earth_disk(r, g['S'])
        rho_e = np.arcsin(np.minimum((reff + self.p.earth_atm_km) / rn, 1.0))
        return np.degrees(sep_e - rho_e)

    def fn(self, key, need_vis=False):
        def f(x):
            return self.geom(x, need_vis=need_vis)
        return f


# ---------------------------------------------------------------------------
# Generic batched numerics
# ---------------------------------------------------------------------------
def scan_brackets(evaluate, key, windows, tol_days, vkey='V'):
    """Find all sign changes of ``key`` inside the windows.

    ``windows`` is a list of (a, b, h) in x-days.  Returns a dict with
    arrays A, B, GA, GB, W (window index) for each bracket and the function
    values at the start/end of every window.
    """
    xs, wid = [], []
    for k, (a, b, h) in enumerate(windows):
        n = max(2, int(math.ceil((b - a) / h)) + 1)
        xs.append(np.linspace(a, b, n))
        wid.append(np.full(n, k))
    if not xs:
        empty = np.zeros(0)
        return dict(A=empty, B=empty, GA=empty, GB=empty, W=empty.astype(int),
                    start=empty, end=empty)
    x = np.concatenate(xs)
    w = np.concatenate(wid)
    ev = evaluate(x)
    g, V = ev[key], ev[vkey]
    starts = np.r_[0, np.nonzero(np.diff(w))[0] + 1]
    ends = np.r_[starts[1:] - 1, x.size - 1]
    same = w[:-1] == w[1:]
    ia = np.nonzero(same)[0]
    A, B, GA, GB, VA, VB, W = x[ia], x[ia + 1], g[ia], g[ia + 1], V[ia], V[ia + 1], w[ia]
    out = {k: [] for k in ('A', 'B', 'GA', 'GB', 'W')}
    while A.size:
        L = SAFETY * np.maximum(VA, VB)
        width = B - A
        lb = 0.5 * (GA + GB - L * width)
        ub = 0.5 * (GA + GB + L * width)
        sc = (GA < 0) != (GB < 0)
        res_pos = (~sc) & (GA >= 0) & (lb > 0)
        res_neg = (~sc) & (GA < 0) & (ub < 0)
        for k, arr in (('A', A), ('B', B), ('GA', GA), ('GB', GB), ('W', W)):
            out[k].append(arr[sc])
        split = ~(sc | res_pos | res_neg) & (width > tol_days)
        if not np.any(split):
            break
        A, B, GA, GB, VA, VB, W = (arr[split] for arr in (A, B, GA, GB, VA, VB, W))
        M = 0.5 * (A + B)
        evm = evaluate(M)
        GM, VM = evm[key], evm[vkey]
        A, B = np.r_[A, M], np.r_[M, B]
        GA, GB = np.r_[GA, GM], np.r_[GM, GB]
        VA, VB = np.r_[VA, VM], np.r_[VM, VB]
        W = np.r_[W, W]
    res = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in out.items()}
    res['W'] = res['W'].astype(int)
    res['start'] = g[starts]
    res['end'] = g[ends]
    res['x_start'] = x[starts]
    res['x_end'] = x[ends]
    return res


def bisect_roots(evaluate, key, A, B, GA, tol_days=1e-9):
    A, B, GA = (np.asarray(v, float).copy() for v in (A, B, GA))
    if A.size == 0:
        return A
    width = float(np.max(B - A))
    n = int(math.ceil(math.log2(max(width, tol_days) / tol_days))) + 1
    for _ in range(min(n, 64)):
        M = 0.5 * (A + B)
        GM = evaluate(M)[key]
        same = (GM < 0) == (GA < 0)
        A = np.where(same, M, A)
        GA = np.where(same, GM, GA)
        B = np.where(same, B, M)
    return 0.5 * (A + B)


def golden_min(evaluate, key, A, B, n_iter=42):
    A, B = np.asarray(A, float).copy(), np.asarray(B, float).copy()
    if A.size == 0:
        return A
    gr = (math.sqrt(5) - 1) / 2
    C = B - gr * (B - A)
    D = A + gr * (B - A)
    ev = evaluate(np.r_[C, D])[key]
    FC, FD = ev[:A.size], ev[A.size:]
    for _ in range(n_iter):
        left = FC < FD
        A = np.where(left, A, C)
        B = np.where(left, D, B)
        oC, oD, oFC, oFD = C, D, FC, FD
        C = np.where(left, B - gr * (B - A), oD)
        D = np.where(left, oC, A + gr * (B - A))
        fnew = evaluate(np.where(left, C, D))[key]
        FC = np.where(left, fnew, oFD)
        FD = np.where(left, oFC, fnew)
    return 0.5 * (A + B)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def base_step_seconds(observer, body):
    tau = observer.char_time_s()
    cap = 600.0 if body == 'moon' else 1800.0
    h = min(cap, tau / 20.0)
    if isinstance(observer, HorizonsObserver):
        h = min(h, observer.step_min * 60.0)
    return max(h, 5.0)


def candidate_windows(ctx, observer, body, jd_a, jd_b, params):
    """Return list of (jd_a, jd_b) TT windows that can contain events."""
    d_min = BODY_MIN_DISTANCE_KM[body]
    r = observer.r_max_km
    if r > 0.45 * d_min:
        return [(jd_a, jd_b)], None
    Rbe, _ = params.body_radii(body)
    par_b = math.asin(min(1.0, r / (d_min - r))) * 1.05
    par_s = math.asin(r / (0.98 * AU_KM))
    rho_s = math.asin(params.sun_radius_km / (0.983 * AU_KM - r))
    rho_b = math.asin(min(1.0, Rbe / (d_min - r)))
    limit = rho_s + rho_b + par_b + par_s + 0.06 * DEG
    tc = find_conjunctions(ctx, body, jd_a - 2.0, jd_b + 2.0)
    span = 0.3 if body == 'moon' else 1.5
    smin, rate = geocentric_min_separation(ctx, body, tc, span)
    keep = smin < limit
    wins = []
    for c, sm, rt in zip(tc[keep], smin[keep], rate[keep]):
        half = 1.5 * limit / max(rt, 1e-6) + (1.0 / 24 if body == 'moon' else 3.0 / 24)
        a, b = max(c - half, jd_a), min(c + half, jd_b)
        if b > a:
            wins.append([a, b, c])
    merged = []
    for w in sorted(wins):
        if merged and w[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
    return [(a, b) for a, b, _ in merged], tc


class LocalSearch:
    def __init__(self, ctx, observer, body, params, jd_a, jd_b):
        self.ctx = ctx
        self.observer = observer
        self.body = body
        self.p = params
        self.jd_a = max(jd_a, ctx.jd_min + 0.5)
        self.jd_b = min(jd_b, ctx.jd_max - 0.5)
        self.jd0 = math.floor(self.jd_a) + 0.5
        self.E = Evaluator(ctx, observer, body, params, self.jd0)
        self.h0 = base_step_seconds(observer, body) / DAY_S
        self.tol = 0.5 / DAY_S
        self.warnings = []

    def run(self, max_events=3000):
        ctx, E = self.ctx, self.E
        if self.jd_b <= self.jd_a:
            return []
        margin = 0.6
        if isinstance(self.observer, HorizonsObserver):
            if self.observer.coverage is None:
                self.observer.survey(self.jd_a - margin, self.jd_b + margin)
            ca, cb = self.observer.coverage
            pad = self.observer.step_min / 1440.0 + 0.02
            if ca + pad > self.jd_a - margin or cb - pad < self.jd_b + margin:
                a2 = max(self.jd_a, ca + pad + margin)
                b2 = min(self.jd_b, cb - pad - margin)
                if b2 <= a2:
                    raise ObserverError(tr('指定期間には JPL Horizons の軌道データがありません'))
                self.warnings.append(tr('JPL Horizons の軌道データがある期間（{start}〜{end}）に限定して計算しました',
                                        start=iso_utc(self.ctx.ts.tt_jd(a2))[:10],
                                        end=iso_utc(self.ctx.ts.tt_jd(b2))[:10]))
                self.jd_a, self.jd_b = a2, b2
            self.h0 = base_step_seconds(self.observer, self.body) / DAY_S
        elif isinstance(self.observer, SSCWebObserver):
            cov = self.observer.coverage_jd()
            if cov and (cov[0] > self.jd_a - margin or cov[1] < self.jd_b + margin):
                a2 = max(self.jd_a, cov[0] + margin + 0.01)
                b2 = min(self.jd_b, cov[1] - margin - 0.01)
                period = (f'{iso_utc(self.ctx.ts.tt_jd(cov[0]))[:10]}〜'
                          f'{iso_utc(self.ctx.ts.tt_jd(cov[1]))[:10]}')
                if b2 <= a2:
                    raise ObserverError(tr('指定期間には NASA SSCWeb の軌道データがありません（提供期間 {period}）', period=period))
                self.warnings.append(tr('NASA SSCWeb の軌道データがある期間（{period}）に限定して計算しました', period=period))
                self.jd_a, self.jd_b = a2, b2
            if not self.observer.surveyed:
                self.observer.survey(self.jd_a, self.jd_b)
            self.h0 = base_step_seconds(self.observer, self.body) / DAY_S
        wins, _ = candidate_windows(ctx, self.observer, self.body, self.jd_a - margin,
                                    self.jd_b + margin, self.p)
        self.observer.prepare(ctx, wins)
        windows = [(a - self.jd0, b - self.jd0, self.h0) for a, b in wins]
        ev = E.geom
        br = scan_brackets(ev, 'g_ext', windows, self.tol)
        roots = bisect_roots(ev, 'g_ext', br['A'], br['B'], br['GA'])
        entering = br['GA'] >= 0
        events = []
        order = np.lexsort((roots, br['W']))
        per_win = {}
        for i in order:
            per_win.setdefault(int(br['W'][i]), []).append((roots[i], bool(entering[i])))
        for k in range(len(windows)):
            lst = per_win.get(k, [])
            inside = br['start'][k] < 0 if len(br['start']) else False
            c1 = windows[k][0] if inside else None
            c1_cut = inside
            for x, ent in lst:
                if ent:
                    c1, c1_cut = x, False
                    inside = True
                elif inside:
                    events.append(dict(x1=c1, x4=x, c1_cut=c1_cut, c4_cut=False))
                    inside = False
            if inside and c1 is not None:
                events.append(dict(x1=c1, x4=windows[k][1], c1_cut=c1_cut, c4_cut=True))
        if len(events) > max_events:
            self.warnings.append(tr('現象が多すぎるため最初の {n} 件のみ処理しました', n=max_events))
            events = events[:max_events]
        if not events:
            return []
        self._internal(events)
        self._maximum(events)
        self._visibility(events)
        out = []
        for e in events:
            jd_max = self.jd0 + e['xm']
            if not (self.jd_a <= jd_max <= self.jd_b):
                continue
            if not self.p.include_invisible and e['vis_fraction'] <= 0:
                continue
            out.append(e)
        self._finalise(out)
        return out

    # -- phases ---------------------------------------------------------------
    def _internal(self, events):
        wins = []
        for e in events:
            dur = e['x4'] - e['x1']
            wins.append((e['x1'], e['x4'], max(min(self.h0, dur / 16.0), 1e-7)))
        br = scan_brackets(self.E.geom, 'g_int', wins, self.tol)
        roots = bisect_roots(self.E.geom, 'g_int', br['A'], br['B'], br['GA'])
        for e in events:
            e['internal'] = []
        per = {}
        order = np.lexsort((roots, br['W']))
        for i in order:
            per.setdefault(int(br['W'][i]), []).append((roots[i], bool(br['GA'][i] >= 0)))
        for k, e in enumerate(events):
            inside = len(br['start']) > k and br['start'][k] < 0
            x2 = e['x1'] if inside else None
            for x, ent in per.get(k, []):
                if ent:
                    x2, inside = x, True
                elif inside:
                    e['internal'].append([x2, x])
                    inside = False
            if inside and x2 is not None:
                e['internal'].append([x2, e['x4']])

    def _maximum(self, events):
        A, B = [], []
        samples = []
        for e in events:
            n = int(min(2000, max(48, 3 * (e['x4'] - e['x1']) / self.h0)))
            samples.append(np.linspace(e['x1'], e['x4'], n))
        allx = np.concatenate(samples)
        sep = self.E.geom(allx)['sep']
        pos = 0
        for e, s in zip(events, samples):
            v = sep[pos:pos + s.size]
            pos += s.size
            k = int(np.argmin(v))
            A.append(s[max(k - 1, 0)])
            B.append(s[min(k + 1, s.size - 1)])
        xm = golden_min(self.E.geom, 'sep', np.array(A), np.array(B))
        for e, x in zip(events, xm):
            e['xm'] = float(x)

    def _visibility(self, events):
        kind = self.observer.kind
        if kind == 'geocenter':
            for e in events:
                e['visible'] = [[e['x1'], e['x4']]]
                e['vis_fraction'] = 1.0
            return
        hv = (120.0 if kind == 'ground' else min(120.0, self.observer.char_time_s() / 40)) / DAY_S
        samples = []
        for e in events:
            n = int(min(4000, max(16, (e['x4'] - e['x1']) / hv + 2)))
            samples.append(np.linspace(e['x1'], e['x4'], n))
        allx = np.concatenate(samples)
        vis = self.E.geom(allx, need_vis=True)['vis']
        A, B, GA, owner = [], [], [], []
        pos = 0
        for k, s in enumerate(samples):
            v = vis[pos:pos + s.size]
            pos += s.size
            events[k]['_vs'] = (s, v)
            idx = np.nonzero((v[:-1] < 0) != (v[1:] < 0))[0]
            for i in idx:
                A.append(s[i]); B.append(s[i + 1]); GA.append(v[i]); owner.append(k)
        roots = bisect_roots(lambda x: self.E.geom(x, need_vis=True), 'vis',
                             np.array(A), np.array(B), np.array(GA), tol_days=1e-8)
        per = {}
        for r, k in zip(roots, owner):
            per.setdefault(k, []).append(r)
        for k, e in enumerate(events):
            s, v = e.pop('_vs')
            cuts = [e['x1']] + sorted(per.get(k, [])) + [e['x4']]
            intervals = []
            for a, b in zip(cuts[:-1], cuts[1:]):
                mid = 0.5 * (a + b)
                vm = np.interp(mid, s, v)
                if vm > 0 and b > a:
                    intervals.append([a, b])
            e['visible'] = intervals
            dur = e['x4'] - e['x1']
            e['vis_fraction'] = (sum(b - a for a, b in intervals) / dur) if dur > 0 else 0.0

    # -- output ---------------------------------------------------------------
    def _finalise(self, events):
        if not events:
            return
        # Evaluate everything needed at contacts in one batch.
        xs, owners = [], []
        for k, e in enumerate(events):
            pts = [('C1', e['x1'])]
            for j, (a, b) in enumerate(e['internal']):
                pts.append(('C2', a))
                pts.append(('C3', b))
            pts.append(('MAX', e['xm']))
            pts.append(('C4', e['x4']))
            pts.sort(key=lambda p: p[1])
            e['_pts'] = pts
            for _, x in pts:
                xs.append(x)
                owners.append(k)
        det = self.details(np.array(xs))
        pos = 0
        for e in events:
            pts = e.pop('_pts')
            contacts = []
            for label, x in pts:
                d = {key: (val[pos] if isinstance(val, (list, np.ndarray)) else val)
                     for key, val in det.items()}
                pos += 1
                d = {k: _py(v) for k, v in d.items()}
                d['label'] = label
                d['visible'] = any(a - 1e-9 <= x <= b + 1e-9 for a, b in e['visible'])
                contacts.append(d)
            e['contacts'] = contacts
        for e in events:
            self._summarise(e)

    def _summarise(self, e):
        body = self.body
        cmax = next(c for c in e['contacts'] if c['label'] == 'MAX')
        ratio = cmax['rho_b_int'] / cmax['rho_s']
        if body == 'moon':
            if e['internal']:
                etype = 'total' if ratio > 1 else 'annular'
            else:
                etype = 'partial'
        else:
            etype = 'transit' if e['internal'] else 'transit_grazing'
        jd0 = self.jd0
        t = self.ctx.time(jd0, np.array([e['x1'], e['xm'], e['x4']]))
        iso = iso_utc(t)
        internal_s = [(b - a) * DAY_S for a, b in e['internal']]
        vis = [[iso_utc(self.ctx.time(jd0, a)), iso_utc(self.ctx.time(jd0, b))]
               for a, b in e['visible']]
        # Maximum during the visible part
        if e['visible']:
            cand = [e['xm']] if any(a <= e['xm'] <= b for a, b in e['visible']) else []
            for a, b in e['visible']:
                cand += [a, b]
            g = self.E.geom(np.array(cand))
            m = magnitude(g['rs'], g['rbe'], g['sep'])
            k = int(np.argmax(m))
            vis_max = dict(time=iso_utc(g['t'][k]), magnitude=float(m[k]),
                           obscuration=float(obscuration(g['rs'][k], g['rbe'][k], g['sep'][k])))
        else:
            vis_max = None
        saros = saros_number(jd0 + e['xm']) if body == 'moon' else None
        e.update(dict(
            body=body, type=etype,
            jd_c1=jd0 + e['x1'], jd_max=jd0 + e['xm'], jd_c4=jd0 + e['x4'],
            c1=iso[0], max=iso[1], c4=iso[2],
            duration_s=(e['x4'] - e['x1']) * DAY_S,
            internal_durations_s=internal_s,
            central_duration_s=max(internal_s) if internal_s else 0.0,
            visible_intervals=vis,
            visible_at_max=any(a <= e['xm'] <= b for a, b in e['visible']),
            magnitude=cmax['magnitude'], obscuration=cmax['obscuration'],
            ratio=ratio, min_sep_arcsec=cmax['sep_arcsec'],
            body_diameter_arcsec=2 * cmax['rho_b'], sun_diameter_arcsec=2 * cmax['rho_s'],
            visible_max=vis_max, saros=saros,
            delta_t_s=float(t[1].delta_t),
        ))

    def details(self, x):
        """Rich quantities at times jd0 + x (apparent places for angles)."""
        x = np.atleast_1d(np.asarray(x, float))
        g = self.E.geom(x, need_vis=True)
        t = g['t']
        sapp = g['sa'].apparent()
        bapp = g['ba'].apparent()
        s_tod = sapp.frame_xyz(TOD).km
        b_tod = bapp.frame_xyz(TOD).km
        su = s_tod / norm(s_tod)
        bu = b_tod / norm(b_tod)
        zhat = np.array([0.0, 0.0, 1.0])[:, None]
        east = np.cross(zhat, su, axis=0)
        east /= norm(east)
        north = np.cross(su, east, axis=0)
        bs = np.sum(bu * su, axis=0)
        xi = np.sum(bu * east, axis=0) / bs * RAD2ARCSEC
        eta = np.sum(bu * north, axis=0) / bs * RAD2ARCSEC
        pa = np.degrees(np.arctan2(xi, eta)) % 360.0
        out = dict(
            time=iso_utc(t), jd_tt=self.jd0 + x,
            sep_arcsec=g['sep'] * RAD2ARCSEC, rho_s=g['rs'] * RAD2ARCSEC,
            rho_b=g['rbe'] * RAD2ARCSEC, rho_b_int=g['rbi'] * RAD2ARCSEC,
            magnitude=magnitude(g['rs'], g['rbe'], g['sep']),
            obscuration=obscuration(g['rs'], g['rbe'], g['sep']),
            xi=xi, eta=eta, pa=pa, vis=g['vis'],
            sun_dist_km=g['ds'], body_dist_km=g['db'],
        )
        kind = self.observer.kind
        if kind == 'ground':
            if self.p.refraction:
                alt, az, _ = sapp.altaz(temperature_C='standard')
            else:
                alt, az, _ = sapp.altaz()
            ha, dec, _ = sapp.hadec()
            H = ha.radians
            d = dec.radians
            phi = math.radians(self.observer.lat)
            q = np.degrees(np.arctan2(np.sin(H), math.tan(phi) * np.cos(d) - np.sin(d) * np.cos(H)))
            out.update(sun_alt=alt.degrees, sun_az=az.degrees, parallactic=q,
                       v_angle=(pa - q) % 360.0)
        elif kind == 'space':
            pos = self.observer.vf.at(t)
            r = pos.position.km
            sep_e, reff, rn = earth_disk(r, g['S'])
            rho_e = np.arcsin(np.minimum(reff / rn, 1.0))
            r_tod = pos.frame_xyz(TOD).km
            eu = -r_tod / norm(r_tod)
            pa_e = np.degrees(np.arctan2(np.sum(eu * east, axis=0), np.sum(eu * north, axis=0))) % 360
            sub = wgs84.geographic_position_of(pos)
            out.update(sat_lat=sub.latitude.degrees, sat_lon=sub.longitude.degrees,
                       sat_alt_km=sub.elevation.km, earth_sep_deg=np.degrees(sep_e),
                       earth_radius_deg=np.degrees(rho_e),
                       earth_atm_deg=np.degrees(np.arcsin(np.minimum(
                           (reff + self.p.earth_atm_km) / rn, 1.0))),
                       earth_pa=pa_e, dist_earth_km=rn)
        return out

    def timeseries(self, e, n=480):
        x1, x4 = e['jd_c1'] - self.jd0, e['jd_c4'] - self.jd0
        dur = x4 - x1
        pad = max(dur * 0.08, 90.0 / DAY_S)
        x = np.linspace(x1 - pad, x4 + pad, n)
        d = self.details(x)
        t0 = self.ctx.time(self.jd0, x[0])
        res = {k: (np.round(np.asarray(v, float), 6).tolist() if k not in ('time',) else None)
               for k, v in d.items() if k not in ('time', 'jd_tt')}
        res['t0'] = iso_utc(t0)
        res['dt_s'] = ((x - x[0]) * DAY_S).round(4).tolist()
        return res


def _py(v):
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v

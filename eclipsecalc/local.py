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
are only used for altitude/azimuth and position angles.  The test whether
the Earth hides the Sun from a spacecraft is about rays passing the Earth, so
it uses the Sun's direction in the Earth's frame (aberrated by the Earth's
barycentric velocity only).
"""
import math
from dataclasses import dataclass, field

import numpy as np
from skyfield.api import wgs84
from skyfield.framelib import true_equator_and_equinox_of_date as TOD
from skyfield.relativity import add_aberration

from .constants import (AU_KM, DAY_S, MERCURY_RADIUS, MOON_K_EXTERNAL, MOON_K_INTERNAL,
                        RAD2ARCSEC, SUN_POLE_DEC_DEG, SUN_POLE_RA_DEG, SUN_RADIUS_OBSERVED,
                        VENUS_RADIUS_VISIBLE, WGS84_A, WGS84_B)
from .conjunctions import BODY_MIN_DISTANCE_KM, BODY_MIN_RATE, find_conjunctions, geocentric_min_separation
from .geometry import angle_between, magnitude, norm, obscuration
from .i18n import span, tr
from .observers import HorizonsObserver, ObserverError, SGP4Observer, SSCWebObserver
from .saros import saros_number
from .shadow import Shadow, sun_moon_itrs
from .timeutil import iso_utc

SAFETY = 1.6
DEG = math.pi / 180.0
# The Sun's rotation axis (ICRS unit vector)
SUN_POLE = np.array([math.cos(SUN_POLE_DEC_DEG * DEG) * math.cos(SUN_POLE_RA_DEG * DEG),
                     math.cos(SUN_POLE_DEC_DEG * DEG) * math.sin(SUN_POLE_RA_DEG * DEG),
                     math.sin(SUN_POLE_DEC_DEG * DEG)])
# Half the time step of the central differences for rates (contact sensitivities, image motion)
HALF_STEP_S = 0.5


@dataclass
class Params:
    sun_radius_km: float = SUN_RADIUS_OBSERVED
    moon_radius_ext_km: float = MOON_K_EXTERNAL * WGS84_A
    moon_radius_int_km: float = MOON_K_INTERNAL * WGS84_A
    mercury_radius_km: float = MERCURY_RADIUS
    venus_radius_km: float = VENUS_RADIUS_VISIBLE
    min_sun_alt_deg: float = 0.0
    refraction: bool = True
    earth_atm_km: float = 0.0
    include_invisible: bool = False
    # the heights of the absorbing atmosphere compared in the Earth's shadow (earth_shadow.py); a tuple,
    # so that the values of as_dict() can be a key
    shadow_atm_levels_km: tuple = (0.0, 140.0, 250.0)
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
        if d.get('shadow_atm_levels_km') not in (None, ''):
            from .earth_shadow import check_levels
            try:
                p.shadow_atm_levels_km = tuple(check_levels(d['shadow_atm_levels_km']))
            except ValueError as exc:
                raise ObserverError(str(exc))
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
            'include_invisible', 'shadow_atm_levels_km')}


def earth_disk(r_obs_km, sun_vec, pole=None):
    """Angular separation between the Sun centre and the Earth centre, and
    the angular radius of the (ellipsoidal) Earth limb toward the Sun, as
    seen from an observer at geocentric position ``r_obs_km``.  The local
    radius is that at the latitude of the point where the ray to the Sun
    passes closest to the geocentre, measured from ``pole`` (the Earth's pole
    of date in the frame of the vectors; the z axis if None)."""
    rn = norm(r_obs_km)
    su = sun_vec / norm(sun_vec)
    sep_e = angle_between(sun_vec, -r_obs_km)
    lam = -np.sum(r_obs_km * su, axis=0)
    q = r_obs_km + np.maximum(lam, 0.0) * su
    qn = np.maximum(norm(q), 1e-9)
    sphi = np.clip((q[2] if pole is None else np.sum(q * pole, axis=0)) / qn, -1, 1)
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

    def sun_only(self, x):
        """The Sun from the observer at jd0 + x, without the body (for earth_view and the Earth's
        shadow): the keys t, o, sa, x, ds and rs of geom()."""
        x = np.atleast_1d(np.asarray(x, float))
        t = self.time(x)
        o = self.obs.at(t)
        sa = o.observe(self.ctx.sun)
        ds = norm(sa.position.km)
        self.n_eval += x.size
        return dict(x=x, t=t, o=o, sa=sa, ds=ds, rs=np.arcsin(self.Rs / ds))

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
        sep_e, reff, rn = self.earth_view(g)[:3]
        rho_e = np.arcsin(np.minimum((reff + self.p.earth_atm_km) / rn, 1.0))
        return np.degrees(sep_e - rho_e)

    def earth_view(self, g):
        """The Earth seen from a spacecraft: (separation of the Sun centre from the Earth
        centre (rad), local Earth radius toward the Sun (km), distance (km), geocentric
        state of the spacecraft).

        A ray from the Sun passes the Earth in the Earth's frame, where its direction is the
        astrometric one aberrated by the Earth's barycentric velocity (the spacecraft's own
        velocity aberrates only what the spacecraft sees; with the astrometric direction the
        boundaries were off by ~0.1-0.2 s in low orbits and 1.2 s in geostationary orbit)."""
        if '_earth' not in g:
            t = g['t']
            st = self.observer.vf.at(t)
            r = st.position.km
            sun = g['sa'].position.au.copy()
            add_aberration(sun, g['o'].velocity.au_per_d - st.velocity.au_per_d, g['sa'].light_time)
            pole = t.precession_matrix()[2]          # mean pole of date (GCRS); nutation < 20"
            g['_earth'] = earth_disk(r, sun, pole) + (st,)
        return g['_earth']

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
    # an event of a far observer can lie up to ~limit / rate days from its conjunction
    pad = max(2.0, 1.5 * limit / BODY_MIN_RATE[body])
    tc = find_conjunctions(ctx, body, jd_a - pad, jd_b + pad)
    smin, rate = geocentric_min_separation(ctx, body, tc, 0.3 if body == 'moon' else 1.5)
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


def tle_decay(ctx, observer, jd_a, jd_b, margin=0.6):
    """A TLE observer after the decay that SGP4 predicts is meaningless (inside the Earth): the end of
    the period limited to before it, and the warnings -> (jd_b, warnings); ObserverError if the
    whole period is after it."""
    jd_d = observer.decay_jd(jd_b + margin)
    if jd_d is None:
        return jd_b, []
    date = iso_utc(ctx.ts.tt_jd(jd_d))[:10]
    if jd_d - margin <= jd_a:
        raise ObserverError(tr('SGP4 ではこの衛星は {date} ごろに再突入するため、指定期間は計算できません', date=date))
    return min(jd_b, jd_d - margin), [tr('SGP4 ではこの衛星は {date} ごろに再突入するため、それ以降は計算していません',
                                         date=date)]


def eclipse_new_moons(ctx, jd, params):
    """The geocentric new moons within 1 d of the times ``jd`` that have a solar eclipse somewhere on
    the Earth (the penumbra touches it: the test of search_global)."""
    jd = np.asarray(jd, float)
    tc = find_conjunctions(ctx, 'moon', jd.min() - 1.0, jd.max() + 1.0)
    if tc.size:
        tc = tc[np.abs(tc[:, None] - jd[None, :]).min(axis=1) < 1.0]
    if tc.size == 0:
        return tc
    off = np.arange(-7 * 60, 7 * 60 + 1, 5) / 1440.0
    S, M, _ = sun_moon_itrs(ctx, (tc[:, None] + off[None, :]).ravel())
    mp = Shadow(S, M, params).fundamental()['m_partial'].reshape(tc.size, -1)
    return tc[mp.min(axis=1) < 0]


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
        self.limit_period(margin)
        wins, _ = candidate_windows(ctx, self.observer, self.body, self.jd_a - margin,
                                    self.jd_b + margin, self.p)
        wins = self.observer.clip_windows(wins)
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

    def limit_period(self, margin):
        """The period limited to the orbit data of the observer (and before the re-entry that SGP4
        predicts), with the orbit data of a service surveyed for it and the step of the scan for
        them; the warnings go to self.warnings."""
        ctx = self.ctx
        if isinstance(self.observer, HorizonsObserver):
            pad = self.observer.step_min / 1440.0 + 0.02
            if self.observer.coverage is None:
                self.observer.survey(self.jd_a - margin - pad, self.jd_b + margin + pad)
            self.warnings += self.observer.step_warnings()
            ca, cb = self.observer.coverage
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
                period = span(iso_utc(self.ctx.ts.tt_jd(cov[0]))[:10], iso_utc(self.ctx.ts.tt_jd(cov[1]))[:10])
                if b2 <= a2:
                    raise ObserverError(tr('指定期間には NASA SSCWeb の軌道データがありません（提供期間 {period}）', period=period))
                self.warnings.append(tr('NASA SSCWeb の軌道データがある期間（{period}）に限定して計算しました', period=period))
                self.jd_a, self.jd_b = a2, b2
            if not self.observer.surveyed:
                self.observer.survey(self.jd_a, self.jd_b)
            self.h0 = base_step_seconds(self.observer, self.body) / DAY_S
        elif isinstance(self.observer, SGP4Observer):
            self.jd_b, w = tle_decay(ctx, self.observer, self.jd_a, self.jd_b, margin)
            self.warnings += [x for x in list(self.observer.notes) + w if x not in self.warnings]
        else:      # orbit data of limited extent (CCSDS ephemerides, propagated state vectors)
            self.jd_a, self.jd_b, w = self.observer.limit_period(ctx, self.jd_a, self.jd_b, margin)
            self.warnings += [x for x in w if x not in self.warnings]
            self.h0 = base_step_seconds(self.observer, self.body) / DAY_S

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
        xm = self._polish_maximum(events, golden_min(self.E.geom, 'sep', np.array(A), np.array(B)))
        for e, x in zip(events, xm):
            e['xm'] = float(x)

    def _polish_maximum(self, events, xm, half_s=2.0, n=21):
        """The computed separation is noisy at ~1e-14 rad, which leaves the minimum of a slow
        eclipse uncertain by ~10 ms (a search over another period gave another time); the
        stationary point of a least-squares cubic through sep^2 (quadratic for uniform relative
        motion) over +-2 s fixes it to microseconds.  Kept when the fit fails or the event is
        shorter than the fit."""
        off = np.linspace(-half_s, half_s, n)
        x1 = np.array([e['x1'] for e in events])
        x4 = np.array([e['x4'] for e in events])
        ok = (xm - half_s / DAY_S >= x1) & (xm + half_s / DAY_S <= x4)
        if not np.any(ok):
            return xm
        xs = xm[ok]
        s2 = self.E.geom((xs[:, None] + off[None, :] / DAY_S).ravel())['sep'].reshape(xs.size, n) ** 2
        c3, c2, c1, _ = np.polyfit(off, (s2 - s2[:, n // 2:n // 2 + 1]).T, 3)
        t = np.zeros(xs.size)
        with np.errstate(divide='ignore', invalid='ignore'):
            for _ in range(3):              # Newton on the derivative c1 + 2 c2 t + 3 c3 t^2, from 0
                t = t - (c1 + 2 * c2 * t + 3 * c3 * t * t) / (2 * c2 + 6 * c3 * t)
        good = (c2 > 0) & np.isfinite(t) & (np.abs(t) < half_s)
        out = xm.copy()
        out[ok] = np.where(good, xs + t / DAY_S, xs)
        return out

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
        sens = self._sensitivities(events)
        pos = 0
        for e in events:
            pts = e.pop('_pts')
            contacts = []
            for label, x in pts:
                d = {key: (val[pos] if isinstance(val, (list, np.ndarray)) else val)
                     for key, val in det.items()}
                d.update(zip(('dt_drs', 'dt_drb'), sens[pos]))
                pos += 1
                d = {k: _py(v) for k, v in d.items()}
                d['label'] = label
                d['visible'] = any(a - 1e-9 <= x <= b + 1e-9 for a, b in e['visible'])
                contacts.append(d)
            e['contacts'] = contacts
        # A far observer also sees the Moon cross the Sun in months without an eclipse on the Earth,
        # which belong to no Saros series (every eclipse seen from the ground does).
        self._ecl_tc = None
        if self.body == 'moon' and self.observer.r_max_km > WGS84_A + 50:
            self._ecl_tc = eclipse_new_moons(self.ctx, [self.jd0 + e['xm'] for e in events], self.p)
        for e in events:
            self._summarise(e)

    def _sensitivities(self, events):
        """For the points of e['_pts']: (dt_drs, dt_drb), the change of the contact time (s) per
        +1" of the angular radius of the Sun / of the body; (None, None) for the maximum and for
        the ends of an event cut by the period.

        A contact is a root of f = sep - rs - rb (C1, C4), sep - (rs - rb) (C2, C3 inside the Sun)
        or sep - (rb - rs) (C2, C3 of a total eclipse); a radius larger by d moves it by -/+ d / f'.
        f' (arcsec/s, including the change of the radii) is a central difference over 1 s."""
        h = HALF_STEP_S / DAY_S
        xs, kinds, out = [], [], []
        for e in events:
            for label, x in e['_pts']:
                cut = ((label in ('C1', 'C2') and e.get('c1_cut') and x <= e['x1'])
                       or (label in ('C3', 'C4') and e.get('c4_cut') and x >= e['x4']))
                if label == 'MAX' or cut:
                    kinds.append(None)
                else:
                    kinds.append('ext' if label in ('C1', 'C4') else 'int')
                    xs.append(x)
        if xs:
            xs = np.array(xs)
            g = self.E.geom(np.r_[xs - h, xs + h])
            n = xs.size
            ext = (g['g_ext'][n:] - g['g_ext'][:n]) * RAD2ARCSEC / (2 * HALF_STEP_S)
            itl = (g['g_int'][n:] - g['g_int'][:n]) * RAD2ARCSEC / (2 * HALF_STEP_S)
            sun_larger = (g['rs'][:n] + g['rs'][n:]) > (g['rbi'][:n] + g['rbi'][n:])
        k = 0
        for kind in kinds:
            if kind is None:
                out.append((None, None))
                continue
            with np.errstate(divide='ignore'):
                if kind == 'ext':
                    out.append((1.0 / ext[k], 1.0 / ext[k]))
                else:
                    s = 1.0 if sun_larger[k] else -1.0      # transit / annular: +1, total: -1
                    out.append((s / itl[k], -s / itl[k]))
            k += 1
        return out

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
        saros = None
        if body == 'moon':
            tc = getattr(self, '_ecl_tc', None)
            if tc is None or (tc.size and np.abs(tc - (jd0 + e['xm'])).min() < 1.0):
                saros = saros_number(jd0 + e['xm'])
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
        # P angle: the Sun's rotation axis projected on the sky, from celestial north toward east;
        # turning (xi, eta) by it gives the solar-north-up X (toward solar west) / Y of solar images.
        pole = np.einsum('ij...,j->i...', TOD.rotation_at(t), SUN_POLE)
        P = np.arctan2(np.sum(pole * east, axis=0), np.sum(pole * north, axis=0))
        cp, sp = np.cos(P), np.sin(P)
        sep, rs, rb = g['sep'] * RAD2ARCSEC, g['rs'] * RAD2ARCSEC, g['rbe'] * RAD2ARCSEC
        out = dict(
            time=iso_utc(t), jd_tt=self.jd0 + x,
            sep_arcsec=sep, rho_s=rs, rho_b=rb, rho_b_int=g['rbi'] * RAD2ARCSEC,
            magnitude=magnitude(g['rs'], g['rbe'], g['sep']),
            obscuration=obscuration(g['rs'], g['rbe'], g['sep']),
            xi=xi, eta=eta, pa=pa, vis=g['vis'],
            p_angle=np.degrees(P), hpc_x=-(xi * cp - eta * sp), hpc_y=xi * sp + eta * cp,
            pa_solar=(pa - np.degrees(P)) % 360.0,
            limb_dist=sep - rs, limb_depth=(rs - sep) / rb,
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
            sep_e, reff, rn, pos = self.E.earth_view(g)
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

    def geometry_at(self, x):
        """details() at the times jd0 + x (e.g. those of images) and the motion there:
        hpc_vx, hpc_vy (solar-north-up X / Y, arcsec/s) and sep_rate (arcsec/s), by central
        differences over 1 s.  The observer must be prepared for these times."""
        x = np.atleast_1d(np.asarray(x, float))
        n, h = x.size, HALF_STEP_S / DAY_S
        d = self.details(np.concatenate([x, x - h, x + h]))
        out = {k: v[:n] if isinstance(v, (list, np.ndarray)) and len(v) == 3 * n else v
               for k, v in d.items()}
        for key, rate in (('hpc_x', 'hpc_vx'), ('hpc_y', 'hpc_vy'), ('sep_arcsec', 'sep_rate')):
            v = np.asarray(d[key], float)
            out[rate] = (v[2 * n:] - v[n:2 * n]) / (2 * HALF_STEP_S)
        return out

    def timeseries(self, e, n=480):
        x1, x4 = e['jd_c1'] - self.jd0, e['jd_c4'] - self.jd0
        dur = x4 - x1
        pad = max(dur * 0.08, 90.0 / DAY_S)
        lo, hi = x1 - pad, x4 + pad
        jd = getattr(self.observer.vf, 'jd', None)
        iv = self.observer.interval_at(e['jd_max'])
        if iv is not None:                  # CCSDS orbit data end where their data (or propagation) ends
            lo, hi = max(lo, iv[0] - self.jd0 + 1e-7), min(hi, iv[1] - self.jd0 - 1e-7)
        elif jd is not None and jd.size:    # tabulated orbits (Horizons, SSCWeb) end where their data ends
            lo, hi = max(lo, jd[0] - self.jd0 + 1e-7), min(hi, jd[-1] - self.jd0 - 1e-7)
        x = np.linspace(lo, hi, n)
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

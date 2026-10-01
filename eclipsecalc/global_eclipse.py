"""Global solar eclipse search (where on Earth is an eclipse visible?)."""
import math

import numpy as np

from .conjunctions import find_conjunctions, geocentric_min_separation
from .constants import DAY_S
from .geometry import norm
from .local import bisect_roots, golden_min
from .saros import saros_number
from .shadow import Shadow, envelope, local_magnitude, margin_at, sun_moon_itrs
from .timeutil import iso_utc

TYPE_JA = {'total': '皆既日食', 'annular': '金環日食', 'hybrid': '金環皆既日食',
           'partial': '部分日食'}


def _fund(ctx, params):
    def f(jd):
        S, M, _ = sun_moon_itrs(ctx, jd)
        return Shadow(S, M, params).fundamental()
    return f


def _first_last_crossings(x, v):
    """Brackets for the first downward and the last upward zero crossing."""
    neg = v < 0
    if not neg.any():
        return None, None
    i = int(np.argmax(neg))
    j = len(v) - 1 - int(np.argmax(neg[::-1]))
    down = (x[i - 1], x[i], v[i - 1]) if i > 0 else None
    up = (x[j], x[j + 1], v[j]) if j < len(v) - 1 else None
    return down, up


def search_global(ctx, jd_a, jd_b, params):
    jd_a = max(jd_a, ctx.jd_min + 1)
    jd_b = min(jd_b, ctx.jd_max - 1)
    tc = find_conjunctions(ctx, 'moon', jd_a - 1, jd_b + 1)
    smin, _ = geocentric_min_separation(ctx, 'moon', tc, 0.3)
    tc = tc[smin < math.radians(1.75)]
    if tc.size == 0:
        return []
    off = np.arange(-7 * 60, 7 * 60 + 1, 5) / 1440.0
    jd = (tc[:, None] + off[None, :])
    fund = _fund(ctx, params)
    f = fund(jd.ravel())
    n = tc.size
    mp = f['m_partial'].reshape(n, -1)
    mc = f['m_central'].reshape(n, -1)
    D = f['D'].reshape(n, -1)
    ecl = mp.min(axis=1) < 0
    idx = np.nonzero(ecl)[0]
    if idx.size == 0:
        return []
    jd, mp, mc, D, tc = jd[idx], mp[idx], mc[idx], D[idx], tc[idx]
    n = idx.size

    # Greatest eclipse: minimum distance of the shadow axis from the geocentre.
    k = np.argmin(D, axis=1)
    A = jd[np.arange(n), np.maximum(k - 1, 0)]
    B = jd[np.arange(n), np.minimum(k + 1, jd.shape[1] - 1)]
    jd_ge = golden_min(lambda x: fund(x), 'D', A, B, n_iter=40)

    # Global contacts P1/P4 (penumbra) and central-line begin/end.
    def crossings(m, key):
        downs, ups = [], []
        for i in range(n):
            d, u = _first_last_crossings(jd[i], m[i])
            downs.append(d)
            ups.append(u)
        res = {}
        for name, lst in (('begin', downs), ('end', ups)):
            sel = [i for i, b in enumerate(lst) if b is not None]
            if sel:
                a = np.array([lst[i][0] for i in sel])
                b = np.array([lst[i][1] for i in sel])
                ga = np.array([lst[i][2] for i in sel])
                r = bisect_roots(lambda x: fund(x), key, a, b, ga, tol_days=1e-8)
                res[name] = dict(zip(sel, r))
            else:
                res[name] = {}
        return res

    pc = crossings(mp, 'm_partial')
    cc = crossings(mc, 'm_central')

    S, M, t_ge = sun_moon_itrs(ctx, jd_ge)
    sh = Shadow(S, M, params)
    fg = sh.fundamental()
    hitP, _, hit = sh.axis_hit()
    near = sh.nearest_surface_point()
    central = np.array([i in cc['begin'] for i in range(n)]) & hit
    P = np.where(central[None, :], hitP, near)
    lm = local_magnitude(S, M, P, params)
    s_, r_, Rp_, Ru_ = sh.coords(P)
    umbral_touch = (r_ < np.abs(Ru_)) | central

    # Eclipse type along the central line (incl. its exact end points).
    types = []
    samp_jd, owner = [], []
    for i in range(n):
        if central[i]:
            a, b = cc['begin'][i], cc['end'].get(i, cc['begin'][i])
            pts = np.r_[a + 1e-7, np.linspace(a, b, 41)[1:-1], b - 1e-7]
            samp_jd.append(pts)
            owner += [i] * pts.size
    ru_sign = {}
    if samp_jd:
        sj = np.concatenate(samp_jd)
        S2, M2, _ = sun_moon_itrs(ctx, sj)
        sh2 = Shadow(S2, M2, params)
        P2, _, h2 = sh2.axis_hit()
        _, _, _, Ru2 = sh2.coords(P2)
        for o, ru, hh in zip(owner, Ru2, h2):
            if hh:
                ru_sign.setdefault(o, []).append(ru)
    for i in range(n):
        if central[i] and ru_sign.get(i):
            arr = np.array(ru_sign[i])
            if np.all(arr > 0):
                types.append('total')
            elif np.all(arr < 0):
                types.append('annular')
            else:
                types.append('hybrid')
        elif umbral_touch[i]:
            types.append('total' if Ru_[i] > 0 else 'annular')
        else:
            types.append('partial')

    # Central duration at the point of greatest eclipse (cone model, P fixed in ITRS).
    dur = np.zeros(n)
    sel = np.nonzero(umbral_touch)[0]
    if sel.size:
        Pf = P[:, sel]
        jg = jd_ge[sel]

        def fu(x, Pf=Pf):
            S3, M3, _ = sun_moon_itrs(ctx, x)
            return {'F': margin_at(Shadow(S3, M3, params), Pf, 'umbra')}

        w = 20.0 / 1440.0
        f0 = fu(jg)['F']
        fl = fu(jg - w)['F']
        fr = fu(jg + w)['F']
        ok = (f0 < 0) & (fl > 0) & (fr > 0)
        r1 = bisect_roots(fu, 'F', jg - w, jg, fl, tol_days=1e-9)
        r2 = bisect_roots(fu, 'F', jg, jg + w, f0, tol_days=1e-9)
        dur[sel] = np.where(ok, (r2 - r1) * DAY_S, 0.0)

    # Path width at greatest eclipse from the umbral envelope.
    width = np.full(n, np.nan)
    if sel.size:
        env, _ = envelope(ctx, jd_ge[sel], params, 'umbra', n_theta=720)
        dj = 30.0 / DAY_S
        S4, M4, _ = sun_moon_itrs(ctx, np.r_[jd_ge[sel] - dj, jd_ge[sel] + dj])
        sh4 = Shadow(S4, M4, params)
        P4, _, h4 = sh4.axis_hit()
        m = sel.size
        for j, i in enumerate(sel):
            pts = env[j]['points']
            if 'N' in pts and 'S' in pts:
                dvec = P4[:, m + j] - P4[:, j] if (h4[j] and h4[m + j]) else None
                chord = pts['N'] - pts['S']
                if dvec is not None and norm(dvec) > 0:
                    dh = dvec / norm(dvec)
                    chord = chord - np.dot(chord, dh) * dh
                width[i] = float(norm(chord))

    events = []
    iso_ge = iso_utc(t_ge)
    for i in range(n):
        if lm['magnitude'][i] <= 0 and types[i] == 'partial':
            continue
        if not (jd_a <= jd_ge[i] <= jd_b):
            continue
        typ = types[i]
        is_central = bool(central[i])
        mag = float(lm['ratio'][i]) if typ != 'partial' else float(lm['magnitude'][i])
        ev = dict(
            kind='global', body='moon', type=typ, type_ja=TYPE_JA[typ],
            central=is_central, noncentral=(typ != 'partial' and not is_central),
            jd_max=float(jd_ge[i]), max=iso_ge[i],
            gamma=float(fg['gamma'][i]), magnitude=mag,
            ratio=float(lm['ratio'][i]), ge_lat=float(lm['lat'][i]), ge_lon=float(lm['lon'][i]),
            sun_alt=float(lm['sun_alt'][i]),
            central_duration_s=float(dur[i]),
            path_width_km=None if not np.isfinite(width[i]) else float(width[i]),
            saros=saros_number(float(jd_ge[i])),
            delta_t_s=float(t_ge[i].delta_t),
        )
        for name, src, key in (('p1', pc, 'begin'), ('p4', pc, 'end'),
                               ('c_begin', cc, 'begin'), ('c_end', cc, 'end')):
            v = src[key].get(i)
            ev['jd_' + name] = None if v is None else float(v)
            ev[name] = None if v is None else iso_utc(ctx.ts.tt_jd(v))
        events.append(ev)
    return events

"""Map products: eclipse paths, limits, magnitude grids, transit visibility."""
import base64
import math

import numpy as np

from .constants import DAY_S
from .geometry import geodetic_normal, geodetic_to_itrs, itrs_to_geodetic, norm
from .global_eclipse import search_global
from .local import bisect_roots
from .shadow import Shadow, envelope, margin_at, sun_moon_itrs
from .timeutil import iso_utc

try:
    import contourpy
except ImportError:  # pragma: no cover
    contourpy = None


def _encode_u16(arr):
    a = np.clip(np.round(arr), 0, 65535).astype('<u2')
    return base64.b64encode(a.tobytes()).decode('ascii')


def _encode_u8(arr):
    a = np.clip(np.round(arr), 0, 255).astype('u1')
    return base64.b64encode(a.tobytes()).decode('ascii')


def _polyline_segments(lat, lon, jd=None, max_jump_deg=20.0):
    """Split a sampled curve into segments at gaps; longitudes are unwrapped
    so that a curve crossing the antimeridian stays continuous (lon may
    leave the -180..180 range, which Leaflet draws correctly)."""
    segs = []
    cur = []
    prev = None
    for i in range(len(lat)):
        if not np.isfinite(lat[i]):
            if len(cur) > 1:
                segs.append(cur)
            cur, prev = [], None
            continue
        lo = float(lon[i])
        if prev is not None:
            lo += 360.0 * round((prev[1] - lo) / 360.0)
        pt = [round(float(lat[i]), 5), round(lo, 5)]
        if jd is not None:
            pt.append(float(jd[i]))
        if prev is not None and (abs(pt[0] - prev[0]) > max_jump_deg or abs(pt[1] - prev[1]) > 60):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        cur.append(pt)
        prev = pt
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _contours(lats, lons, field, levels):
    out = []
    if contourpy is None:
        return out
    gen = contourpy.contour_generator(lons, lats, field, name='serial')
    for lv in levels:
        lines = gen.lines(lv)
        segs = []
        for ln in lines:
            if len(ln) < 2:
                continue
            segs.append([[round(float(p[1]), 3), round(float(p[0]), 3)] for p in ln])
        out.append({'level': lv, 'lines': segs})
    return out


def find_global_event(ctx, jd_near, params, days=2.0):
    evs = search_global(ctx, jd_near - days, jd_near + days, params)
    if not evs:
        return None
    return min(evs, key=lambda e: abs(e['jd_max'] - jd_near))


def solar_eclipse_map(ctx, jd_near, params, grid_deg=1.0, min_alt_deg=0.0, refraction=True,
                      event=None):
    ev = event or find_global_event(ctx, jd_near, params)
    if ev is None:
        return {'exists': False}
    p1 = ev['jd_p1'] or ev['jd_max'] - 3.5 / 24
    p4 = ev['jd_p4'] or ev['jd_max'] + 3.5 / 24
    res = {'exists': True, 'event': ev}

    # --- central line, umbral limits and outlines ----------------------------
    step = 30.0 / DAY_S
    x = np.arange(p1, p4 + step, step)
    S, M, t = sun_moon_itrs(ctx, x)
    sh = Shadow(S, M, params)
    fund = sh.fundamental()
    if ev['type'] != 'partial':
        P, _, hit = sh.axis_hit()
        lat, lon, _ = itrs_to_geodetic(P)
        lat = np.where(hit, lat, np.nan)
        res['central_line'] = _polyline_segments(lat, lon, x)
        # extend to the exact begin / end points of the central line
        umb = fund['m_umbral'] < 0.02
        xu = x[umb]
        if xu.size:
            env, _ = envelope(ctx, xu, params, 'umbra', n_theta=360)
            limits = {'N': [], 'S': []}
            for xi, e in zip(xu, env):
                for side in ('N', 'S'):
                    pt = e['points'].get(side)
                    if pt is None:
                        limits[side].append((np.nan, np.nan, xi))
                    else:
                        la, lo, _ = itrs_to_geodetic(pt[:, None])
                        limits[side].append((la[0], lo[0], xi))
            res['umbra_limits'] = {
                side: _polyline_segments(np.array([p[0] for p in v]), np.array([p[1] for p in v]),
                                         np.array([p[2] for p in v]))
                for side, v in limits.items()}
            # shadow outlines every 10 minutes (on round minutes)
            marks = np.arange(math.ceil(p1 * 144) / 144, p4, 1 / 144)
            outl = []
            if marks.size:
                env_m, _ = envelope(ctx, marks, params, 'umbra', n_theta=180)
                for xm, e in zip(marks, env_m):
                    if not e['hit'].any():
                        continue
                    la, lo, _ = itrs_to_geodetic(e['outline'])
                    la = np.where(e['hit'], la, np.nan)
                    segs = _polyline_segments(np.r_[la, la[:1]], np.r_[lo, lo[:1]])
                    if segs:
                        outl.append({'time': iso_utc(ctx.ts.tt_jd(xm)), 'lines': segs})
            res['umbra_outlines'] = outl
        # duration of the central phase along the central line (cone model)
        cl_x = x[hit][::4]
        if cl_x.size:
            Pc = P[:, hit][:, ::4]

            def fu(xx, Pc=Pc):
                S3, M3, _ = sun_moon_itrs(ctx, xx)
                return {'F': margin_at(Shadow(S3, M3, params), Pc, 'umbra')}
            w = 20.0 / 1440.0
            f0, fl, fr = fu(cl_x)['F'], fu(cl_x - w)['F'], fu(cl_x + w)['F']
            ok = (f0 < 0) & (fl > 0) & (fr > 0)
            r1 = bisect_roots(fu, 'F', cl_x - w, cl_x, fl, tol_days=2e-8)
            r2 = bisect_roots(fu, 'F', cl_x, cl_x + w, f0, tol_days=2e-8)
            la, lo, _ = itrs_to_geodetic(Pc)
            S5, M5, _ = sun_moon_itrs(ctx, cl_x)
            vS = S5 - Pc
            n = geodetic_normal(la, lo)
            alt = np.degrees(np.arcsin(np.sum(n * vS, axis=0) / norm(vS)))
            res['central_points'] = [
                {'time': iso_utc(ctx.ts.tt_jd(xx)), 'lat': float(a), 'lon': float(b),
                 'duration_s': float((q - p) * DAY_S) if k else None, 'sun_alt': float(h)}
                for xx, a, b, p, q, k, h in zip(cl_x, la, lo, r1, r2, ok, alt)]

    # --- penumbral (partial) limits -----------------------------------------
    pen = fund['m_partial'] < 0
    xp = x[pen][::2]
    if xp.size:
        env, _ = envelope(ctx, xp, params, 'penumbra', n_theta=360)
        limits = {'N': [], 'S': []}
        for xi, e in zip(xp, env):
            for side in ('N', 'S'):
                pt = e['points'].get(side)
                if pt is None:
                    limits[side].append((np.nan, np.nan))
                else:
                    la, lo, _ = itrs_to_geodetic(pt[:, None])
                    limits[side].append((la[0], lo[0]))
        res['penumbra_limits'] = {
            side: _polyline_segments(np.array([p[0] for p in v]), np.array([p[1] for p in v]))
            for side, v in limits.items()}

    # --- magnitude grid -------------------------------------------------------
    res['grid'] = magnitude_grid(ctx, p1, p4, params, grid_deg, min_alt_deg, refraction)
    return res


def magnitude_grid(ctx, p1, p4, params, grid_deg=1.0, min_alt_deg=0.0, refraction=True):
    lats = np.arange(-85.0, 85.0 + 1e-9, grid_deg)
    lons = np.arange(-180.0, 180.0 + 1e-9, grid_deg)
    LA, LO = np.meshgrid(lats, lons, indexing='ij')
    la, lo = LA.ravel(), LO.ravel()
    P = geodetic_to_itrs(la, lo, 0.0)
    nrm = geodetic_normal(la, lo)
    step = 2.0 / 1440.0
    xs = np.arange(p1, p4 + step, step)
    S, M, _ = sun_moon_itrs(ctx, xs)
    thr = min_alt_deg - (0.57 if refraction else 0.0)
    sin_thr = math.sin(math.radians(thr))
    K = la.size
    best = np.zeros(K)
    best_any = np.zeros(K)
    chunk = max(1, int(2.5e6 // max(xs.size, 1)))
    for c0 in range(0, K, chunk):
        c1 = min(K, c0 + chunk)
        Pc = P[:, c0:c1, None]
        vS = S[:, None, :] - Pc
        vM = M[:, None, :] - Pc
        dS = norm(vS)
        dM = norm(vM)
        dot = np.sum(vS * vM, axis=0)
        cr = norm(np.cross(vS, vM, axis=0))
        sep = np.arctan2(cr, dot)
        rs = np.arcsin(params.sun_radius_km / dS)
        rm = np.arcsin(params.moon_radius_ext_km / dM)
        mag = (rs + rm - sep) / (2 * rs)
        up = np.sum(nrm[:, c0:c1, None] * vS, axis=0) / dS > sin_thr
        best_any[c0:c1] = np.maximum(mag.max(axis=1), 0)
        mv = np.where(up, mag, -1.0)
        k = np.argmax(mv, axis=1)
        rows = np.arange(c1 - c0)
        mbest = mv[rows, k]
        # quadratic refinement of the minimum separation (sep^2 is ~quadratic in t)
        km, kp = np.maximum(k - 1, 0), np.minimum(k + 1, xs.size - 1)
        interior = (k > 0) & (k < xs.size - 1) & up[rows, km] & up[rows, kp]
        y0, y1, y2 = sep[rows, km] ** 2, sep[rows, k] ** 2, sep[rows, kp] ** 2
        den = y0 - 2 * y1 + y2
        with np.errstate(divide='ignore', invalid='ignore'):
            delta = np.where(den > 0, (y0 - y2) / (2 * den), 0.0)
            ymin = y1 - np.where(den > 0, (y0 - y2) ** 2 / (8 * den), 0.0)
        good = interior & (np.abs(delta) <= 1.0) & (ymin >= 0)
        sep_min = np.where(good, np.sqrt(np.maximum(ymin, 0)), sep[rows, k])
        mref = (rs[rows, k] + rm[rows, k] - sep_min) / (2 * rs[rows, k])
        best[c0:c1] = np.where(mbest > 0, np.maximum(mref, mbest), 0.0)
    field = best.reshape(LA.shape)
    return {
        'lat0': float(lats[0]), 'lon0': float(lons[0]), 'dlat': grid_deg, 'dlon': grid_deg,
        'nlat': int(lats.size), 'nlon': int(lons.size),
        'scale': 10000,
        'values': _encode_u16(field * 10000),
        'contours': _contours(lats, lons, field, [0.001, 0.2, 0.4, 0.6, 0.8]),
        'max_any': float(best_any.max()),
    }


def transit_map(ctx, jd_c1, jd_c4, jd_max, grid_deg=1.0, min_alt_deg=0.0, refraction=True):
    """Classify every location by which part of a transit it can see.

    Codes: 0 none, 1 entire transit, 2 in progress at sunrise (end visible),
    3 in progress at sunset (start visible), 4 only the middle part,
    5 start & end visible but interrupted by night.
    Geocentric contact times are used (topocentric times differ by
    a few minutes at most), as is customary for such maps.
    """
    lats = np.arange(-85.0, 85.0 + 1e-9, grid_deg)
    lons = np.arange(-180.0, 180.0 + 1e-9, grid_deg)
    LA, LO = np.meshgrid(lats, lons, indexing='ij')
    la, lo = LA.ravel(), LO.ravel()
    P = geodetic_to_itrs(la, lo, 0.0)
    nrm = geodetic_normal(la, lo)
    n = max(8, int((jd_c4 - jd_c1) * 144) + 2)
    xs = np.r_[np.linspace(jd_c1, jd_c4, n), jd_max]
    t = ctx.ts.tt_jd(xs)
    from skyfield.framelib import itrs
    S = ctx.earth.at(t).observe(ctx.sun).frame_xyz(itrs).km
    thr = min_alt_deg - (0.57 if refraction else 0.0)
    vS = S[:, None, :] - P[:, :, None]
    up = np.degrees(np.arcsin(np.sum(nrm[:, :, None] * vS, axis=0) / norm(vS))) > thr
    upm = up[:, :n]
    us, ue = upm[:, 0], upm[:, -1]
    anyup, allup = upm.any(axis=1), upm.all(axis=1)
    code = np.zeros(la.size)
    code[allup] = 1
    code[(~us) & ue & ~allup] = 2
    code[us & (~ue) & ~allup] = 3
    code[(~us) & (~ue) & anyup] = 4
    code[us & ue & ~allup] = 5
    sub = []
    for lbl, j in (('C1', 0), ('MAX', n), ('C4', n - 1)):
        v = S[:, j] / norm(S[:, j])
        sub.append({'label': lbl, 'lat': float(np.degrees(np.arcsin(v[2]))),
                    'lon': float(np.degrees(np.arctan2(v[1], v[0])))})
    return {
        'exists': True,
        'grid': {'lat0': float(lats[0]), 'lon0': float(lons[0]), 'dlat': grid_deg,
                 'dlon': grid_deg, 'nlat': int(lats.size), 'nlon': int(lons.size),
                 'codes': _encode_u8(code.reshape(LA.shape))},
        'subsolar': sub,
    }

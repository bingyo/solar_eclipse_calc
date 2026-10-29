"""Check eclipsecalc against the Hinode quick-look (QL) movies of NAOJ.

The QL movies (https://hinode.nao.ac.jp/en/for-researchers/qlmovies/, read
with tools/hinode_ql.py) carry the UT of every exposure to the millisecond
and helioprojective axes in arcsec.  Two measurements (docs/hinode_validation.md,
section 1):

* mercury: the 2006-11-08 transit of Mercury in the SOT/FG G-band movies of
  the ingress (19:10-19:17 UT) and egress (00:04-00:12).  In every frame the
  solar limb (inflection point of the limb darkening along solar radii) and
  Mercury's silhouette (circle fit to its edge) give d, the distance of
  Mercury's centre inside the limb.  d(t) = -r / +r (r = Mercury's measured
  radius) are the external / internal contacts, independent of the image
  scale; they are compared with the contacts computed for the default solar
  radius (visible limb, 959.96" at 1 AU), NASA's 959.63" and the IAU 2015
  nominal 695,700 km.  A joint fit of d(t) of all frames gives the radius of
  the visible limb, a time offset and the scale factor of the QL axes.
* eclipses: XRT movies of 2007-02-17 (an eclipse seen only from orbit),
  2007-03-19 (total in orbit), 2011-01-04 (annular), 2013-05-10, 2017-08-21,
  2020-06-21, 2022-10-25 and 2025-03-29.
  In every frame with the Moon on the Sun the centres of the lunar disk and
  of the X-ray Sun are fitted with circles of the computed angular radii (the
  radius of the X-ray limb is calibrated on frames of the same movie without
  the Moon).  The Moon-Sun offsets of a pass give the orbit time-shift s
  (seconds by which the actual Hinode was ahead of the SSCWeb orbit; 1 s =
  7.5 km) and the image rotation theta (expected: the solar P angle).  The
  frame time is the exposure start: the long exposures of the brighter movie
  of a pair give a larger s (2020-06-21; Ti poly against Al mesh 2013-05-10).

Usage:  python tools/validate_hinode_ql.py [mercury|eclipses]
Needs ffmpeg (FFMPEG, the PATH or C:\\ffmpeg\\bin).  On the first run the
movies (about 25 MB) are downloaded from hinode.nao.ac.jp into
data/cache/hinode_ql/ and Hinode's orbit from NASA SSCWeb; then the run takes
about 5 minutes.
"""
import argparse
import datetime as dt
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import hinode_ql as q  # noqa: E402
from eclipsecalc.constants import (AU_KM, RAD2ARCSEC, SUN_RADIUS_IAU2015,  # noqa: E402
                                   SUN_RADIUS_NASA, SUN_RADIUS_OBSERVED)
from eclipsecalc.local import Params  # noqa: E402
from validate_hinode_images import Predictor, rotate  # noqa: E402

RADII = (('959.96" (default)', SUN_RADIUS_OBSERVED), ('959.63" (NASA)', SUN_RADIUS_NASA),
         ('695,700 km (IAU 2015)', SUN_RADIUS_IAU2015))


def utc(t):
    return t.isoformat(timespec='milliseconds')


def hms(t):
    return t.strftime('%H:%M:%S.%f')[:-4]


# --- image helpers -------------------------------------------------------------------
def bilinear(a, x, y):
    """a[y, x] at fractional pixel positions (x = column, y = row); NaN outside."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    h, w = a.shape
    ok = (x >= 0) & (y >= 0) & (x <= w - 1.001) & (y <= h - 1.001)
    xi = np.clip(np.floor(x).astype(int), 0, w - 2)
    yi = np.clip(np.floor(y).astype(int), 0, h - 2)
    fx, fy = x - xi, y - yi
    v = (a[yi, xi] * (1 - fx) * (1 - fy) + a[yi, xi + 1] * fx * (1 - fy)
         + a[yi + 1, xi] * (1 - fx) * fy + a[yi + 1, xi + 1] * fx * fy)
    return np.where(ok, v, np.nan)


def smooth(a, s):
    """Separable Gaussian smoothing (sigma s px, 7-px kernel, edges repeated)."""
    k = np.exp(-np.arange(-3, 4) ** 2 / (2 * s * s))
    k /= k.sum()
    p = np.pad(a, 3, mode='edge')
    a = sum(w * p[i:i + a.shape[0], 3:-3] for i, w in enumerate(k))
    p = np.pad(a, ((0, 0), (3, 3)), mode='edge')
    return sum(w * p[:, i:i + a.shape[1]] for i, w in enumerate(k))


def subpix_peak(v, i):
    if 0 < i < v.size - 1:
        den = v[i - 1] - 2 * v[i] + v[i + 1]
        if den < 0:
            return i + 0.5 * (v[i - 1] - v[i + 1]) / den
    return float(i)


def fit_circle(x, y, R, c0, clip=2.5, floor=0.3, centred=False, window=None, rounds=30, free_r=False):
    """Robust circle of radius R (or free radius) through the points: Gauss-Newton from c0, then
    the points with |residual| >= max(clip * sigma, floor) are dropped and the fit repeated
    (sigma from the median absolute residual, about the median if centred).  window: start with
    the points within that distance of the circle about c0.  dict(cx, cy, r, n, rms, cov, keep, res)."""
    cx, cy, r = float(c0[0]), float(c0[1]), float(R)
    keep = np.ones(x.size, bool)
    if window is not None:
        keep = np.abs(np.hypot(x - cx, y - cy) - R) < window
        if keep.sum() < 8:
            keep[:] = True

    def jac():
        dx, dy = x[keep] - cx, y[keep] - cy
        d = np.hypot(dx, dy)
        J = np.c_[-dx / d, -dy / d, -np.ones(d.size)] if free_r else np.c_[-dx / d, -dy / d]
        return J, r - d
    for _ in range(rounds):
        for _ in range(30):
            J, f = jac()
            st = np.linalg.lstsq(J, f, rcond=None)[0]
            cx, cy = cx + st[0], cy + st[1]
            r += st[2] if free_r else 0.0
            if np.linalg.norm(st) < 1e-7:
                break
        res = np.hypot(x - cx, y - cy) - r
        m = np.median(res[keep]) if centred else 0.0
        s = 1.4826 * np.median(np.abs(res[keep] - m))
        new = np.abs(res - m) < max(clip * s, floor)
        if np.array_equal(new, keep) or new.sum() < 6:
            break
        keep = new
    J, _ = jac()
    sig = float(np.std(res[keep]))
    return dict(cx=cx, cy=cy, r=r, n=int(keep.sum()), rms=sig, keep=keep, res=res,
                cov=np.linalg.inv(J.T @ J) * sig ** 2)


# --- 2006-11-08 transit of Mercury (SOT/FG G band) ----------------------------------------
MERCURY_MOVIES = (('ingress', '2006/11/08/FG_GBM20061108191032_191701.mpg', ('C1', 'C2')),
                  ('egress', '2006/11/09/FG_GBM20061109000431_001204.mpg', ('C4', 'C3')))
R_LIMB = 969.0                  # curvature of the circles fitted to the limb (only the curvature matters)
SHELL = np.arange(1.5, 2.51, 0.1)


class Frame:
    """The plot area of a QL frame with arcsec coordinates (movie-mean scale, this frame's zero points)."""

    def __init__(self, img, ax, sx, sy):
        L, R, T, B = ax.box
        self.c0, self.r0 = L + 2, T + 2                 # inside the axis lines and ticks
        self.im = q.grey(img)[T + 2:B - 1, L + 2:R - 1]
        self.ims = smooth(self.im, 0.8)
        self.sx, self.sy = sx, sy
        cc, rc = 0.5 * (L + R), 0.5 * (T + B)
        Xc, Yc = ax.to_arcsec(cc, rc)
        self.X0, self.Y0 = Xc - sx * cc, Yc + sy * rc

    def xy(self, col, row):
        return self.X0 + self.sx * (np.asarray(col) + self.c0), self.Y0 - self.sy * (np.asarray(row) + self.r0)

    def sample(self, X, Y):
        return bilinear(self.ims, (np.asarray(X) - self.X0) / self.sx - self.c0,
                        (self.Y0 - np.asarray(Y)) / self.sy - self.r0)


def limb_fit(fr, centre, half, step, kind='inf', exclude=None, ds=0.04):
    """Centre of a circle of curvature R_LIMB through the limb points on radial lines from
    'centre' (R_LIMB +- half arcsec, one line per 'step' arcsec along the limb, lines where
    exclude(X, Y) skipped): kind 'inf' = the inflection point (outermost strong maximum of
    -dI/dr), 'half' = half-way between the mean levels 1.5-2.5" inside and outside of it.
    Returns ((X, Y), rms of the fit)."""
    h, w = fr.im.shape
    cX, cY = fr.xy(np.array([0, w - 1, 0, w - 1]), np.array([0, 0, h - 1, h - 1]))
    ang = np.arctan2(cY - centre[1], cX - centre[0])
    rr = np.arange(-half, half + 1e-9, ds)
    pts = []
    for a in np.arange(ang.min(), ang.max(), step / R_LIMB):
        ca, sa = math.cos(a), math.sin(a)
        v = fr.sample(centre[0] + (R_LIMB + rr) * ca, centre[1] + (R_LIMB + rr) * sa)
        if np.isnan(v).any() or (exclude is not None and exclude(centre[0] + R_LIMB * ca, centre[1] + R_LIMB * sa)):
            continue
        g = -np.gradient(v, ds)
        pk = np.nonzero((g[1:-1] >= g[:-2]) & (g[1:-1] >= g[2:]) & (g[1:-1] > 0.5 * g.max()))[0] + 1
        if not pk.size or not 5 <= pk.max() <= rr.size - 6:
            continue
        r = R_LIMB + rr[0] + ds * subpix_peak(g, int(pk.max()))
        if kind == 'half':
            vin = fr.sample(centre[0] + (r - SHELL) * ca, centre[1] + (r - SHELL) * sa)
            vout = fr.sample(centre[0] + (r + SHELL) * ca, centre[1] + (r + SHELL) * sa)
            mid = 0.5 * (vin.mean() + vout.mean())
            j = np.nonzero((v[:-1] >= mid) & (v[1:] < mid))[0]
            if np.isnan(mid) or not j.size:
                continue
            j = j[np.argmin(np.abs(R_LIMB + rr[j] - r))]
            r = R_LIMB + rr[j] + ds * (v[j] - mid) / (v[j] - v[j + 1])
        pts.append((centre[0] + r * ca, centre[1] + r * sa))
    p = np.array(pts)
    f = fit_circle(p[:, 0], p[:, 1], R_LIMB, centre, clip=3.0, floor=0.02, centred=True, rounds=6)
    return (f['cx'], f['cy']), f['rms']


def find_mercury(fr, sun_c):
    """First guess of Mercury's centre: centroid of the dark pixels inside the limb."""
    im = smooth(fr.im, 1.0)
    X, Y = fr.xy(*np.meshgrid(np.arange(im.shape[1]), np.arange(im.shape[0])))
    n = np.hypot(X - sun_c[0], Y - sun_c[1]) - R_LIMB
    i_ph, i_sky = np.median(im[(n > -8) & (n < -2)]), np.median(im[(n > 2) & (n < 8)])
    dark = (im < i_sky + 0.5 * (i_ph - i_sky)) & (n < -0.6)
    return np.array([X[dark].mean(), Y[dark].mean()]) if dark.sum() >= 10 else None


def mercury_edge(fr, sun_c, guess, r_fix=None, kind='grad', iters=4, ds=0.03):
    """Mercury's silhouette where it lies on the photosphere: edge points on rays from the
    centre (kind 'grad': maximum of dI/dr; 'half': half-way between the darkest level inside
    and the photosphere 0.7-1.2" outside) and a circle fit (radius r_fix or free), iterated.
    dict(cx, cy, r, n, cover (degrees of edge on the photosphere), rms) or None."""
    gx, gy = guess
    rr = np.arange(1.5, 9.0, ds)
    out = None
    for _ in range(iters):
        pts = []
        for th in np.radians(np.arange(0, 360, 2.0)):
            c, s = math.cos(th), math.sin(th)
            v = fr.sample(gx + rr * c, gy + rr * s)
            if np.isnan(v).any():
                continue
            g = np.gradient(v, ds)
            i = int(np.argmax(g))
            if i < 3 or i > rr.size - 4:
                continue
            re = rr[0] + ds * subpix_peak(g, i)
            if math.hypot(gx + (re + 0.7) * c - sun_c[0], gy + (re + 0.7) * s - sun_c[1]) - R_LIMB > -0.6:
                continue                               # the bright side must be photosphere
            if kind == 'half':
                inner, outer = rr[rr < re - 0.5], re + np.arange(0.7, 1.21, 0.05)
                vin, vout = fr.sample(gx + inner * c, gy + inner * s), fr.sample(gx + outer * c, gy + outer * s)
                if np.isnan(vin).all() or np.isnan(vout).all():
                    continue
                mid = 0.5 * (np.nanmin(vin) + np.nanmean(vout))
                j = np.nonzero((v[:-1] < mid) & (v[1:] >= mid))[0]
                if not j.size:
                    continue
                j = j[np.argmin(np.abs(rr[j] - re))]
                re = rr[j] + ds * (mid - v[j]) / (v[j + 1] - v[j])
            pts.append((gx + re * c, gy + re * s))
        if len(pts) < 8:
            return None
        X, Y = np.array(pts).T
        keep = np.ones(X.size, bool)
        for _ in range(8):                             # 3-sigma clipping around the circle
            if r_fix is None:
                A = np.c_[2 * X[keep], 2 * Y[keep], np.ones(keep.sum())]
                cx, cy, cc = np.linalg.lstsq(A, X[keep] ** 2 + Y[keep] ** 2, rcond=None)[0]
                r = math.sqrt(cc + cx * cx + cy * cy)
            else:
                f = fit_circle(X[keep], Y[keep], r_fix, (gx, gy), clip=3.0, floor=0.02, centred=True, rounds=6)
                cx, cy, r = f['cx'], f['cy'], r_fix
            res = np.hypot(X - cx, Y - cy) - r
            new = np.abs(res) < max(3 * 1.4826 * np.median(np.abs(res[keep])), 0.03)
            if np.array_equal(new, keep) or new.sum() < 8:
                break
            keep = new
        gx, gy = cx, cy
        out = dict(cx=cx, cy=cy, r=r, n=int(keep.sum()), cover=2.0 * len(pts), rms=float(np.std(res[keep])))
    return out


def mercury_rows(frames, r_fix=None):
    """Per frame: Mercury's circle fits and d, its centre's distance inside the limb (QL arcsec).
    r_fix None: free radius, edge 'grad' and 'half' (d to the limb at the inflection point);
    else radius r_fix, edge 'grad', d to the limb at the inflection point ('inf') and at half
    intensity ('limb_half').  The limb rms (inflection point) is kept for the selection."""
    sx, sy = q.mean_scale(frames)[:2]
    rows = []
    for t, img, ax in frames:
        if t is None or ax is None:
            continue
        F = Frame(img, ax, sx, sy)
        c2 = limb_fit(F, limb_fit(F, (0.0, 0.0), 15.0, 2.0)[0], 3.0, 0.4)[0]
        g = find_mercury(F, c2)
        row = dict(t=t)
        rows.append(row)
        if g is None:
            continue
        if r_fix is None:
            fits = {kind: mercury_edge(F, c2, g, kind=kind) for kind in ('grad', 'half')}
            fits = {k: m for k, m in fits.items() if m is not None and m['cover'] >= 60}
            limbs = {'half': ('inf',)}
        else:
            m = mercury_edge(F, c2, g, r_fix)
            if m is None or m['rms'] > 0.1:       # a small notch: start from points along the solar radius
                u = (g - c2) / np.linalg.norm(g - c2)
                for dl in (0.3, 0.8, 1.5, 2.5, 3.5):
                    m2 = mercury_edge(F, c2, g + u * (r_fix - dl), r_fix)
                    if m2 is not None and m2['n'] >= 12 and (m is None or m2['rms'] < m['rms']):
                        m = m2
            fits = {'grad': m} if m is not None else {}
            limbs = {'grad': ('inf', 'half')}
        for key, m in fits.items():
            row[key] = m
            excl = lambda X, Y, m=m: math.hypot(X - m['cx'], Y - m['cy']) < m['r'] + 1.5   # noqa: E731
            for kind in limbs.get(key, ()):
                c3, rms = limb_fit(F, c2, 3.0, 0.3, kind, excl)
                m['d_' + kind] = R_LIMB - math.hypot(m['cx'] - c3[0], m['cy'] - c3[1])
                if kind == 'inf':
                    m['limb_rms'] = rms
    return rows


def observed_contacts(t, d, r_obs, labels):
    """Times (s from the first frame) where a parabola through d(t) reaches -r_obs (labels[0])
    and +r_obs (labels[1]), with the statistical error."""
    c = np.polyfit(t, d, 2)
    sd = np.std(d - np.polyval(c, t))
    out = {}
    for lab, target in zip(labels, (-1, 1)):
        roots = np.roots(np.r_[c[:-1], c[-1] - target * r_obs])
        roots = roots[np.isreal(roots)].real
        tc = roots[np.argmin(np.abs(roots - t.mean()))]
        out[lab] = (tc, sd / abs(np.polyval(np.polyder(c), tc)) / math.sqrt(t.size))
    return out


def computed_contacts(pred, t0, t1, shift=0.0, step=0.5):
    """External / internal contacts in [t0, t1] (datetimes, linear interpolation on a step-s grid)."""
    ts = [t0 + dt.timedelta(seconds=float(x)) for x in np.arange(0.0, (t1 - t0).total_seconds(), step)]
    d = pred.details([utc(t) for t in ts], shift)
    out = {}
    for kind, sgn in (('ext', 1), ('int', -1)):
        g = d['rho_s'] + sgn * d['rho_b'] - d['sep_arcsec']
        k = np.nonzero(np.sign(g[:-1]) != np.sign(g[1:]))[0]
        if k.size:
            out[kind] = ts[k[0]] + dt.timedelta(seconds=step * g[k[0]] / (g[k[0]] - g[k[0] + 1]))
    return out


def joint_fit(rows):
    """d = kappa * (rho_s * (1 + e) - sep - tau * dsep/dt) for all frames (Gauss-Newton from the
    kappa = 1 solution): kappa = QL arcsec / true arcsec, e = relative change of the solar radius,
    tau = time offset (s, + = the observed d comes earlier)."""
    d, sep, rs, sdot = (np.array([r[k] for r in rows]) for k in ('d', 'sep', 'rs', 'sdot'))
    A = np.c_[rs, -sdot]
    e, tau = np.linalg.lstsq(A, d - rs + sep, rcond=None)[0]
    k = 1.0
    for _ in range(20):
        base = rs * (1 + e) - sep - tau * sdot
        J = np.c_[base, k * rs, -k * sdot]
        st = np.linalg.lstsq(J, d - k * base, rcond=None)[0]
        k, e, tau = k + st[0], e + st[1], tau + st[2]
    res = d - k * (rs * (1 + e) - sep - tau * sdot)
    err = np.sqrt(np.diag(np.linalg.inv(J.T @ J) * np.var(res, ddof=3)))
    return dict(kappa=k, e=e, tau=tau, err=err, rms=float(res.std()), n=d.size)


def mercury_2006():
    print('2006-11-08 transit of Mercury: SOT/FG G-band QL movies (ingress, egress)')
    preds = {lab: Predictor('mercury', '2006-11-08T18:00', '2006-11-09T01:30',
                            Params(include_invisible=True, sun_radius_km=R)) for lab, R in RADII}
    pred = preds[RADII[0][0]]
    movies = []
    for name, path, labels in MERCURY_MOVIES:
        frames = q.movie_frames(f'{q.QL_BASE}/{path}')
        movies.append(dict(name=name, labels=labels, frames=frames, free=mercury_rows(frames)))
    full = lambda rows, key: [r[key]['r'] for r in rows if key in r and r[key]['cover'] >= 359 and r[key]['rms'] < 0.1]  # noqa: E731
    r_fix = float(np.mean(sum((full(mv['free'], 'grad') for mv in movies), [])))
    for mv in movies:
        mv['fix'] = mercury_rows(mv['frames'], r_fix)
        mv['r_obs'] = {key: np.mean(full(mv['free'], key)) for key in ('grad', 'half')}
        mv['n_full'] = len(full(mv['free'], 'grad'))
    # (label, frame rows, edge fit, d, Mercury's radius from the free fits)
    methods = (('main', 'fix', 'grad', 'd_inf', 'grad'), ('limb at half intensity', 'fix', 'grad', 'd_half', 'grad'),
               ('edge of Mercury at half intensity', 'free', 'half', 'd_inf', 'half'))
    obs, fits, per_movie = {}, {}, {}
    for label, rows_key, edge, dkey, rkey in methods:
        allrows = []
        for mv in movies:
            sel = [r for r in mv[rows_key] if edge in r and r[edge]['rms'] <= 0.08 and r[edge]['n'] >= 20
                   and r[edge]['limb_rms'] <= 0.1]
            t0 = mv['frames'][0][0]
            p = pred.details([utc(r['t']) for r in sel])
            t = np.array([(r['t'] - t0).total_seconds() for r in sel])
            d = np.array([r[edge][dkey] for r in sel])
            for lab, (tc, err) in observed_contacts(t, d, mv['r_obs'][rkey], mv['labels']).items():
                obs.setdefault(lab, []).append((t0 + dt.timedelta(seconds=float(tc)), err))
            sdot = np.gradient(p['sep_arcsec'], t)
            allrows += [dict(d=d[i], sep=p['sep_arcsec'][i], rs=p['rho_s'][i], sdot=sdot[i]) for i in range(t.size)]
            if label == 'main':
                per_movie[mv['name']] = dict(n=t.size, rho_b=float(p['rho_b'].mean()), dsun=float(p['sun_dist_km'].mean()))
        fits[label] = joint_fit(allrows)
    print(f"  Mercury's radius from free circle fits (frames with the whole disk on the Sun): "
          + ', '.join(f"{mv['name']} {mv['r_obs']['grad']:.4f}\" ({mv['n_full']} frames)" for mv in movies)
          + f'; fixed-radius fits use {r_fix:.4f}" (QL arcsec)')
    print('  frames used: ' + ', '.join(f"{k} {v['n']} of {len(mv['frames'])}" for (k, v), mv in zip(per_movie.items(), movies)))
    comp = {}
    for lab, _ in RADII:
        for mv in movies:
            c = computed_contacts(preds[lab], mv['frames'][0][0], mv['frames'][-1][0])
            comp.setdefault(lab, {}).update(zip(mv['labels'], (c.get('ext'), c.get('int'))))
    print('  contact  observed (QL movies)                      ' + ''.join(f'computed {lab:24s}' for lab, _ in RADII))
    for lab in ('C1', 'C2', 'C3', 'C4'):
        (to, err), *others = obs[lab]
        alt = ', '.join(f'{(o - to).total_seconds():+.2f}' for o, _ in others)
        line = f'  {lab}       {hms(to)} +-{err:.2f} s (other methods {alt} s)  '
        for name, _ in RADII:
            tc = comp[name][lab]
            line += f'  {hms(tc)} (O-C {(to - tc).total_seconds():+5.2f} s)     '
        print(line.rstrip())
    f = fits['main']
    R = SUN_RADIUS_OBSERVED * (1 + f['e'])
    alt = ', '.join(f"{SUN_RADIUS_OBSERVED * (1 + fits[k]['e']):,.0f}" for k, *_ in methods[1:])
    print(f"  joint fit of d(t) ({f['n']} frames): radius of the visible limb {R:,.0f} +- {SUN_RADIUS_OBSERVED * f['err'][1]:.0f} km "
          f"= {R / AU_KM * RAD2ARCSEC:.2f}\" at 1 AU (other methods {alt} km); time offset {f['tau']:+.2f} +- {f['err'][2]:.2f} s; "
          f"QL scale kappa = {f['kappa']:.5f} +- {f['err'][0]:.5f} (true arcsec = QL x {1 / f['kappa']:.4f}); rms {f['rms']:.3f}\"")
    print("  Mercury's radius / kappa: " + ', '.join(
        f"{mv['name']} {mv['r_obs']['grad'] / f['kappa']:.3f}\" (computed {per_movie[mv['name']]['rho_b']:.3f}\"; "
        f"size alone gives kappa = {mv['r_obs']['grad'] / per_movie[mv['name']]['rho_b']:.4f})" for mv in movies))
    d_rs = (SUN_RADIUS_OBSERVED - SUN_RADIUS_NASA) / AU_KM * RAD2ARCSEC
    sens = [abs((comp[RADII[0][0]][lab] - comp[RADII[1][0]][lab]).total_seconds()) * 0.1 / d_rs for lab in ('C1', 'C2', 'C3', 'C4')]
    shift = {}
    for mv in movies:
        c = computed_contacts(pred, mv['frames'][0][0], mv['frames'][-1][0], shift=10.0)
        shift.update(zip(mv['labels'], (c.get('ext'), c.get('int'))))
    print('  sensitivity: 0.1" (at 1 AU) of the solar radius moves the contacts by '
          + '/'.join(f'{x:.1f}' for x in sens) + ' s; the orbit shifted by 10 s (75 km) moves them by '
          + '/'.join(f"{(shift[lab] - comp[RADII[0][0]][lab]).total_seconds():+.2f}" for lab in ('C1', 'C2', 'C3', 'C4')) + ' s')


# --- 2017-2025 eclipses (XRT) ----------------------------------------------------------
XRT_MOVIES = (   # QL movie, role in a pair of movies taken together (longer exposure = brighter), maxima of the passes
    ('XRT_AMM20070217160100_162607.mpg', 'single', ('2007-02-17T16:10',)),     # seen only from orbit
    ('XRT_APM20070319024759_030354.mpg', 'single', ('2007-03-19T02:55',)),     # total in orbit
    ('XRT_TIM20110104090615_092549.mpg', 'single', ('2011-01-04T09:16',)),     # annular in orbit
    ('XRT_AMM20130510013905_182335.mpg', 'short', ('2013-05-10T01:48',)),      # Al mesh and Ti poly together
    ('XRT_TIM20130510013857_182407.mpg', 'long', ('2013-05-10T01:48',)),
    ('XRT_APM20170821061950_185419.mpg', 'short', ('2017-08-21T16:57',)),
    ('XRT_APM20200621090843_092939.mpg', 'short', ('2020-06-21T09:16',)),
    ('XRT_APM20200621090846_092912.mpg', 'long', ('2020-06-21T09:16',)),
    ('XRT_APM20221025083311_092537.mpg', 'short', ('2022-10-25T09:07',)),
    ('XRT_APM20221025102555_174519.mpg', 'single', ('2022-10-25T10:36', '2022-10-25T12:03')),
    ('XRT_APM20250329033322_175013.mpg', 'short', ('2025-03-29T09:25', '2025-03-29T10:52', '2025-03-29T12:20')),
    ('XRT_APM20250329033328_175019.mpg', 'long', ('2025-03-29T09:25', '2025-03-29T10:52', '2025-03-29T12:20')),
)
MARGIN = dt.timedelta(minutes=8)       # frames from C1 - MARGIN to C4 + MARGIN
SIG_RS = 0.3                           # px: uncertainty of the X-ray limb radius during the eclipse


def edge_scan(a, inside, c, R, band, avoid=None, win=(2.0, 8.0), step=0.25):
    """Dark-to-bright edge points on rays (every degree) from c = (col, row) across R +- band px:
    maximum of dI/dr of the profile (box-smoothed over 1.5 px) away from the ends.  Rows
    (col, row, r, contrast); contrast = mean win[0]-win[1] px outside - inside."""
    rr = np.arange(R - band, R + band + 1e-9, step)
    n0, n1 = int(win[0] / step), int(win[1] / step)
    lo, hi = n1 + 1, rr.size - n1 - 1
    h, w = a.shape
    out = []
    for th in np.radians(np.arange(0, 360, 1.0)):
        x, y = c[0] + rr * math.cos(th), c[1] + rr * math.sin(th)
        if hi <= lo or x.min() < 0 or y.min() < 0 or x.max() > w - 2 or y.max() > h - 2:
            continue
        if not inside[np.round(y).astype(int), np.round(x).astype(int)].all() or (avoid is not None and avoid(x, y).any()):
            continue
        v = bilinear(a, x, y)
        g = np.gradient(np.convolve(v, np.ones(6) / 6, mode='same'), step)
        j = lo + int(np.argmax(g[lo:hi]))
        den = g[j - 1] - 2 * g[j] + g[j + 1]
        r = rr[j] + (np.clip(0.5 * (g[j - 1] - g[j + 1]) / den, -1, 1) * step if den < 0 else 0.0)
        out.append((c[0] + r * math.cos(th), c[1] + r * math.sin(th), r,
                    v[j + n0:j + n1].mean() - v[max(j - n1, 0):j - n0].mean()))
    return np.array(out).reshape(-1, 4)


def measure_xrt(img, ax, moon, sun, Rm, rs, free_sun=False):
    """Centres (px) of the lunar disk (radius Rm px) and of the X-ray limb (radius rs px, or free),
    iterated from the guesses.  dict(moon, sun, fm, fs, sp (limb points of fs), rs)."""
    a = q.grey(img)
    L, R, T, B = ax.box
    inside = np.zeros(a.shape, bool)
    inside[T + 2:B - 1, L + 2:R - 1] = True
    con = 2 * (1.4826 * np.median(np.abs(np.diff(a[T + 2:B - 1, L + 2:R - 1], axis=1))) / math.sqrt(2) + 0.5)
    moon, sun = np.array(moon, float), np.array(sun, float)
    fm = fs = sp_fit = None
    for it in range(5):
        band = 12 if it == 0 else 6
        mp = edge_scan(a, inside, moon, Rm, band)
        mp = mp[(np.hypot(mp[:, 0] - sun[0], mp[:, 1] - sun[1]) < rs - 3.0) & (mp[:, 3] > con)]
        if len(mp) >= 8:
            fm = fit_circle(mp[:, 0], mp[:, 1], Rm, moon, window=max(6.0, 0.02 * Rm))
            moon = np.array([fm['cx'], fm['cy']])
        sp = edge_scan(a, inside, sun, rs, band, win=(1.5, 6.0),
                       avoid=lambda x, y: np.hypot(x - moon[0], y - moon[1]) < Rm + 5.0)
        sp = sp[sp[:, 3] > con]
        if len(sp) >= 8:
            fs = fit_circle(sp[:, 0], sp[:, 1], rs, sun, window=max(6.0, 0.02 * rs), free_r=free_sun)
            sun, sp_fit = np.array([fs['cx'], fs['cy']]), sp
            rs = fs['r'] if free_sun else rs
    return dict(moon=moon, sun=sun, fm=fm, fs=fs, sp=sp_fit, rs=rs)


def predicted_px(d, j, ax, sun_hpc):
    """Guesses (px) of the Moon and Sun centres and their radii (px) in frame j."""
    sc = 0.5 * (ax.scale_x + ax.scale_y)
    E, N = rotate((d['xi'][j], d['eta'][j]), d['p_angle'][j])
    return (ax.to_pixel(sun_hpc[0] - E, sun_hpc[1] + N), ax.to_pixel(*sun_hpc),
            d['rho_b'][j] / sc, d['rho_s'][j] / sc, sc)


def calibrate(sel, d, clear):
    """X-ray limb radius offset (px, joint fit to the limb of up to 30 frames without the Moon)
    and the Sun centre (HPC arcsec, median of those frames)."""
    pick = clear[:15] + clear[-15:] if len(clear) > 30 else clear
    pts = []
    for j in pick:
        t, img, ax = sel[j]
        moon0, sun0, Rm, Rs, _ = predicted_px(d, j, ax, (0.0, 0.0))
        res = measure_xrt(img, ax, moon0, sun0, Rm, Rs, free_sun=True)
        if res['fs'] and res['fs']['n'] >= 20:
            pts.append((res['sp'][res['fs']['keep']], res['sun'], Rs, ax))
    if not pts:
        return 0.0, np.zeros(2), 0
    fit = lambda p, c0, R, **kw: fit_circle(p[:, 0], p[:, 1], R, c0, window=max(6.0, 0.02 * R), **kw)  # noqa: E731
    grid = np.arange(-1.5, 2.51, 0.05)
    chi = np.array([sum(np.sum(fit(p, c0, Rs + o, clip=1e9)['res'] ** 2) for p, c0, Rs, _ in pts) for o in grid])
    jj = int(np.argmin(chi))
    a, b, _ = np.polyfit(grid[max(jj - 4, 0):jj + 5], chi[max(jj - 4, 0):jj + 5], 2)
    off = -b / (2 * a)
    cen = [ax.to_arcsec(f['cx'], f['cy']) for f, ax in ((fit(p, c0, Rs + off), ax) for p, c0, Rs, ax in pts)]
    return float(off), np.median(np.array(cen, float), axis=0), len(pts)


def measure_pass(sel, d, todo, sun_hpc, rs_off):
    """Moon - Sun offsets (E, N: solar east / north, arcsec) of the frames 'todo'."""
    rows = []
    for j in todo:
        t, img, ax = sel[j]
        moon0, sun0, Rm, Rs, sc = predicted_px(d, j, ax, sun_hpc)
        try:
            res = measure_xrt(img, ax, moon0, sun0, Rm, Rs + rs_off)
        except (np.linalg.LinAlgError, ValueError, IndexError):
            continue
        fm, fs = res['fm'], res['fs']
        if fm is None or fs is None or fm['n'] < 20 or fs['n'] < 20:
            continue
        (sX, sY), (mX, mY) = ax.to_arcsec(*res['sun']), ax.to_arcsec(*res['moon'])
        err = math.sqrt(np.trace(fm['cov']) + np.trace(fs['cov'][:2, :2])) * sc / math.sqrt(2)
        rows.append(dict(j=int(j), time=utc(t), E=float(-(mX - sX)), N=float(mY - sY), err=float(err), P=float(d['p_angle'][j])))
    return rows


def fit_shift(pred, rows, scale=False, errfloor=2.0):
    """Orbit time-shift s (s), image rotation theta (deg) and optionally a scale k fitted to the
    measured offsets: chi2 on a 0.5-s grid of s, a parabola through its minimum; the error of s
    is scaled by the reduced chi2."""
    times = [r['time'] for r in rows]
    M = np.array([[r['E'], r['N']] for r in rows])
    W = 1 / np.hypot([r['err'] for r in rows], errfloor)
    w2 = (W ** 2)[:, None]

    def at(s):
        d = pred.details(times, s)
        px, py = d['xi'], d['eta']
        th = math.atan2(np.sum(w2[:, 0] * (px * M[:, 1] - py * M[:, 0])), np.sum(w2[:, 0] * (px * M[:, 0] + py * M[:, 1])))
        Rv = np.c_[math.cos(th) * px - math.sin(th) * py, math.sin(th) * px + math.cos(th) * py]
        k = np.sum(w2 * Rv * M) / np.sum(w2 * Rv * Rv) if scale else 1.0
        res = M - k * Rv
        return math.degrees(th), k, res, float(np.sum((W[:, None] * res) ** 2))
    grid = np.arange(-20.0, 20.01, 0.5)
    chi = np.array([at(s)[3] for s in grid])
    j = int(np.argmin(chi))
    sl = slice(max(j - 3, 0), min(j + 4, grid.size))
    a, b, c = np.polyfit(grid[sl], chi[sl], 2)
    dof = max(2 * len(rows) - (3 if scale else 2), 1)
    best = -b / (2 * a) if a > 0 else grid[j]
    s_err = math.sqrt(max((c - b * b / (4 * a)) / dof, 1e-12) / a) if a > 0 else float('nan')
    th, k, res, _ = at(best)
    return dict(s=float(best), s_err=s_err, theta=th, k=k, n=len(rows), rms=float(np.sqrt(np.mean(np.sum(res ** 2, axis=1)))))


def pass_window(pred, tmax):
    """Computed C1 and C4 (datetimes) of the pass with its maximum near tmax."""
    ts = [tmax + dt.timedelta(seconds=float(x)) for x in np.arange(-1800.0, 1800.1, 5.0)]
    d = pred.details([utc(t) for t in ts])
    g = d['sep_arcsec'] - d['rho_s'] - d['rho_b']
    k = np.nonzero(np.sign(g[:-1]) != np.sign(g[1:]))[0]
    cross = [ts[i] + dt.timedelta(seconds=5.0 * g[i] / (g[i] - g[i + 1])) for i in k]
    return cross[0], cross[-1]


def xrt_movie_pass(frames, pred, tmax):
    """Measure one movie over one pass and fit s and theta; dict of the results."""
    c1, c4 = pass_window(pred, tmax)
    sel = [f for f in frames if f[0] and f[2] and c1 - MARGIN <= f[0] <= c4 + MARGIN]
    d = pred.details([utc(t) for t, _, _ in sel])
    near = d['sep_arcsec'] <= d['rho_s'] + d['rho_b'] + 20
    rs_off, sun_hpc, ncal = calibrate(sel, d, list(np.nonzero(~near)[0]))
    todo = list(np.nonzero(near)[0])
    rows = measure_pass(sel, d, todo, sun_hpc, rs_off)
    if len(rows) < 3:
        return dict(c1=c1, c4=c4, n=len(rows), ok=False)
    f = fit_shift(pred, rows)
    s2 = [fit_shift(pred, measure_pass(sel, d, todo, sun_hpc, rs_off + dr))['s'] for dr in (-0.2, 0.2)]
    dsdr = (s2[1] - s2[0]) / 0.4
    imgs = [sel[r['j']] for r in rows[::max(1, len(rows) // 8)]]
    p75 = np.median([np.percentile(img.astype(int).sum(axis=2)[ax.box[2] + 2:ax.box[3] - 1, ax.box[0] + 2:ax.box[1] - 1], 75)
                     for _, img, ax in imgs])
    f.update(c1=c1, c4=c4, rs_off=rs_off, sun_hpc=sun_hpc, ncal=ncal, dsdr=dsdr, k=fit_shift(pred, rows, scale=True)['k'],
             s_tot=math.hypot(f['s_err'], SIG_RS * dsdr), dtheta=f['theta'] - float(np.mean([r['P'] for r in rows])),
             p75=float(p75), t0=rows[0]['time'], t1=rows[-1]['time'], rows=rows)
    f['ok'] = f['n'] >= 8 and f['rms'] <= 12 and f['s_tot'] <= 1.5
    return f


def wmean(vals):
    """Weighted mean of (value, error) and its error, scaled by sqrt(chi2/dof) when > 1."""
    v, e = np.array(vals).T
    w = 1 / e ** 2
    m = float(np.sum(w * v) / np.sum(w))
    chi = float(np.sum(((v - m) / e) ** 2))
    return m, math.sqrt(1 / np.sum(w)) * math.sqrt(max(chi / (v.size - 1), 1.0) if v.size > 1 else 1.0), v.size


def eclipses():
    print('Eclipses 2007-2025: XRT QL movies, Moon-Sun offsets -> orbit time-shift s and image rotation theta')
    print('  pass (max, UTC)   movie                  role    p75  frames  limb off.  s (s)                     theta-P  rms    k')
    preds, results = {}, []
    for fname, role, maxima in XRT_MOVIES:
        url = f'{q.QL_BASE}/{fname[7:11]}/{fname[11:13]}/{fname[13:15]}/{fname}'
        frames = q.movie_frames(url)
        for tm in maxima:
            tmax = dt.datetime.fromisoformat(tm)
            if tm not in preds:
                preds[tm] = Predictor('moon', utc(tmax - dt.timedelta(minutes=45)), utc(tmax + dt.timedelta(minutes=45)))
            r = xrt_movie_pass(frames, preds[tm], tmax)
            r.update(event=tm[:10], tmax=tm, movie=fname[4:25], role=role)
            results.append(r)
            if not r.get('s_err'):
                print(f"  {tm.replace('T', ' ')}  {r['movie']}  {role:6s}  too few measured frames ({r['n']})")
                continue
            print(f"  {tm.replace('T', ' ')}  {r['movie']}  {role:6s} {r['p75']:4.0f}  {r['n']:3d}/{r['ncal']:2d}   {r['rs_off']:+5.2f} px  "
                  f"{r['s']:+6.2f} +- {r['s_tot']:.2f} (stat {r['s_err']:.2f}, limb {SIG_RS * abs(r['dsdr']):.2f})  "
                  f"{r['dtheta']:+5.2f}   {r['rms']:4.1f}\"  {r['k']:.4f}{'' if r['ok'] else '  (fails the quality cut)'}")
    print('  (frames: measured / without the Moon for the limb calibration; s +- total error = statistical and '
          f'{SIG_RS} px of the X-ray limb radius; quality cut: >= 8 frames, rms <= 12", error <= 1.5 s)')
    print('  event       short exposure (dim or single movie)    long exposure (bright movie)')
    for ev in sorted({r['event'] for r in results}):
        line = f'  {ev}'
        for roles in (('short', 'single'), ('long',)):
            v = [(r['s'], r['s_tot']) for r in results if r['event'] == ev and r['role'] in roles and r['ok']]
            line += f"  {'%+.2f +- %.2f s (%d pass%s)' % (*wmean(v), '' if len(v) == 1 else 'es') if v else '-':38s}"
        print(line)
    for tm in sorted({r['tmax'] for r in results}):
        pair = {r['role']: r for r in results if r['tmax'] == tm and r['ok']}
        if 'short' in pair and 'long' in pair:
            print(f"  pass {tm}: s(long) - s(short) = {pair['long']['s'] - pair['short']['s']:+.2f} +- "
                  f"{math.hypot(pair['long']['s_tot'], pair['short']['s_tot']):.2f} s (display level p75 "
                  f"{pair['short']['p75']:.0f} / {pair['long']['p75']:.0f})")


def main():
    ap = argparse.ArgumentParser(description='Check eclipsecalc against the Hinode QL movies.')
    ap.add_argument('part', nargs='?', default='all', choices=('all', 'mercury', 'eclipses'))
    part = ap.parse_args().part
    q.find_ffmpeg()
    t0 = time.time()
    if part in ('all', 'mercury'):
        mercury_2006()
    if part in ('all', 'eclipses'):
        eclipses()
    print(f'({time.time() - t0:.0f} s)')


if __name__ == '__main__':
    main()

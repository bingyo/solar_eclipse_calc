"""Lunar shadow-cone geometry in the Earth-fixed (ITRS) frame.

The penumbral / umbral cones are the cones tangent to the solar and lunar
spheres.  A point lies inside the penumbra exactly when the two apparent
disks overlap, so this formulation is equivalent to the Besselian
elements but works directly with vectors and the WGS84 ellipsoid.
Sun and Moon positions are geocentric apparent positions rotated into ITRS
(precession-nutation, Delta T, Earth rotation), as for Besselian elements.
"""
import numpy as np
from skyfield.framelib import itrs

from .constants import WGS84_A
from .geometry import ELLIPSOID_SCALE, itrs_to_geodetic, line_ellipsoid_intersection, norm

ZHAT = np.array([0.0, 0.0, 1.0])[:, None]
TSCALE = ELLIPSOID_SCALE[:, None]


def sun_moon_itrs(ctx, jd):
    jd = np.atleast_1d(np.asarray(jd, float))
    t = ctx.ts.tt_jd(jd)
    e = ctx.earth.at(t)
    # Apparent geocentric places: in the Earth's rest frame these are the true
    # directions of the incoming light, so the Earth-fixed cone geometry is
    # correct to ~20 m (the BCRS-astrometric alternative would err by
    # v_orbit x differential light time ~ 0.6 km across the Earth's disk).
    S = e.observe(ctx.sun).apparent().frame_xyz(itrs).km
    M = e.observe(ctx.moon).apparent().frame_xyz(itrs).km
    return S, M, t


class Shadow:
    def __init__(self, S, M, params):
        self.S, self.M = S, M
        G = S - M
        self.dSM = norm(G)
        self.u = -G / self.dSM
        Rs = params.sun_radius_km
        self.Rme, self.Rmi = params.moon_radius_ext_km, params.moon_radius_int_km
        self.sf1 = (Rs + self.Rme) / self.dSM
        self.cf1 = np.sqrt(1 - self.sf1 ** 2)
        self.tf1 = self.sf1 / self.cf1
        self.sf2 = (Rs - self.Rmi) / self.dSM
        self.cf2 = np.sqrt(1 - self.sf2 ** 2)
        self.tf2 = self.sf2 / self.cf2
        e1 = np.cross(ZHAT, self.u, axis=0)
        self.e1 = e1 / norm(e1)
        self.e2 = np.cross(self.u, self.e1, axis=0)

    # --- fundamental-plane quantities ---------------------------------------
    def fundamental(self):
        M, u = self.M, self.u
        mu = np.sum(M * u, axis=0)
        Q = M - mu * u                     # foot of the axis nearest the geocentre
        D = norm(Q) / WGS84_A
        Mp, up = M * TSCALE, u * TSCALE
        sp = -np.sum(Mp * up, axis=0) / np.sum(up * up, axis=0)
        Qp = Mp + sp * up
        Dp = norm(Qp) / WGS84_A
        s0 = -mu
        L1 = self.Rme / self.cf1 + s0 * self.tf1
        L2 = self.Rmi / self.cf2 - s0 * self.tf2
        qn = norm(Q)
        qhat = Q / np.where(qn > 0, qn, 1.0)
        kq = np.where(qn > 0, norm(qhat * TSCALE), 1.0)
        north = np.sign(np.sum(Q * ZHAT, axis=0))
        return dict(D=D, Dp=Dp, L1=L1, L2=L2,
                    m_partial=Dp - 1 - L1 * kq / WGS84_A,
                    m_central=Dp - 1,
                    m_umbral=Dp - 1 - np.abs(L2) * kq / WGS84_A,
                    gamma=D * np.where(north == 0, 1, north), Qp=Qp)

    def coords(self, P):
        """Axial coordinate s, radial distance r and cone radii at points P."""
        w = P - self.M
        s = np.sum(w * self.u, axis=0)
        r = norm(w - s * self.u)
        Rp = self.Rme / self.cf1 + s * self.tf1
        Ru = self.Rmi / self.cf2 - s * self.tf2
        return s, r, Rp, Ru

    def axis_hit(self):
        return line_ellipsoid_intersection(self.M, self.u)

    def nearest_surface_point(self):
        """Point of the ellipsoid closest to the shadow axis (for non-central)."""
        f = self.fundamental()
        Qp = f['Qp']
        P = WGS84_A * Qp / norm(Qp)
        return P / TSCALE

    def outline(self, theta, cone='umbra'):
        """Intersection of the cone with the ellipsoid for each time (columns)
        and each generator azimuth theta.  Returns P (3, N, K) and hit (N, K)."""
        if cone == 'umbra':
            V = self.M + self.u * (self.Rmi / self.sf2)
            cf, sf = self.cf2, self.sf2
        else:
            V = self.M - self.u * (self.Rme / self.sf1)
            cf, sf = self.cf1, self.sf1
        ct, st = np.cos(theta)[None, :], np.sin(theta)[None, :]
        d = (cf[None, :, None] * self.u[:, :, None]
             + sf[None, :, None] * (self.e1[:, :, None] * ct[None] + self.e2[:, :, None] * st[None]))
        N, K = self.u.shape[1], theta.size
        Vb = np.broadcast_to(V[:, :, None], (3, N, K)).reshape(3, -1)
        P, _, hit = line_ellipsoid_intersection(Vb, d.reshape(3, -1))
        return P.reshape(3, N, K), hit.reshape(N, K)


def margin_at(shadow, P, cone):
    s, r, Rp, Ru = shadow.coords(P)
    if cone == 'umbra':
        return r - np.abs(Ru)
    return r - Rp


def local_magnitude(S, M, P, params):
    """Magnitude/ratio/sun altitude at ITRS points P (3,N) for S, M (3,N)."""
    vS, vM = S - P, M - P
    dS, dM = norm(vS), norm(vM)
    cross = np.cross(vS, vM, axis=0)
    sep = np.arctan2(norm(cross), np.sum(vS * vM, axis=0))
    rs = np.arcsin(params.sun_radius_km / dS)
    rm = np.arcsin(params.moon_radius_ext_km / dM)
    rmi = np.arcsin(params.moon_radius_int_km / dM)
    lat, lon, _ = itrs_to_geodetic(P)
    la, lo = np.radians(lat), np.radians(lon)
    n = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    alt = np.degrees(np.arcsin(np.sum(n * vS, axis=0) / dS))
    mag = np.maximum((rs + rm - sep) / (2 * rs), 0)
    return dict(magnitude=mag, ratio=rmi / rs, sep=sep, rs=rs, rm=rm, sun_alt=alt,
                lat=lat, lon=lon)


def envelope(ctx, jd, params, cone='umbra', n_theta=360, delta_s=1.0):
    """Path-limit points (envelope of the moving shadow outline).

    For each time, the outline points where the shadow boundary moves
    tangentially (d/dt of the margin = 0) are the northern and southern
    limits.  Returns list per time of dict(side -> ITRS point).
    """
    jd = np.atleast_1d(jd)
    dj = delta_s / 86400.0
    S, M, _ = sun_moon_itrs(ctx, np.r_[jd, jd - dj, jd + dj])
    N = jd.size
    sh0 = Shadow(S[:, :N], M[:, :N], params)
    shm = Shadow(S[:, N:2 * N], M[:, N:2 * N], params)
    shp = Shadow(S[:, 2 * N:], M[:, 2 * N:], params)
    theta = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    P, hit = sh0.outline(theta, cone)
    K = theta.size
    Pf = P.reshape(3, -1)
    rep = lambda sh: _repeat_shadow(sh, K)
    fdot = margin_at(rep(shp), Pf, cone) - margin_at(rep(shm), Pf, cone)
    fdot = fdot.reshape(N, K)
    # side of each outline point relative to the axis (north/south)
    w = P - sh0.M[:, :, None]
    s = np.sum(w * sh0.u[:, :, None], axis=0)
    perp = w - s[None] * sh0.u[:, :, None]
    zp = ZHAT - np.sum(ZHAT * sh0.u, axis=0)[None, :] * sh0.u
    side = np.sum(perp * zp[:, :, None], axis=0)
    out = []
    for i in range(N):
        pts = {}
        f, h = fdot[i], hit[i]
        j = np.arange(K)
        jn = (j + 1) % K
        ok = h & h[jn] & ((f < 0) != (f[jn] < 0))
        for a in np.nonzero(ok)[0]:
            b = jn[a]
            w0 = f[a] / (f[a] - f[b])
            pt = P[:, i, a] * (1 - w0) + P[:, i, b] * w0
            sd = 'N' if side[i, a] * (1 - w0) + side[i, b] * w0 > 0 else 'S'
            pts[sd] = pt
        out.append(dict(points=pts, outline=P[:, i, :], hit=h))
    return out, sh0


def _repeat_shadow(sh, K):
    """Shadow object with each time column repeated K times (row-major)."""
    new = Shadow.__new__(Shadow)
    for name in ('u', 'M', 'e1', 'e2'):
        setattr(new, name, np.repeat(getattr(sh, name), K, axis=1))
    for name in ('dSM', 'sf1', 'cf1', 'tf1', 'sf2', 'cf2', 'tf2'):
        setattr(new, name, np.repeat(getattr(sh, name), K))
    new.Rme, new.Rmi = sh.Rme, sh.Rmi
    return new

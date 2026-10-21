"""Numerical orbit propagation of a state vector (CCSDS OPM / OCM states, orbit determination).

The equations of motion are integrated by the modified Chebyshev-Picard iteration (Bai & Junkins
2011, J. Astronaut. Sci. 58, 583): over a segment of the orbit the trajectory is a Chebyshev
polynomial through Chebyshev-Gauss-Lobatto nodes, improved by Picard iteration from a two-body
first guess, so that each iteration evaluates the forces at all the nodes at once (vectorised).  The
converged polynomials are kept as the ephemeris (like the Chebyshev records of a SPICE kernel), so
positions and velocities at any time come from them.

Forces about the Earth: the EGM96 gravity field to degree and order 8 (NGA coefficient file,
fully normalised; in the Earth-fixed frame of date without polar motion), the Sun and the Moon as
point masses (DE440 from the context), and, when the spacecraft parameters are given, atmospheric
drag (exponential atmosphere of CIRA-72 mean conditions, Vallado table 8-4, rotating with the
Earth) and solar radiation pressure (cannonball with the conical shadow of the Earth).  About another
centre (the Sun, the Moon, the solar system barycentre, ...): that body and the Sun, the planets, the
Earth and the Moon as point masses.
"""
import math

import numpy as np

from .constants import AU_KM, DAY_S, EARTH_OMEGA, GM_EARTH, GM_MOON, WGS84_A, WGS84_F
from .i18n import tr
from .observers import ObserverError

GM_SUN = 1.32712440018e11            # km^3 / s^2
# GM (km^3/s^2) of the bodies of the solar system (DE440; planets with their satellites)
GM_BODIES = {
    'sun': GM_SUN, 'mercury': 22031.868551, 'venus': 324858.592, 'earth': GM_EARTH, 'moon': GM_MOON,
    'mars': 42828.375816, 'jupiter': 126712764.1, 'saturn': 37940584.8418, 'uranus': 5794556.4,
    'neptune': 6836527.10058, 'pluto': 975.5,
}
# The ephemeris targets of DE440s (planet systems by their barycentres)
EPH_TARGETS = {'sun': 'sun', 'mercury': 'mercury', 'venus': 'venus', 'earth': 'earth', 'moon': 'moon',
               'mars': 'mars barycenter', 'jupiter': 'jupiter barycenter', 'saturn': 'saturn barycenter',
               'uranus': 'uranus barycenter', 'neptune': 'neptune barycenter', 'pluto': 'pluto barycenter',
               'ssb': 'solar system barycenter', 'emb': 'earth barycenter'}

EGM96_R = 6378.1363                   # reference radius of EGM96 (km)
# EGM96 fully normalised coefficients (n, m, C, S) to degree 8 (NGA, file EGM96 of
# EGM96_Spherical_Harmonics.zip; tide-free)
EGM96 = (
    (2, 0, -0.484165371736e-03, 0.000000000000e+00),
    (2, 1, -0.186987635955e-09, 0.119528012031e-08),
    (2, 2, 0.243914352398e-05, -0.140016683654e-05),
    (3, 0, 0.957254173792e-06, 0.000000000000e+00),
    (3, 1, 0.202998882184e-05, 0.248513158716e-06),
    (3, 2, 0.904627768605e-06, -0.619025944205e-06),
    (3, 3, 0.721072657057e-06, 0.141435626958e-05),
    (4, 0, 0.539873863789e-06, 0.000000000000e+00),
    (4, 1, -0.536321616971e-06, -0.473440265853e-06),
    (4, 2, 0.350694105785e-06, 0.662671572540e-06),
    (4, 3, 0.990771803829e-06, -0.200928369177e-06),
    (4, 4, -0.188560802735e-06, 0.308853169333e-06),
    (5, 0, 0.685323475630e-07, 0.000000000000e+00),
    (5, 1, -0.621012128528e-07, -0.944226127525e-07),
    (5, 2, 0.652438297612e-06, -0.323349612668e-06),
    (5, 3, -0.451955406071e-06, -0.214847190624e-06),
    (5, 4, -0.295301647654e-06, 0.496658876769e-07),
    (5, 5, 0.174971983203e-06, -0.669384278219e-06),
    (6, 0, -0.149957994714e-06, 0.000000000000e+00),
    (6, 1, -0.760879384947e-07, 0.262890545501e-07),
    (6, 2, 0.481732442832e-07, -0.373728201347e-06),
    (6, 3, 0.571730990516e-07, 0.902694517163e-08),
    (6, 4, -0.862142660109e-07, -0.471408154267e-06),
    (6, 5, -0.267133325490e-06, -0.536488432483e-06),
    (6, 6, 0.967616121092e-08, -0.237192006935e-06),
    (7, 0, 0.909789371450e-07, 0.000000000000e+00),
    (7, 1, 0.279872910488e-06, 0.954336911867e-07),
    (7, 2, 0.329743816488e-06, 0.930667596042e-07),
    (7, 3, 0.250398657706e-06, -0.217198608738e-06),
    (7, 4, -0.275114355257e-06, -0.123800392323e-06),
    (7, 5, 0.193765507243e-08, 0.177377719872e-07),
    (7, 6, -0.358856860645e-06, 0.151789817739e-06),
    (7, 7, 0.109185148045e-08, 0.244415707993e-07),
    (8, 0, 0.496711667324e-07, 0.000000000000e+00),
    (8, 1, 0.233422047893e-07, 0.590060493411e-07),
    (8, 2, 0.802978722615e-07, 0.654175425859e-07),
    (8, 3, -0.191877757009e-07, -0.863454445021e-07),
    (8, 4, -0.244600105471e-06, 0.700233016934e-07),
    (8, 5, -0.255352403037e-07, 0.891462164788e-07),
    (8, 6, -0.657361610961e-07, 0.309238461807e-06),
    (8, 7, 0.672811580072e-07, 0.747440473633e-07),
    (8, 8, -0.124092493016e-06, 0.120533165603e-06),
)

# Exponential atmosphere (Vallado, Fundamentals of Astrodynamics, table 8-4: CIRA-72): base
# altitude (km), density there (kg/m^3), scale height (km)
ATMOSPHERE = np.array([
    (0, 1.225, 7.249), (25, 3.899e-2, 6.349), (30, 1.774e-2, 6.682), (40, 3.972e-3, 7.554),
    (50, 1.057e-3, 8.382), (60, 3.206e-4, 7.714), (70, 8.770e-5, 6.549), (80, 1.905e-5, 5.799),
    (90, 3.396e-6, 5.382), (100, 5.297e-7, 5.877), (110, 9.661e-8, 7.263), (120, 2.438e-8, 9.473),
    (130, 8.484e-9, 12.636), (140, 3.845e-9, 16.149), (150, 2.070e-9, 22.523), (180, 5.464e-10, 29.740),
    (200, 2.789e-10, 37.105), (250, 7.248e-11, 45.546), (300, 2.418e-11, 53.628), (350, 9.518e-12, 53.298),
    (400, 3.725e-12, 58.515), (450, 1.585e-12, 60.828), (500, 6.967e-13, 63.822), (600, 1.454e-13, 71.835),
    (700, 3.614e-14, 88.667), (800, 1.170e-14, 124.64), (900, 5.245e-15, 181.05), (1000, 3.019e-15, 268.00),
])
SOLAR_PRESSURE_1AU = 4.56e-6          # N / m^2
SUN_RADIUS_KM = 696_000.0
DECAY_ALT_KM = 100.0                  # below this the propagation of an Earth satellite stops (re-entry)


class PropagationError(ObserverError):
    pass


# ---------------------------------------------------------------------------
# Chebyshev-Picard machinery
# ---------------------------------------------------------------------------
class _Cheb:
    """Chebyshev-Gauss-Lobatto nodes tau_j = -cos(j pi / N) (ascending, -1 to 1), the matrix C that
    turns values at the nodes into the coefficients of the interpolating polynomial, and M, the
    integral from -1 to each node of that polynomial (Picard iteration: x = x0 + M (dx/dtau))."""

    def __init__(self, n):
        self.n = n
        theta = np.pi - np.arange(n + 1) * np.pi / n
        self.tau = np.cos(theta)
        T = np.cos(np.outer(theta, np.arange(n + 1)))
        w = np.ones(n + 1)
        w[0] = w[-1] = 0.5
        C = (2.0 / n) * (T * w[:, None]).T
        C[0] *= 0.5
        C[n] *= 0.5
        self.C = C
        B = np.zeros((n + 2, n + 1))         # coefficients of the integral (degree n + 1)
        B[1, 0] = 1.0
        B[2, 1] = 0.25
        for k in range(2, n + 1):
            B[k + 1, k] += 1.0 / (2 * (k + 1))
            B[k - 1, k] -= 1.0 / (2 * (k - 1))
        sgn = (-1.0) ** np.arange(n + 2)       # T_k(-1): the integral vanishes at tau = -1
        B[0] = -(sgn[1:, None] * B[1:]).sum(axis=0)
        self.M = np.cos(np.outer(theta, np.arange(n + 2))) @ B @ C


def _stumpff(z):
    c = np.empty_like(z)
    s = np.empty_like(z)
    pos, neg = z > 1e-8, z < -1e-8
    small = ~(pos | neg)
    sz = np.sqrt(z[pos])
    c[pos] = (1 - np.cos(sz)) / z[pos]
    s[pos] = (sz - np.sin(sz)) / sz ** 3
    sz = np.sqrt(-z[neg])
    c[neg] = (np.cosh(sz) - 1) / -z[neg]
    s[neg] = (np.sinh(sz) - sz) / sz ** 3
    zs = z[small]
    c[small] = 0.5 - zs / 24 + zs * zs / 720
    s[small] = 1 / 6 - zs / 120 + zs * zs / 5040
    return c, s


def kepler(x0, dt, gm):
    """Two-body states (n, 6) at times dt (s, array) from the state x0 (6,) (universal variables,
    any conic)."""
    r0, v0 = np.asarray(x0[:3], float), np.asarray(x0[3:], float)
    dt = np.asarray(dt, float)
    r0n = math.sqrt(r0 @ r0)
    sq = math.sqrt(gm)
    vr0 = (r0 @ v0) / r0n
    alpha = 2.0 / r0n - (v0 @ v0) / gm
    chi = sq * dt * alpha if alpha > 1e-9 else sq * dt / r0n
    for _ in range(50):
        z = alpha * chi * chi
        c, s = _stumpff(z)
        f = r0n * vr0 / sq * chi * chi * c + (1 - alpha * r0n) * chi ** 3 * s + r0n * chi - sq * dt
        df = r0n * vr0 / sq * chi * (1 - z * s) + (1 - alpha * r0n) * chi * chi * c + r0n
        d = f / df
        chi = chi - d
        if np.max(np.abs(d)) < 1e-12 * max(1.0, float(np.max(np.abs(chi)))):
            break
    z = alpha * chi * chi
    c, s = _stumpff(z)
    f = 1 - chi * chi / r0n * c
    g = dt - chi ** 3 * s / sq
    r = f[:, None] * r0 + g[:, None] * v0
    rn = np.linalg.norm(r, axis=1)
    fd = sq / (rn * r0n) * chi * (z * s - 1)
    gd = 1 - chi * chi / rn * c
    v = fd[:, None] * r0 + gd[:, None] * v0
    return np.concatenate([r, v], axis=1)


def elements(r, v, gm=GM_EARTH):
    """Osculating elements of a state: a (km, negative for a hyperbola), e, i, RAAN, argument of
    pericentre, true and mean anomaly (deg) in the frame of the vectors."""
    r, v = np.asarray(r, float), np.asarray(v, float)
    rn, vn2 = np.linalg.norm(r), v @ v
    h = np.cross(r, v)
    hn = np.linalg.norm(h)
    en = 0.5 * vn2 - gm / rn
    a = -gm / (2 * en) if abs(en) > 1e-14 else math.inf
    evec = np.cross(v, h) / gm - r / rn
    e = float(np.linalg.norm(evec))
    i = math.degrees(math.acos(max(-1.0, min(1.0, h[2] / hn))))
    nvec = np.array([-h[1], h[0], 0.0])
    nn = np.linalg.norm(nvec)
    raan = math.degrees(math.atan2(nvec[1], nvec[0])) % 360 if nn > 1e-12 else 0.0
    if e > 1e-10:
        ref = nvec / nn if nn > 1e-12 else np.array([1.0, 0, 0])
        argp = math.degrees(math.atan2(np.cross(ref, evec) @ h / hn, ref @ evec)) % 360
        nu = math.degrees(math.atan2(np.cross(evec, r) @ h / hn, evec @ r)) % 360
    else:
        argp = 0.0
        ref = nvec / nn if nn > 1e-12 else np.array([1.0, 0, 0])
        nu = math.degrees(math.atan2(np.cross(ref, r) @ h / hn, ref @ r)) % 360
    nr = math.radians(nu)
    if e < 1:
        E = 2 * math.atan2(math.sqrt(1 - e) * math.sin(nr / 2), math.sqrt(1 + e) * math.cos(nr / 2))
        M = math.degrees(E - e * math.sin(E)) % 360
    else:
        H = 2 * math.atanh(math.sqrt((e - 1) / (e + 1)) * math.tan(nr / 2)) if abs(nr) < math.pi else 0.0
        M = math.degrees(e * math.sinh(H) - H)
    return {'a_km': a, 'e': e, 'i_deg': i, 'raan_deg': raan, 'argp_deg': argp, 'nu_deg': nu, 'm_deg': M,
            'r_min_km': a * (1 - e) if e < 1 else hn * hn / (gm * (1 + e)),
            'r_max_km': a * (1 + e) if e < 1 else math.inf,
            'period_min': 2 * math.pi * math.sqrt(a ** 3 / gm) / 60 if e < 1 else None}


def state_from_elements(a, e, i, raan, argp, anomaly, gm=GM_EARTH, mean=False):
    """Cartesian state (6,) of osculating elements (km, deg); ``anomaly`` true or (``mean``) mean."""
    i, raan, argp, an = (math.radians(x) for x in (i, raan, argp, anomaly))
    if mean:
        if e >= 1:
            raise PropagationError(tr('平均近点角の軌道要素は楕円軌道（離心率 1 未満）でだけ使えます'))
        E = an
        for _ in range(50):
            d = (E - e * math.sin(E) - an) / (1 - e * math.cos(E))
            E -= d
            if abs(d) < 1e-14:
                break
        nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
    else:
        nu = an
    p = a * (1 - e * e)
    if not p > 0:
        raise PropagationError(tr('軌道要素を解釈できません（半直弦 a(1−e²) が正になりません）'))
    rr = p / (1 + e * math.cos(nu))
    pos = np.array([rr * math.cos(nu), rr * math.sin(nu), 0.0])
    vel = math.sqrt(gm / p) * np.array([-math.sin(nu), e + math.cos(nu), 0.0])
    cO, sO, ci, si, cw, sw = math.cos(raan), math.sin(raan), math.cos(i), math.sin(i), math.cos(argp), math.sin(argp)
    R = np.array([[cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci, sO * si],
                  [sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci, -cO * si],
                  [sw * si, cw * si, ci]])
    return np.concatenate([R @ pos, R @ vel])


def local_frame(r, v, frame):
    """The 3x3 matrix whose columns are the axes of an orbit-relative frame (RTN / RSW / TNW /
    QSW / LVLH ...) of the state r, v in the inertial frame."""
    rn = r / np.linalg.norm(r)
    h = np.cross(r, v)
    hn = h / np.linalg.norm(h)
    frame = frame.upper().replace('_INERTIAL', '').replace('_ROTATING', '')
    if frame in ('RTN', 'RSW', 'QSW', 'RIC'):
        return np.column_stack([rn, np.cross(hn, rn), hn])
    if frame in ('TNW',):
        t = v / np.linalg.norm(v)
        return np.column_stack([t, np.cross(hn, t), hn])
    if frame in ('LVLH',):                 # z toward the centre, y opposite to the angular momentum
        z = -rn
        y = -hn
        return np.column_stack([np.cross(y, z), y, z])
    return None


# ---------------------------------------------------------------------------
# Force models
# ---------------------------------------------------------------------------
def _unnormalised(degree):
    out = []
    for n, m, c, s in EGM96:
        if n > degree:
            continue
        f = math.sqrt((2.0 - (m == 0)) * (2 * n + 1) * math.factorial(n - m) / math.factorial(n + m))
        out.append((n, m, c * f, s * f))
    return out


_FIELDS = {d: _unnormalised(d) for d in (2, 4, 8)}


def harmonics_accel(rf, degree=8):
    """Acceleration (km/s^2) of the EGM96 terms of degree 2..``degree`` at Earth-fixed positions rf
    (n, 3), by the recursions of Cunningham (Montenbruck & Gill, Satellite Orbits, 3.2.4-3.2.5)."""
    terms = _FIELDS[degree]
    R = EGM96_R
    x, y, z = rf[:, 0], rf[:, 1], rf[:, 2]
    r2 = x * x + y * y + z * z
    rho = R * R / r2
    x0, y0, z0 = R * x / r2, R * y / r2, R * z / r2
    N = degree + 1
    V = [[None] * (N + 1) for _ in range(N + 1)]
    W = [[None] * (N + 1) for _ in range(N + 1)]
    zero = np.zeros_like(x)
    V[0][0] = R / np.sqrt(r2)
    W[0][0] = zero
    V[1][0] = z0 * V[0][0]
    W[1][0] = zero
    for n in range(2, N + 1):
        V[n][0] = ((2 * n - 1) * z0 * V[n - 1][0] - (n - 1) * rho * V[n - 2][0]) / n
        W[n][0] = zero
    for m in range(1, N + 1):
        V[m][m] = (2 * m - 1) * (x0 * V[m - 1][m - 1] - y0 * W[m - 1][m - 1])
        W[m][m] = (2 * m - 1) * (x0 * W[m - 1][m - 1] + y0 * V[m - 1][m - 1])
        if m < N:
            V[m + 1][m] = (2 * m + 1) * z0 * V[m][m]
            W[m + 1][m] = (2 * m + 1) * z0 * W[m][m]
        for n in range(m + 2, N + 1):
            V[n][m] = ((2 * n - 1) * z0 * V[n - 1][m] - (n + m - 1) * rho * V[n - 2][m]) / (n - m)
            W[n][m] = ((2 * n - 1) * z0 * W[n - 1][m] - (n + m - 1) * rho * W[n - 2][m]) / (n - m)
    ax = np.zeros_like(x)
    ay = np.zeros_like(x)
    az = np.zeros_like(x)
    for n, m, C, S in terms:
        if m == 0:
            ax -= C * V[n + 1][1]
            ay -= C * W[n + 1][1]
            az -= (n + 1) * C * V[n + 1][0]
        else:
            fac = 0.5 * (n - m + 1) * (n - m + 2)
            ax += 0.5 * (-C * V[n + 1][m + 1] - S * W[n + 1][m + 1]) + fac * (C * V[n + 1][m - 1] + S * W[n + 1][m - 1])
            ay += 0.5 * (-C * W[n + 1][m + 1] + S * V[n + 1][m + 1]) + fac * (-C * W[n + 1][m - 1] + S * V[n + 1][m - 1])
            az += (n - m + 1) * (-C * V[n + 1][m] - S * W[n + 1][m])
    k = GM_EARTH / (R * R)
    return np.stack([ax * k, ay * k, az * k], axis=1)


def atmosphere_density(h_km):
    """Density (kg/m^3) of the exponential atmosphere at altitudes h (km); 0 above 1500 km."""
    h = np.asarray(h_km, float)
    k = np.clip(np.searchsorted(ATMOSPHERE[:, 0], h, side='right') - 1, 0, len(ATMOSPHERE) - 1)
    base, rho0, H = ATMOSPHERE[k, 0], ATMOSPHERE[k, 1], ATMOSPHERE[k, 2]
    out = rho0 * np.exp(-(h - base) / H)
    return np.where(h > 1500.0, 0.0, out)


def shadow_factor(r, sun):
    """Fraction of the solar disk seen from r (n, 3) past the Earth (conical shadow; sun = Sun's
    position relative to the Earth's centre (n, 3))."""
    d_s = sun - r
    ds = np.linalg.norm(d_s, axis=1)
    rn = np.linalg.norm(r, axis=1)
    a = np.arcsin(np.minimum(SUN_RADIUS_KM / ds, 1.0))
    b = np.arcsin(np.minimum(WGS84_A / rn, 1.0))
    cosc = np.clip(np.sum(-r * d_s, axis=1) / (rn * ds), -1, 1)
    c = np.arccos(cosc)
    nu = np.ones_like(rn)
    full = c < b - a
    part = (c < a + b) & ~full
    nu[full] = 0.0
    if np.any(part):
        ap, bp, cp = a[part], b[part], c[part]
        x = (cp * cp + ap * ap - bp * bp) / (2 * cp)
        y = np.sqrt(np.maximum(ap * ap - x * x, 0.0))
        A = ap * ap * np.arccos(np.clip(x / ap, -1, 1)) + bp * bp * np.arccos(np.clip((cp - x) / bp, -1, 1)) - cp * y
        nu[part] = 1 - A / (np.pi * ap * ap)
    return nu


class _Tables:
    """Positions and velocities of bodies relative to the centre, and (about the Earth) the
    orientation of the Earth (the GCRS -> equator-of-date matrix and the sidereal angle), on a grid
    of ``step`` seconds from the anchor epoch (TT), built in chunks as the propagation advances."""

    CHUNK_S = 20 * DAY_S

    def __init__(self, ctx, jd0, center, bodies, step=3600.0, earth_orientation=False):
        self.ctx, self.jd0, self.center, self.bodies, self.step = ctx, jd0, center, list(bodies), step
        self.eo = earth_orientation
        self.lo = self.hi = None
        self.pos = {b: np.zeros((0, 3)) for b in self.bodies}
        self.vel = {b: np.zeros((0, 3)) for b in self.bodies}
        self.M = np.zeros((0, 3, 3))
        self.gast = np.zeros(0)

    def _compute(self, k0, k1):
        ctx = self.ctx
        s = np.arange(k0, k1 + 1) * self.step
        jd = ctx.ts.tt_jd(self.jd0[0], self.jd0[1] + s / DAY_S)
        jd_tt = jd.tt
        if np.any(jd_tt < ctx.jd_min) or np.any(jd_tt > ctx.jd_max):
            raise PropagationError(tr('軌道の伝搬が暦 {name} の範囲を超えました', name=ctx.ephemeris_name))
        eph = ctx.eph
        c = eph[EPH_TARGETS[self.center]]
        out = {}
        for b in self.bodies:
            st = (eph[EPH_TARGETS[b]] - c).at(jd)
            out[b] = (st.position.km.T, st.velocity.km_per_s.T)
        M, g = None, None
        if self.eo:
            M = np.moveaxis(jd.M, -1, 0)
            g = np.unwrap(jd.gast * (2 * math.pi / 24.0))
        return s, out, M, g

    def ensure(self, s_lo, s_hi):
        k0 = int(math.floor(s_lo / self.step)) - 2
        k1 = int(math.ceil(s_hi / self.step)) + 2
        if self.lo is not None and k0 >= self.lo and k1 <= self.hi:
            return
        if self.lo is None:
            k0 = min(k0, int(math.floor(-self.CHUNK_S / self.step)))
            k1 = max(k1, int(math.ceil(self.CHUNK_S / self.step)))
            s, out, M, g = self._compute(k0, k1)
            self.lo, self.hi = k0, k1
            for b in self.bodies:
                self.pos[b], self.vel[b] = out[b]
            if self.eo:
                self.M, self.gast = M, g
            return
        chunk = int(self.CHUNK_S / self.step)
        if k0 < self.lo:
            a = min(k0, self.lo - chunk)
            s, out, M, g = self._compute(a, self.lo - 1)
            for b in self.bodies:
                self.pos[b] = np.concatenate([out[b][0], self.pos[b]])
                self.vel[b] = np.concatenate([out[b][1], self.vel[b]])
            if self.eo:
                self.M = np.concatenate([M, self.M])
                g = g + 2 * math.pi * round((2 * self.gast[0] - self.gast[1] - g[-1]) / (2 * math.pi))   # continue the unwrapped angle
                self.gast = np.concatenate([g, self.gast])
            self.lo = a
        if k1 > self.hi:
            b1 = max(k1, self.hi + chunk)
            s, out, M, g = self._compute(self.hi + 1, b1)
            for b in self.bodies:
                self.pos[b] = np.concatenate([self.pos[b], out[b][0]])
                self.vel[b] = np.concatenate([self.vel[b], out[b][1]])
            if self.eo:
                self.M = np.concatenate([self.M, M])
                g = g + 2 * math.pi * round((2 * self.gast[-1] - self.gast[-2] - g[0]) / (2 * math.pi))
                self.gast = np.concatenate([self.gast, g])
            self.hi = b1

    def _index(self, s):
        u = s / self.step - self.lo
        k = np.clip(np.floor(u).astype(int), 0, self.hi - self.lo - 1)
        return k, u - k

    def body(self, b, s):
        """Cubic Hermite interpolation of the body's position at times s (n,)."""
        k, f = self._index(s)
        p, v = self.pos[b], self.vel[b]
        h = self.step
        f2, f3 = f * f, f * f * f
        h00 = 2 * f3 - 3 * f2 + 1
        h10 = f3 - 2 * f2 + f
        h01 = -2 * f3 + 3 * f2
        h11 = f3 - f2
        return (h00[:, None] * p[k] + h10[:, None] * h * v[k] + h01[:, None] * p[k + 1]
                + h11[:, None] * h * v[k + 1])

    def earth(self, s):
        """(GCRS -> equator of date matrices (n, 3, 3), Greenwich apparent sidereal angle (n,))."""
        k, f = self._index(s)
        M = self.M[k] + f[:, None, None] * (self.M[k + 1] - self.M[k])
        g = self.gast[k] + f * (self.gast[k + 1] - self.gast[k])
        return M, g


class Forces:
    """Accelerations (km/s^2) at times s (seconds from the anchor epoch, TT), positions r and
    velocities v (n, 3) relative to the centre in ICRS (GCRS) axes."""

    def __init__(self, ctx, jd0, center='earth', degree=8, drag=None, srp=None):
        self.ctx, self.jd0, self.center = ctx, jd0, center
        self.degree = degree
        self.drag = drag            # C_D A / m (m^2 / kg) or None
        self.srp = srp              # C_R A / m (m^2 / kg) or None
        self.gm = GM_BODIES['sun'] if center == 'ssb' else GM_BODIES.get(center, GM_EARTH)
        if center == 'emb':
            self.gm = GM_EARTH + GM_MOON
        if center == 'earth':
            bodies = ['sun', 'moon']
        else:
            bodies = [b for b in ('sun', 'mercury', 'venus', 'earth', 'moon', 'mars', 'jupiter', 'saturn',
                                  'uranus', 'neptune') if b != center]
        self.bodies = bodies
        need = bodies if center != 'ssb' else bodies
        self.tables = _Tables(ctx, jd0, center, need, earth_orientation=(center == 'earth'))

    def ensure(self, s_lo, s_hi):
        self.tables.ensure(s_lo, s_hi)

    def accel(self, s, r, v, degree=None):
        tb = self.tables
        rn = np.linalg.norm(r, axis=1)
        if self.center == 'ssb':
            a = np.zeros_like(r)
        else:
            a = -self.gm * r / rn[:, None] ** 3
        bodies = {}
        for b in self.bodies:
            sb = tb.body(b, s)
            bodies[b] = sb
            d = sb - r
            dn = np.linalg.norm(d, axis=1)
            if self.center == 'ssb':
                a += GM_BODIES[b] * d / dn[:, None] ** 3
            else:
                sn = np.linalg.norm(sb, axis=1)
                a += GM_BODIES[b] * (d / dn[:, None] ** 3 - sb / sn[:, None] ** 3)
        if self.center == 'earth':
            deg = self.degree if degree is None else degree
            M, g = tb.earth(s)
            if deg >= 2:
                cg, sg = np.cos(g), np.sin(g)
                rd = np.einsum('nij,nj->ni', M, r)                 # equator of date
                rf = np.stack([cg * rd[:, 0] + sg * rd[:, 1], -sg * rd[:, 0] + cg * rd[:, 1], rd[:, 2]], axis=1)
                af = harmonics_accel(rf, deg)
                ad = np.stack([cg * af[:, 0] - sg * af[:, 1], sg * af[:, 0] + cg * af[:, 1], af[:, 2]], axis=1)
                a += np.einsum('nji,nj->ni', M, ad)
            if self.drag:
                pole = M[:, 2, :]
                vrel = v - EARTH_OMEGA * np.cross(pole, r)
                sphi = np.sum(r * pole, axis=1) / rn
                h = rn - WGS84_A * (1 - WGS84_F * sphi * sphi)
                rho = atmosphere_density(h)
                vr = np.linalg.norm(vrel, axis=1)
                a -= (0.5e3 * self.drag * rho * vr)[:, None] * vrel
            if self.srp:
                sun = bodies['sun']
                d = r - sun
                dn = np.linalg.norm(d, axis=1)
                nu = shadow_factor(r, sun)
                p = SOLAR_PRESSURE_1AU * (AU_KM / dn) ** 2 * self.srp * 1e-3 * nu
                a += (p / dn)[:, None] * d
        elif self.srp:
            sun = r * 0 if self.center == 'sun' else bodies['sun']
            d = r - sun
            dn = np.linalg.norm(d, axis=1)
            p = SOLAR_PRESSURE_1AU * (AU_KM / dn) ** 2 * self.srp * 1e-3
            a += (p / dn)[:, None] * d
        return a


# ---------------------------------------------------------------------------
# The trajectory: Chebyshev segments forward and backward from the anchor
# ---------------------------------------------------------------------------
class Trajectory:
    """The propagated orbit from the state ``x0`` (km, km/s, ICRS axes, relative to the centre of
    ``forces``) at the anchor epoch (s = 0).  ``maneuvers``: [(s, function(state) -> delta-v (3,))]
    applied impulsively when the propagation passes them.  ``limits``: (s_min, s_max), the longest
    propagation allowed back and forth.  Earth satellites stop at re-entry (``decay_s``)."""

    NODES = 24
    MAX_ITER = 40
    FRAC = 0.2                  # segment length as a fraction of the circular period at the radius
    SAME_S = 1e-6               # maneuvers this close (s) are at the same time

    def __init__(self, forces, x0, maneuvers=(), limits=(-math.inf, math.inf)):
        self.forces = forces
        self.gm = forces.gm
        self.cheb = _Cheb(self.NODES)
        self.x0 = np.asarray(x0, float)
        self.maneuvers = sorted(maneuvers, key=lambda m: m[0])
        self.limits = limits
        self.end = {1: [0.0, self.x0.copy()], -1: [0.0, self.x0.copy()]}
        for m in self.maneuvers:             # an impulse at the anchor epoch acts after it
            if abs(m[0]) <= self.SAME_S:
                self.end[1][1] = self.end[1][1] + np.concatenate([np.zeros(3), m[1](self.end[1][1])])
        self.segs = {1: [], -1: []}          # (t0, h, coefR (N+1, 3), coefV (N+1, 3))
        self.decay_s = {1: None, -1: None}
        self._index = None
        self.n_segments = 0

    # -- propagation ------------------------------------------------------------
    def _segment(self, t0, x0, h):
        cb = self.cheb
        w2 = 0.5 * h
        ts = t0 + w2 * (cb.tau + 1)
        X = kepler(x0, ts - t0, self.gm)
        R, V = X[:, :3], X[:, 3:]
        rmin = float(np.min(np.linalg.norm(R, axis=1)))
        deg = self.forces.degree
        if self.forces.center == 'earth':        # the high-degree terms fade with distance
            deg = 8 if rmin < 3 * WGS84_A else 4 if rmin < 12 * WGS84_A else 2
            deg = min(deg, self.forces.degree)
        self.forces.ensure(min(ts[0], ts[-1]), max(ts[0], ts[-1]))
        scale = max(rmin, 1.0)
        prev = math.inf
        for it in range(self.MAX_ITER):
            A = self.forces.accel(ts, R, V, deg)
            Vn = x0[3:] + cb.M @ (w2 * A)
            Rn = x0[:3] + cb.M @ (w2 * Vn)
            d = max(float(np.max(np.abs(Rn - R))), float(np.max(np.abs(Vn - V))) * abs(w2))
            R, V = Rn, Vn
            if d < 1e-13 * scale + 1e-10:
                return ts, R, V
            if it > 8 and d > 0.8 * prev:
                break
            prev = d
        return None

    def _step(self, direction, target):
        t, x = self.end[direction]
        rn = float(np.linalg.norm(x[:3]))
        h = self.FRAC * 2 * math.pi * math.sqrt(rn ** 3 / self.gm)
        # shorter where the orbit comes closer (e.g. toward the perigee of an eccentric orbit)
        guess = kepler(x, np.linspace(0, direction * h, 9), self.gm)
        rmin = float(np.min(np.linalg.norm(guess[:, :3], axis=1)))
        h = min(h, self.FRAC * 2 * math.pi * math.sqrt(rmin ** 3 / self.gm), 2 * DAY_S)
        nxt = [m for m in self.maneuvers if (m[0] - t) * direction > self.SAME_S]
        stop, burn = target, False
        if nxt:
            mt = nxt[0][0] if direction > 0 else nxt[-1][0]
            if (mt - stop) * direction <= 0:
                stop, burn = mt, True
        h = min(h, abs(stop - t))
        burn = burn and h == abs(stop - t)
        for _ in range(14):
            res = self._segment(t, x, direction * h)
            if res is not None:
                break
            h *= 0.5
            burn = False
        else:
            if self.forces.center == 'earth' and rn < WGS84_A + 2 * DECAY_ALT_KM:
                self.decay_s[direction] = t          # deep in the atmosphere: taken as the re-entry
                return
            raise PropagationError(tr('軌道の数値積分が収束しませんでした（{time} 付近）', time=f'{t / DAY_S:+.3f} d'))
        ts, R, V = res
        cb = self.cheb
        rr = np.linalg.norm(R, axis=1)
        if self.forces.center == 'earth' and np.any(rr < WGS84_A + DECAY_ALT_KM):
            k = int(np.argmax(rr < WGS84_A + DECAY_ALT_KM))
            self.decay_s[direction] = float(ts[max(k - 1, 0)])
        self.segs[direction].append((t, direction * h, cb.C @ R, cb.C @ V))
        self.n_segments += 1
        self._index = None
        t_new = stop if burn else t + direction * h
        x_new = np.concatenate([R[-1], V[-1]])
        for m in self.maneuvers if burn else ():     # the impulses at the end of the segment
            if abs(m[0] - t_new) <= self.SAME_S:
                dv = m[1](x_new)
                x_new[3:] += dv if direction > 0 else -dv
        self.end[direction] = [t_new, x_new]

    def extend(self, s_lo, s_hi):
        """Propagate so that [s_lo, s_hi] is covered (within the limits and before a re-entry)."""
        for direction, target in ((1, s_hi), (-1, s_lo)):
            target = min(target, self.limits[1]) if direction > 0 else max(target, self.limits[0])
            while (target - self.end[direction][0]) * direction > 1e-6:
                if self.decay_s[direction] is not None:
                    break
                self._step(direction, target)

    def span(self):
        lo = self.end[-1][0] if self.decay_s[-1] is None else self.decay_s[-1]
        hi = self.end[1][0] if self.decay_s[1] is None else self.decay_s[1]
        return lo, hi

    def _build_index(self):
        segs = list(reversed(self.segs[-1])) + self.segs[1]
        lo = np.array([min(t0, t0 + h) for t0, h, _, _ in segs])
        self._index = (segs, lo, np.array([t0 for t0, _, _, _ in segs]), np.array([h for _, h, _, _ in segs]),
                       np.array([c for _, _, c, _ in segs]), np.array([c for _, _, _, c in segs]))

    def state(self, s):
        """Positions and velocities (n, 3) at times s (n,), which must lie in span()."""
        s = np.atleast_1d(np.asarray(s, float))
        lo, hi = self.span()
        if s.size and (s.min() < lo - 1e-6 or s.max() > hi + 1e-6):
            raise PropagationError(tr('軌道を伝搬した範囲の外の時刻が要求されました'))
        if self._index is None:
            self._build_index()
        segs, starts, t0, h, cr, cv = self._index
        if not segs:
            return np.tile(self.x0[:3], (s.size, 1)), np.tile(self.x0[3:], (s.size, 1))
        k = np.clip(np.searchsorted(starts, s, side='right') - 1, 0, len(segs) - 1)
        tau = np.clip(2 * (s - t0[k]) / h[k] - 1, -1, 1)
        T = np.cos(np.outer(np.arccos(tau), np.arange(self.cheb.n + 1)))
        return np.einsum('nk,nkc->nc', T, cr[k]), np.einsum('nk,nkc->nc', T, cv[k])

"""Observer models.

Every observer exposes a Skyfield ``VectorFunction`` (``vf``) whose centre is
the geocentre (399), so ``ctx.earth + observer.vf`` is a barycentric vector
from which ``observe()`` performs full light-time iteration.

Supported observers:
  * GroundObserver   - a point on/above the WGS84 ellipsoid (horizon applies)
  * GeocenterObserver - Earth's centre (no visibility constraint)
  * TLEObserver      - an Earth satellite propagated with SGP4
  * KeplerObserver   - two-body orbit with J2 secular perturbations
  * FixedITRSObserver - Earth-fixed point in space (e.g. geostationary slot)
  * HorizonsObserver - any spacecraft with a JPL Horizons ephemeris
  * SSCWebObserver   - historical orbit of a satellite from NASA SSCWeb
"""
import http.client
import json
import math
import time
import urllib.error
import urllib.parse

import numpy as np
from skyfield.api import EarthSatellite, wgs84
from skyfield.vectorlib import VectorFunction

from .constants import (AU_KM, DAY_S, EARTH_OMEGA, GM_EARTH, GM_MOON, J2_EARTH, MOON_RADIUS_MEAN,
                        MOON_SOI_KM, WGS84_A, WGS84_B)
from .context import CACHE_DIR
from .geometry import norm
from .i18n import span, tr
from .net import urlopen as _urlopen

AU_PER_DAY_PER_KM_S = DAY_S / AU_KM


class ObserverError(ValueError):
    pass


class NetworkError(ObserverError):
    """An online service (CelesTrak, JPL Horizons, NASA SSCWeb) could not be reached."""


# no connection, timeouts, cut or broken answers (URLError and its HTTPError are OSErrors)
_NET_ERRORS = (OSError, http.client.HTTPException)


def _get_json(service, url, timeout, headers=None):
    try:
        with _urlopen(url, timeout, headers) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except _NET_ERRORS + (ValueError,) as exc:      # ValueError: an answer that is not JSON
        raise NetworkError(tr('{service} に接続できませんでした: {exc}', service=service, exc=exc))


class _ZeroVector(VectorFunction):
    center = 399
    target = 'geocenter'
    ephemeris = None

    def _at(self, t):
        z = np.zeros((3,) + np.shape(t.tt))
        return z, z, None, None


class Observer:
    kind = 'space'          # 'ground' | 'geocenter' | 'space'
    name = ''
    r_min_km = WGS84_A
    r_max_km = WGS84_A
    v_max_km_s = 0.5
    needs_network = False
    notes = ()              # warnings about the input itself (given with the results)

    def prepare(self, ctx, jd_windows):
        """Called before a scan with the TT JD windows that will be evaluated."""

    def limit_period(self, ctx, jd_a, jd_b, margin):
        """The part of the period [jd_a, jd_b] (TT JDs) that the orbit data cover (the search looks
        ``margin`` days beyond it) -> (jd_a, jd_b, warnings when it was shortened)."""
        return jd_a, jd_b, []

    def clip_windows(self, windows):
        """The windows of a search without the times the orbit data do not cover."""
        return windows

    def interval_at(self, jd):
        """The span (TT JDs) of the orbit data around ``jd``, or None if it is unbounded."""
        return None

    def char_time_s(self):
        """Time scale on which the observer's parallax changes appreciably."""
        return max(self.r_min_km, 1.0) / max(self.v_max_km_s, 1e-6)

    def warnings_for(self, ctx, jd):
        return []

    def describe(self):
        return {'kind': self.kind, 'name': self.name}


class GeocenterObserver(Observer):
    kind = 'geocenter'
    r_min_km = 0.0
    r_max_km = 0.0
    v_max_km_s = 0.0

    def __init__(self):
        self.vf = _ZeroVector()

    @property
    def name(self):
        return tr('地心')

    def char_time_s(self):
        return 1e9


class GroundObserver(Observer):
    kind = 'ground'

    def __init__(self, lat, lon, elevation_m=0.0, name=''):
        if not (-90 <= lat <= 90):
            raise ObserverError(tr('緯度は -90〜90 度で指定してください'))
        if not (-180 <= lon <= 360):
            raise ObserverError(tr('経度は -180〜180 度で指定してください'))
        if not -12_000 <= elevation_m <= 100_000:      # also NaN
            raise ObserverError(tr('標高は -12,000〜100,000 m で指定してください'))
        self.lat = float(lat)
        self.lon = float(((lon + 180.0) % 360.0) - 180.0)
        self.elevation_m = float(elevation_m)
        self.name = name or f'{abs(self.lat):.4f}°{"N" if self.lat >= 0 else "S"} {abs(self.lon):.4f}°{"E" if self.lon >= 0 else "W"}'
        self.vf = wgs84.latlon(self.lat, self.lon, elevation_m=self.elevation_m)
        r = WGS84_A + self.elevation_m / 1000.0
        self.r_min_km = WGS84_B + self.elevation_m / 1000.0
        self.r_max_km = r
        self.v_max_km_s = EARTH_OMEGA * r * math.cos(math.radians(self.lat)) + 1e-3

    def char_time_s(self):
        # Diurnal parallax: one radian of rotation takes ~3.8 h.
        return 1.0 / EARTH_OMEGA

    def describe(self):
        return {'kind': 'ground', 'name': self.name, 'lat': self.lat, 'lon': self.lon,
                'elevation_m': self.elevation_m}


class FixedITRSObserver(Observer):
    """Earth-fixed point far above the surface, e.g. an ideal geostationary satellite."""

    def __init__(self, lat, lon, height_km, name=''):
        if not (-90 <= lat <= 90):
            raise ObserverError(tr('緯度は -90〜90 度で指定してください'))
        if not (-180 <= lon <= 360):
            raise ObserverError(tr('経度は -180〜180 度で指定してください'))
        if not 0 <= height_km < 1e7:
            raise ObserverError(tr('入力値を解釈できません: {exc}', exc=f'height_km={height_km}'))
        self.lat, self.lon, self.height_km = float(lat), float(lon), float(height_km)
        self.name = name or tr('地球固定点 {lon:.2f}°', lon=lon)
        self.vf = wgs84.latlon(self.lat, self.lon, elevation_m=self.height_km * 1000.0)
        r = WGS84_A + self.height_km
        self.r_min_km = self.r_max_km = r
        self.v_max_km_s = EARTH_OMEGA * r + 1e-3

    def char_time_s(self):
        return 1.0 / EARTH_OMEGA

    def describe(self):
        return {'kind': 'space', 'model': 'fixed', 'name': self.name, 'lat': self.lat,
                'lon': self.lon, 'height_km': self.height_km}


class SGP4Observer(Observer):
    """An Earth satellite propagated with SGP4/SDP4 from mean elements (a TLE or a CCSDS OMM)."""
    notes = ()

    def _init_sgp4(self, ctx, satrec, name, perigee_error):
        self.vf = EarthSatellite.from_satrec(satrec, ctx.ts)
        self.vf.name = name or None
        m = self.vf.model
        if m.error or not m.no_kozai > 0:       # sgp4 does not raise on unreadable fields
            raise ObserverError(tr('TLE を解釈できません: {exc}', exc=f'sgp4 error {m.error}'))
        n = m.no_kozai / 60.0                       # rad/s
        a = (GM_EARTH / n ** 2) ** (1.0 / 3.0)
        e = m.ecco
        self.a_km, self.e = a, e
        self.period_min = 2 * math.pi / n / 60.0
        self.r_min_km = a * (1 - e)
        self.r_max_km = a * (1 + e) * 1.02
        self.v_max_km_s = math.sqrt(GM_EARTH * (2 / self.r_min_km - 1 / a)) * 1.02
        self.epoch_jd = float(self.vf.epoch.tt)
        if self.r_min_km < WGS84_A:
            raise ObserverError(perigee_error)

    def char_time_s(self):
        return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))

    def age_warnings(self, age):
        if age > 30:
            return [tr('TLE 元期から {age:.0f} 日離れています。SGP4 の位置誤差は数百 km 以上になり得るため、'
                       '結果は目安として扱ってください。', age=age)]
        if age > 7:
            return [tr('TLE 元期から {age:.0f} 日離れています（位置誤差は数 km〜数十 km 程度）。', age=age)]
        return []

    def warnings_for(self, ctx, jd):
        return list(self.notes) + self.age_warnings(abs(jd - self.epoch_jd))

    def decay_jd(self, jd_b, step=0.05):
        """The first TT Julian date after the epoch (up to ``jd_b``) at which SGP4 reports an error
        (6: decayed) or puts the satellite below 100 km; None if there is none."""
        jd = np.arange(self.epoch_jd, jd_b + step, step)
        if not jd.size:
            return None
        e, r, _ = self.vf.model.sgp4_array(np.floor(jd), jd - np.floor(jd))
        k = np.flatnonzero((e != 0) | ~(np.linalg.norm(r, axis=1) > WGS84_A + 100.0))     # ~(>) catches NaN
        return float(jd[k[0]]) if k.size else None

    def elements(self):
        m = self.vf.model
        # the mean elements themselves (TEME of epoch; for checking what was read)
        return {'period_min': self.period_min, 'perigee_km': self.r_min_km - WGS84_A,
                'apogee_km': self.a_km * (1 + self.e) - WGS84_A, 'a_km': self.a_km, 'e': self.e,
                'i_deg': math.degrees(m.inclo), 'raan_deg': math.degrees(m.nodeo),
                'argp_deg': math.degrees(m.argpo), 'm_deg': math.degrees(m.mo),
                'mean_motion_rev_per_day': m.no_kozai * 1440 / (2 * math.pi), 'bstar': m.bstar,
                'epoch_jd_tt': self.epoch_jd}


class TLEObserver(SGP4Observer):
    def __init__(self, ctx, line1, line2, name=''):
        line1, line2 = line1.strip(), line2.strip()
        if not (line1.startswith('1 ') and line2.startswith('2 ')):
            raise ObserverError(tr('TLE の 1 行目は "1 "、2 行目は "2 " で始まる必要があります'))
        try:
            sat = EarthSatellite(line1, line2, name or None, ctx.ts)
        except Exception as exc:  # pragma: no cover - sgp4 raises various errors
            raise ObserverError(tr('TLE を解釈できません: {exc}', exc=exc))
        # lines whose checksum (last digit) does not match: probably mistyped
        self.bad_lines = [k for k, ln in ((1, line1), (2, line2))
                          if len(ln) >= 69 and ln[68].isdigit() and int(ln[68]) != _tle_checksum(ln)]
        self.line1, self.line2 = line1, line2
        self.name = name or f'NORAD {sat.model.satnum}'
        self._init_sgp4(ctx, sat.model, self.name,
                        tr('この TLE の近地点は地球内部にあります（再突入済みの可能性）'))

    def warnings_for(self, ctx, jd):
        out = [tr('TLE の {n} 行目のチェックサムが合いません（写し間違いの可能性があります）', n=k) for k in self.bad_lines]
        return out + super().warnings_for(ctx, jd)

    def describe(self):
        return dict({'kind': 'space', 'model': 'tle', 'name': self.name, 'line1': self.line1,
                     'line2': self.line2, 'norad': self.vf.model.satnum}, **self.elements())


def _tle_checksum(line):
    return sum(int(c) if c.isdigit() else c == '-' for c in line[:68]) % 10


def tle_with_mean_anomaly(line2, m_deg):
    """Line 2 of a TLE with the mean anomaly at epoch (columns 44-51) replaced
    and the checksum redone: the same orbit with the satellite elsewhere on it."""
    line2 = line2.rstrip()
    if len(line2) < 68 or not line2.startswith('2 '):
        raise ObserverError(tr('TLE の 2 行目が短すぎます（69 文字の形式で指定してください）'))
    body = f'{line2[:43]}{float(m_deg) % 360.0:8.4f}{line2[51:68]}'
    return body + str(_tle_checksum(body))


class _KeplerVF(VectorFunction):
    center = 399
    ephemeris = None

    def __init__(self, name, epoch_jd, a, e, i, raan, argp, m0, j2=True):
        self.target = name
        self.epoch_jd = epoch_jd
        self.a, self.e = a, e
        self.i = math.radians(i)
        self.raan0 = math.radians(raan)
        self.argp0 = math.radians(argp)
        self.m0 = math.radians(m0)
        n0 = math.sqrt(GM_EARTH / a ** 3)
        if j2:
            p = a * (1 - e * e)
            f = 1.5 * J2_EARTH * (WGS84_A / p) ** 2
            si2 = math.sin(self.i) ** 2
            self.n = n0 * (1 + f * math.sqrt(1 - e * e) * (1 - 1.5 * si2))
            self.raan_dot = -f * self.n * math.cos(self.i)
            self.argp_dot = 0.5 * f * self.n * (4 - 5 * si2)
        else:
            self.n, self.raan_dot, self.argp_dot = n0, 0.0, 0.0

    def _at(self, t):
        dt = ((t.whole - self.epoch_jd) + t.tt_fraction) * DAY_S
        dt = np.asarray(dt, float)
        M = self.m0 + self.n * dt
        raan = self.raan0 + self.raan_dot * dt
        argp = self.argp0 + self.argp_dot * dt
        e = self.e
        E = M.copy() if np.ndim(M) else np.array(M)
        for _ in range(30):
            dE = (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
            E = E - dE
            if np.max(np.abs(dE)) < 1e-13:
                break
        cE, sE = np.cos(E), np.sin(E)
        a = self.a
        xp = a * (cE - e)
        yp = a * math.sqrt(1 - e * e) * sE
        vfac = self.n * a / (1 - e * cE)
        vxp = -vfac * sE
        vyp = vfac * math.sqrt(1 - e * e) * cE
        cO, sO = np.cos(raan), np.sin(raan)
        cw, sw = np.cos(argp), np.sin(argp)
        ci, si = math.cos(self.i), math.sin(self.i)
        # Perifocal -> inertial rotation (R3(-raan) R1(-i) R3(-argp)).
        r11 = cO * cw - sO * sw * ci
        r12 = -cO * sw - sO * cw * ci
        r21 = sO * cw + cO * sw * ci
        r22 = -sO * sw + cO * cw * ci
        r31 = sw * si
        r32 = cw * si
        pos = np.array([r11 * xp + r12 * yp, r21 * xp + r22 * yp, r31 * xp + r32 * yp])
        vel = np.array([r11 * vxp + r12 * vyp, r21 * vxp + r22 * vyp, r31 * vxp + r32 * vyp])
        return pos / AU_KM, vel * AU_PER_DAY_PER_KM_S, None, None


TROPICAL_YEAR_DAYS = 365.24219
# General precession in right ascension for a point on the equator ("m"), deg/yr.
PRECESSION_RA_DEG_PER_YEAR = 46.1 / 3600.0


def mean_sun_ra_deg(ctx, jd_tt):
    """Right ascension of the fictitious mean Sun (J2000 equator), degrees.
    Local mean solar time is defined by it: GMST = alpha + UT1 + 12 h."""
    t = ctx.ts.tt_jd(jd_tt)
    ut_h = ((float(t.ut1) - 0.5) % 1.0) * 24.0
    ra_of_date = ((float(t.gmst) - ut_h - 12.0) * 15.0) % 360.0
    return (ra_of_date - PRECESSION_RA_DEG_PER_YEAR * (jd_tt - 2451545.0) / 365.25) % 360.0


def raan_from_ltan(ctx, jd_tt, ltan_h):
    """Right ascension of the ascending node for a local (mean) solar time
    of the ascending node, e.g. 18.0 for a dusk-side ascending node."""
    return (mean_sun_ra_deg(ctx, jd_tt) + (ltan_h - 12.0) * 15.0) % 360.0


def ltan_from_raan(ctx, jd_tt, raan_deg):
    return ((raan_deg - mean_sun_ra_deg(ctx, jd_tt)) / 15.0 + 12.0) % 24.0


def sun_synchronous_inclination(a_km, e=0.0):
    """Inclination (deg) whose secular J2 node drift follows the mean Sun
    (360 deg per tropical year), with the same model as _KeplerVF."""
    target = 2 * math.pi / (TROPICAL_YEAR_DAYS * DAY_S)
    n0 = math.sqrt(GM_EARTH / a_km ** 3)
    f = 1.5 * J2_EARTH * (WGS84_A / (a_km * (1 - e * e))) ** 2
    i = math.radians(98.0)
    for _ in range(30):
        n = n0 * (1 + f * math.sqrt(1 - e * e) * (1 - 1.5 * math.sin(i) ** 2))
        c = -target / (f * n)
        if c < -1:
            raise ObserverError(tr('この軌道の大きさでは太陽同期軌道になりません（高度が高すぎます）'))
        i = math.acos(c)
    return math.degrees(i)


class KeplerObserver(Observer):
    def __init__(self, ctx, epoch_jd_tt, a_km, e, i_deg, raan_deg, argp_deg, m_deg,
                 j2=True, name=''):
        if not all(math.isfinite(v) for v in (epoch_jd_tt, a_km, e, i_deg, raan_deg, argp_deg, m_deg)):
            raise ObserverError(tr('軌道要素は数値で指定してください'))
        if not (0 <= e < 1):
            raise ObserverError(tr('離心率は 0 以上 1 未満で指定してください'))
        if a_km * (1 - e) <= WGS84_A:
            raise ObserverError(tr('近地点高度が地表より低くなっています'))
        self.name = name or tr('軌道要素で指定した衛星')
        self.params = dict(epoch_jd_tt=epoch_jd_tt, a_km=a_km, e=e, i_deg=i_deg,
                           raan_deg=raan_deg, argp_deg=argp_deg, m_deg=m_deg, j2=j2)
        self.vf = _KeplerVF(self.name, epoch_jd_tt, a_km, e, i_deg, raan_deg, argp_deg,
                            m_deg, j2)
        self.r_min_km = a_km * (1 - e)
        self.r_max_km = a_km * (1 + e)
        self.v_max_km_s = math.sqrt(GM_EARTH * (2 / self.r_min_km - 1 / a_km)) * 1.01
        self.period_min = 2 * math.pi / self.vf.n / 60

    def char_time_s(self):
        return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))

    def describe(self):
        d = {'kind': 'space', 'model': 'kepler', 'name': self.name,
             'period_min': self.period_min,
             'perigee_km': self.r_min_km - WGS84_A, 'apogee_km': self.r_max_km - WGS84_A}
        d.update(self.params)
        return d


# ---------------------------------------------------------------------------
# JPL Horizons
# ---------------------------------------------------------------------------
HORIZONS_URL = 'https://ssd.jpl.nasa.gov/api/horizons.api'
HERMITE_EPS_KM = 0.01         # largest position error allowed for the interpolation of Horizons vectors


def hermite_step_min(r_km, v_km_s, eps_km=HERMITE_EPS_KM):
    """Longest step (minutes) of the cubic Hermite interpolation of positions and velocities
    whose position error stays below ``eps_km`` for motion at radius ``r_km`` and speed
    ``v_km_s`` (the smallest over arrays).  The error is h^4 |x''''| / 384 with
    |x''''| = r (v / r)^4 for circular motion, so h = (r / v) (384 eps / r)^(1/4): 2 min in a
    low Earth orbit (60 min would give ~3,000 km), 22 min in the geostationary orbit (0.5 km)."""
    r = np.asarray(r_km, float)
    v = np.maximum(np.asarray(v_km_s, float), 1e-9)
    return float(np.min((r / v) * (384.0 * eps_km / r) ** 0.25 / 60.0))


def _pericentres(jd, pos, vel, gm, r_body, a_max, window):
    """The distances and speeds at the pericentres (above ``r_body``) of the osculating two-body
    orbits of states relative to a body: of the bound orbits with a < ``a_max``, and of the
    hyperbolic ones whose pericentre passage falls within ``window`` (TDB JDs; a flyby that a
    coarse table can miss)."""
    r, v = norm(pos), norm(vel)
    h = norm(np.cross(pos, vel, axis=0))
    en = 0.5 * v * v - gm / r                         # energy per unit mass
    e = np.sqrt(np.maximum(0.0, 1.0 + 2.0 * en * h * h / (gm * gm)))
    rp = h * h / (gm * (1.0 + e))
    with np.errstate(divide='ignore', invalid='ignore'):
        a = -gm / (2.0 * en)                          # negative for a hyperbola
        # time from the pericentre of a hyperbola: r = |a| (e cosh F - 1), M = e sinh F - F
        F = np.arccosh(np.maximum(1.0, (1.0 - r / a) / e))
        dt = (e * np.sinh(F) - F) * np.sqrt(-a ** 3 / gm)
        t_peri = jd - np.sign(np.sum(pos * vel, axis=0)) * dt / DAY_S
        keep = np.where(en < 0, a < a_max, (t_peri >= window[0]) & (t_peri <= window[1])) & (rp > r_body)
    return list(rp[keep]), list(h[keep] / rp[keep])


def _orbit_step_min(jd, pos, vel, ctx=None):
    """hermite_step_min() of a tabulated orbit (geocentric states at the TDB JDs ``jd``): at the
    smallest distance with the largest speed, at the largest distance, at the perigees of the
    osculating orbits about the Earth (a coarse table can miss the perigee of an eccentric orbit
    or of a flyby) and, within the Moon's sphere of influence, at the distances and speeds
    relative to the Moon and at the perilunes (a lunar orbiter)."""
    r, v = norm(pos), norm(vel)
    k = int(np.argmax(r))
    rr, vv = [float(r.min()), float(r[k])], [float(v.max()), float(v[k])]
    window = (float(jd[0]), float(jd[-1]))
    near = np.zeros(r.size, bool)
    if ctx is not None:             # (the survey may reach a little past the ends of the ephemeris)
        moon = (ctx.moon - ctx.earth).at(ctx.ts.tdb_jd(np.clip(jd, ctx.jd_min, ctx.jd_max)))
        mp, mv = pos - moon.position.km, vel - moon.velocity.km_per_s
        near = (norm(mp) < MOON_SOI_KM) & (jd >= ctx.jd_min) & (jd <= ctx.jd_max)
        if np.any(near):
            mp, mv = mp[:, near], mv[:, near]
            rr += list(norm(mp))
            vv += list(norm(mv))
            p, s = _pericentres(jd[near], mp, mv, GM_MOON, MOON_RADIUS_MEAN, MOON_SOI_KM, window)
            rr += p
            vv += s
    # Earth satellites (farther bound orbits follow the Moon) and flybys of the Earth
    far = ~near
    p, s = _pericentres(jd[far], pos[:, far], vel[:, far], GM_EARTH, WGS84_A, 150_000.0, window)
    return hermite_step_min(rr + p, vv + s)


def _table_step_min(jd, pos, vel, eps_km=HERMITE_EPS_KM):
    """The step (minutes) for an interpolation error below ``eps_km`` measured on a table itself:
    each sample interpolated from its neighbours (twice the table's step), the error scaled by
    h^4 (inf with fewer than 3 samples).  It holds for any motion the table resolves; a sharper
    turn than the table's step is underestimated (the error saturates at the size of the turn)."""
    if jd.size < 3:
        return math.inf
    h = (jd[2:] - jd[:-2]) * DAY_S
    s = (jd[1:-1] - jd[:-2]) * DAY_S / h
    p0, p1 = pos[:, :-2], pos[:, 2:]
    v0, v1 = vel[:, :-2] * h, vel[:, 2:] * h
    s2, s3 = s * s, s * s * s
    p = (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * v0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * v1
    err = np.maximum(norm(p - pos[:, 1:-1]), 1e-12)
    return float(np.min(h / 60.0 * (eps_km / err) ** 0.25))


def _horizons_request(command, jd_start_tdb, jd_stop_tdb, step_min):
    params = {
        'format': 'json',
        'COMMAND': f"'{command}'",
        'OBJ_DATA': 'NO',
        'MAKE_EPHEM': 'YES',
        'EPHEM_TYPE': 'VECTORS',
        'CENTER': "'500@399'",
        'START_TIME': f"'JD{jd_start_tdb:.6f}'",
        'STOP_TIME': f"'JD{jd_stop_tdb:.6f}'",
        'STEP_SIZE': f"'{int(step_min)} m'",
        'VEC_TABLE': '2',
        'REF_SYSTEM': 'ICRF',
        'REF_PLANE': 'FRAME',
        'VEC_CORR': 'NONE',
        'OUT_UNITS': 'KM-S',
        'CSV_FORMAT': 'YES',
        'VEC_LABELS': 'NO',
        'TIME_TYPE': 'TDB',
    }
    payload = _get_json('JPL Horizons', HORIZONS_URL + '?' + urllib.parse.urlencode(params), 90)
    text = payload.get('result', '')
    if '$$SOE' not in text:
        msg = payload.get('error') or text.strip().splitlines()[-12:]
        if isinstance(msg, list):
            msg = '\n'.join(msg)
        raise ObserverError(tr('JPL Horizons からデータを取得できませんでした:\n{msg}', msg=msg))
    body = text.split('$$SOE', 1)[1].split('$$EOE', 1)[0]
    rows = []
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 8:
            continue
        rows.append([float(parts[0])] + [float(v) for v in parts[2:8]])
    if not rows:
        raise ObserverError(tr('JPL Horizons の応答にデータ行がありません'))
    arr = np.array(rows)
    return arr[:, 0], arr[:, 1:4].T, arr[:, 4:7].T


class _HorizonsVF(VectorFunction):
    center = 399
    ephemeris = None

    def __init__(self, name):
        self.target = name
        self.jd = np.zeros(0)
        self.pos = np.zeros((3, 0))
        self.vel = np.zeros((3, 0))

    def add(self, jd, pos, vel):
        jd = np.concatenate([self.jd, jd])
        pos = np.concatenate([self.pos, pos], axis=1)
        vel = np.concatenate([self.vel, vel], axis=1)
        order = np.argsort(jd, kind='stable')
        jd, pos, vel = jd[order], pos[:, order], vel[:, order]
        keep = np.concatenate([[True], np.diff(jd) > 1e-9])
        self.jd, self.pos, self.vel = jd[keep], pos[:, keep], vel[:, keep]

    def covers(self, a, b):
        if self.jd.size < 2:
            return False
        i, j = np.searchsorted(self.jd, [a, b])
        if i == 0 or j >= self.jd.size:
            return False
        # no gap between fetched chunks inside [a, b] (all chunks have the same step)
        return float(np.max(np.diff(self.jd[i - 1:j + 1]))) <= 1.5 * float(np.median(np.diff(self.jd)))

    def _at(self, t):
        x = np.asarray(t.whole + t.tdb_fraction, float)
        shape = x.shape
        x = np.atleast_1d(x)
        jd = self.jd
        if jd.size < 2 or np.any(x < jd[0] - 1e-9) or np.any(x > jd[-1] + 1e-9):
            raise ObserverError(tr('JPL Horizons の取得範囲外の時刻が要求されました'))
        i = np.clip(np.searchsorted(jd, x) - 1, 0, jd.size - 2)
        h = (jd[i + 1] - jd[i]) * DAY_S
        s = (x - jd[i]) * DAY_S / h
        p0, p1 = self.pos[:, i], self.pos[:, i + 1]
        v0, v1 = self.vel[:, i] * h, self.vel[:, i + 1] * h
        s2, s3 = s * s, s * s * s
        h00 = 2 * s3 - 3 * s2 + 1
        h10 = s3 - 2 * s2 + s
        h01 = -2 * s3 + 3 * s2
        h11 = s3 - s2
        p = h00 * p0 + h10 * v0 + h01 * p1 + h11 * v1
        dh00 = 6 * s2 - 6 * s
        dh10 = 3 * s2 - 4 * s + 1
        dh01 = -6 * s2 + 6 * s
        dh11 = 3 * s2 - 2 * s
        v = (dh00 * p0 + dh10 * v0 + dh01 * p1 + dh11 * v1) / h
        p = p.reshape((3,) + shape)
        v = v.reshape((3,) + shape)
        return p / AU_KM, v * AU_PER_DAY_PER_KM_S, None, None


class HorizonsObserver(Observer):
    needs_network = True
    MAX_POINTS = 60_000            # one request
    MAX_TOTAL_POINTS = 600_000     # all the windows of one search (~34 MB)

    def __init__(self, ctx, command, step_min=5, name=''):
        command = str(command).strip()
        if not command:
            raise ObserverError(tr('Horizons の天体 ID を指定してください（例: -170 = JWST）'))
        self.command = command
        self.step_min = max(1, int(step_min))
        self.requested_step_min = self.step_min    # survey() makes step_min finer for close orbits
        self.survey_step_min = None
        self.step_unresolved = False
        self.name = name or f'Horizons {command}'
        self.vf = _HorizonsVF(self.name)
        self.ctx = ctx
        self.r_min_km = WGS84_A
        self.r_max_km = WGS84_A
        self.v_max_km_s = 8.0
        self.coverage = None

    def _fetch(self, jd_a, jd_b, step_min):
        n = (jd_b - jd_a) * 1440 / step_min
        if n > self.MAX_POINTS:
            raise ObserverError(
                tr('Horizons から取得するデータ点が多すぎます（{n} 点）。期間を短くするか'
                   '刻み幅を大きくしてください。', n=int(n)))
        key = f'{self.command}_{jd_a:.5f}_{jd_b:.5f}_{step_min}'.replace('/', '_')
        safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in key)
        path = CACHE_DIR / f'horizons_{safe}.npz'
        if path.exists():
            d = np.load(path)
            return d['jd'], d['pos'], d['vel']
        jd, pos, vel = _horizons_request(self.command, jd_a, jd_b, step_min)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, jd=jd, pos=pos, vel=vel)
        return jd, pos, vel

    def survey(self, jd_a, jd_b):
        """Coarse fetch to learn the distance range (used for pre-filtering) and the step needed
        for an interpolation error below HERMITE_EPS_KM, from the motion about the Earth and the
        Moon and as measured on the table: a coarser step_min is made finer (requested_step_min
        keeps the one asked for)."""
        span = jd_b - jd_a
        step = max(self.step_min, int(math.ceil(span * 1440 / 3000)))
        jd, pos, vel = self._fetch(jd_a - 0.01, jd_b + 0.01 + step / 1440.0, step)
        self.coverage = (float(jd[0]), float(jd[-1]))
        r = norm(pos)
        v = norm(vel)
        self.r_min_km = float(r.min()) * 0.98
        self.r_max_km = float(r.max()) * 1.02
        self.v_max_km_s = float(v.max()) * 1.05 + 0.01
        if self.r_min_km < WGS84_A:
            self.r_min_km = WGS84_A
        self.survey_step_min = step
        model = _orbit_step_min(jd, pos, vel, self.ctx)
        measured = _table_step_min(jd, pos, vel)
        self.step_limit_min = min(model, measured)
        if self.step_min > self.step_limit_min:
            self.step_min = max(1, int(math.floor(self.step_limit_min)))
        # the table turns more sharply than the gravity of the Earth and the Moon explains and than
        # its own step resolves, so the step measured on it may still be too coarse (step_warnings)
        self.step_unresolved = bool(measured < min(0.5 * model, step) and self.step_min > 0.5 * measured)
        return jd, pos, vel

    def step_warnings(self):
        """The warnings about the step that survey() chose (for the results)."""
        out = []
        if self.step_min < self.requested_step_min:
            out.append(tr('地球や月に近い軌道のため、JPL Horizons の位置の刻みを {old} 分から {new} 分に'
                          '細かくしました（位置の補間の誤差を {eps:g} m 以下にするため）',
                          old=self.requested_step_min, new=self.step_min, eps=HERMITE_EPS_KM * 1000))
        if self.step_unresolved:
            out.append(tr('JPL Horizons の軌道が {step} 分刻みの下調べでは追えないほど急に曲がっているため、'
                          '位置の補間の誤差が {eps:g} m を超えるおそれがあります（期間を短くするか、位置の刻みを'
                          '小さくしてください）', step=self.survey_step_min, eps=HERMITE_EPS_KM * 1000))
        return out

    def prepare(self, ctx, jd_windows):
        # Pad by one output step so that the samples bracket the window; a long window is
        # fetched in parts of at most MAX_POINTS points.
        step = self.step_min / 1440.0
        pad = step + 0.01
        part = (self.MAX_POINTS - 10) * step
        todo = [(a, b) for a, b in jd_windows if not self.vf.covers(a - 0.002, b + 0.002)]
        n = sum(b - a + 2 * pad for a, b in todo) / step
        if n > self.MAX_TOTAL_POINTS:
            raise ObserverError(tr('JPL Horizons から取得するデータ点が多すぎます（位置の刻み {step} 分で {n} 点）。'
                                   '期間を短くしてください', step=self.step_min, n=int(n)))
        for a, b in todo:
            edges = np.linspace(a - pad, b + pad, int(math.ceil((b - a + 2 * pad) / part)) + 1)
            for lo, hi in zip(edges[:-1], edges[1:]):
                jd, pos, vel = self._fetch(lo, hi, self.step_min)
                self.vf.add(jd, pos, vel)
        for a, b in jd_windows:
            if not self.vf.covers(a - 1e-4, b + 1e-4):
                raise ObserverError(tr('JPL Horizons から必要な期間の軌道データを取得できませんでした'
                                       '（探査機の軌道データが提供されている期間外の可能性があります）'))

    def char_time_s(self):
        return max(60.0, min(self.r_min_km / self.v_max_km_s, 6 * 3600.0))

    def describe(self):
        return {'kind': 'space', 'model': 'horizons', 'name': self.name,
                'command': self.command, 'step_min': self.step_min,
                'step_min_requested': self.requested_step_min,
                'r_min_km': self.r_min_km, 'r_max_km': self.r_max_km}


# ---------------------------------------------------------------------------
# NASA SSCWeb (Satellite Situation Center) - historical orbits
# ---------------------------------------------------------------------------
SSC_URL = 'https://sscweb.gsfc.nasa.gov/WS/sscr/2/locations/{sat}/{a},{b}/geij2000/'


def _ssc_request(sat, utc_a, utc_b):
    """Geocentric GEI J2000 positions (km) of SSCWeb satellite ``sat`` between
    two datetimes (UTC). Returns (list of ISO UTC strings, (3, N) array)."""
    url = SSC_URL.format(sat=urllib.parse.quote(sat), a=utc_a.strftime('%Y%m%dT%H%M%SZ'),
                         b=utc_b.strftime('%Y%m%dT%H%M%SZ'))
    payload = _get_json('NASA SSCWeb', url, 120, {'Accept': 'application/json'})
    # SSCWeb's JSON wraps every object as ["java.class.Name", {...}].
    result = payload[1]['Result'][1]
    if result.get('StatusCode') != 'SUCCESS':
        text = result.get('StatusText', ['', []])[1]
        raise ObserverError(tr('NASA SSCWeb からデータを取得できませんでした: {msg}', msg=' '.join(text) or result))
    data = result.get('Data', ['', []])[1]
    if not data:
        raise ObserverError(tr('NASA SSCWeb に {sat} の {start}〜{end} の軌道データがありません', sat=sat,
                               start=f'{utc_a:%Y-%m-%d %H:%M}', end=f'{utc_b:%Y-%m-%d %H:%M}'))
    sd = data[0][1]
    coords = next(c[1] for c in sd['Coordinates'][1] if c[1]['CoordinateSystem'] == 'GEI_J_2000')
    xyz = np.array([coords['X'][1], coords['Y'][1], coords['Z'][1]], float)
    times = [t[1] for t in sd['Time'][1]]
    return times, xyz


class _SampledVF(VectorFunction):
    """8-point Lagrange interpolation of tabulated geocentric positions
    (TT Julian dates); velocities are the derivative of the interpolant."""
    center = 399
    ephemeris = None
    NPT = 8

    def __init__(self, name):
        self.target = name
        self.jd = np.zeros(0)
        self.pos = np.zeros((3, 0))

    def add(self, jd, pos):
        jd = np.concatenate([self.jd, jd])
        pos = np.concatenate([self.pos, pos], axis=1)
        order = np.argsort(jd, kind='stable')
        jd, pos = jd[order], pos[:, order]
        keep = np.concatenate([[True], np.diff(jd) > 1e-9])
        self.jd, self.pos = jd[keep], pos[:, keep]

    def covers(self, a, b):
        if self.jd.size < self.NPT:
            return False
        i, j = np.searchsorted(self.jd, [a, b])
        if i == 0 or j >= self.jd.size:
            return False
        # no gap longer than 3 samples inside [a, b]
        seg = self.jd[i - 1:j + 1]
        return float(np.max(np.diff(seg))) <= 3.5 * float(np.median(np.diff(self.jd)))

    def _at(self, t):
        jd0 = self.jd[0]
        x = np.asarray((t.whole - jd0) + t.tt_fraction, float)
        shape = x.shape
        x = np.atleast_1d(x) * DAY_S
        nodes = (self.jd - jd0) * DAY_S
        n = self.NPT
        if nodes.size < n or np.any(x < nodes[0] - 1e-3) or np.any(x > nodes[-1] + 1e-3):
            raise ObserverError(tr('NASA SSCWeb の取得範囲外の時刻が要求されました'))
        i0 = np.clip(np.searchsorted(nodes, x) - n // 2, 0, nodes.size - n)
        idx = i0[:, None] + np.arange(n)[None, :]             # (N, n)
        X = nodes[idx]
        d = x[:, None] - X                                     # (N, n)
        L = np.empty_like(d)
        dL = np.empty_like(d)
        for j in range(n):
            others = [m for m in range(n) if m != j]
            den = np.prod([X[:, j] - X[:, m] for m in others], axis=0)
            L[:, j] = np.prod([d[:, m] for m in others], axis=0) / den
            dL[:, j] = sum(np.prod([d[:, m] for m in others if m != k], axis=0)
                           for k in others) / den
        P = self.pos[:, idx]                                   # (3, N, n)
        p = np.sum(P * L[None], axis=2).reshape((3,) + shape)
        v = np.sum(P * dL[None], axis=2).reshape((3,) + shape)
        return p / AU_KM, v * AU_PER_DAY_PER_KM_S, None, None


SSC_OBSERVATORIES_URL = 'https://sscweb.gsfc.nasa.gov/WS/sscr/2/observatories'
_ssc_list = None


def ssc_satellites(max_age_days=7.0):
    """Satellites available from SSCWeb: [{'id', 'name', 'start', 'end',
    'resolution_s'}] (cached in data/cache for a week)."""
    global _ssc_list
    path = CACHE_DIR / 'ssc_observatories.json'
    if _ssc_list is not None:
        return _ssc_list
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * DAY_S
    if not fresh:
        try:
            with _urlopen(SSC_OBSERVATORIES_URL, 60, {'Accept': 'application/json'}) as resp:
                raw = resp.read().decode('utf-8')
            json.loads(raw)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding='utf-8')
        except Exception as exc:
            if not path.exists():
                raise ObserverError(tr('NASA SSCWeb の衛星一覧を取得できませんでした: {exc}', exc=exc))
    payload = json.loads(path.read_text(encoding='utf-8'))
    out = []
    for item in payload[1]['Observatory'][1]:
        o = item[1]
        out.append({'id': o['Id'], 'name': o['Name'], 'start': o['StartTime'][1][:19] + 'Z',
                    'end': o['EndTime'][1][:19] + 'Z', 'resolution_s': o.get('Resolution')})
    out.sort(key=lambda o: o['name'].lower())
    _ssc_list = out
    return out


class SSCWebObserver(Observer):
    """Satellite whose (historical) orbit is taken from NASA SSCWeb.

    CelesTrak only serves the latest TLE, so past events seen from a
    satellite (e.g. eclipses observed by Hinode) need archived orbits.
    SSCWeb tabulates positions (mostly every 60 s) for about 300 science
    satellites and spacecraft.
    """
    needs_network = True
    CHUNK_DAYS = 7.0          # longest single request
    SURVEY_MAX_DAYS = 15.0    # longest orbit sampled to learn the distance range
    MAX_DAYS = 400.0          # total data fetched for one search

    def __init__(self, ctx, sat_id, name=''):
        sat_id = str(sat_id).strip().lower()
        if not sat_id:
            raise ObserverError(tr('SSCWeb の衛星 ID を指定してください（例: hinode）'))
        self.sat_id = sat_id
        self.name = name or f'SSCWeb {sat_id}'
        self.ctx = ctx
        self.vf = _SampledVF(self.name)
        self.surveyed = False
        self.r_min_km = WGS84_A
        self.r_max_km = WGS84_A
        self.v_max_km_s = 8.0
        self.period_min = 90.0
        self.r_range = None

    def coverage_jd(self):
        """(start, end) TT Julian dates of the SSCWeb orbit, or None if the
        satellite list cannot be obtained."""
        try:
            sats = ssc_satellites()
        except ObserverError:
            return None
        sat = next((o for o in sats if o['id'] == self.sat_id), None)
        if sat is None:
            raise ObserverError(tr('NASA SSCWeb に衛星 ID「{sat}」はありません', sat=self.sat_id))
        t = [self.ctx.ts.utc(int(x[0:4]), int(x[5:7]), int(x[8:10]), int(x[11:13]), int(x[14:16]),
                             int(x[17:19])) for x in (sat['start'], sat['end'])]
        return float(t[0].tt), float(t[1].tt)

    def _fetch_one(self, jd_a, jd_b):
        ts = self.ctx.ts
        ua = ts.tt_jd(jd_a).utc_datetime().replace(microsecond=0, tzinfo=None)
        ub = ts.tt_jd(jd_b).utc_datetime().replace(microsecond=0, tzinfo=None)
        key = f'ssc_{self.sat_id}_{ua:%Y%m%dT%H%M%S}_{ub:%Y%m%dT%H%M%S}'
        safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in key)
        path = CACHE_DIR / f'{safe}.npz'
        if path.exists():
            d = np.load(path)
            return d['jd'], d['pos']
        times, xyz = _ssc_request(self.sat_id, ua, ub)
        t = ts.utc([int(s[0:4]) for s in times], [int(s[5:7]) for s in times],
                   [int(s[8:10]) for s in times], [int(s[11:13]) for s in times],
                   [int(s[14:16]) for s in times], [float(s[17:23]) for s in times])
        jd = np.asarray(t.tt, float)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, jd=jd, pos=xyz)
        return jd, xyz

    def _fetch(self, jd_a, jd_b):
        n = max(1, int(math.ceil((jd_b - jd_a) / self.CHUNK_DAYS)))
        edges = np.linspace(jd_a, jd_b, n + 1)
        parts = [self._fetch_one(a, b) for a, b in zip(edges[:-1], edges[1:])]
        return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts], axis=1)

    def survey(self, jd_a, jd_b):
        """Learn the distance range from (at least) one full orbit at the start
        of the search interval."""
        jd, pos = self._fetch(jd_a, jd_a + 1.0)
        r0 = float(norm(pos[:, 0]))
        v0 = float(norm(pos[:, 1] - pos[:, 0])) / ((jd[1] - jd[0]) * DAY_S)
        inv_a = 2.0 / r0 - v0 * v0 / GM_EARTH
        if inv_a > 0:
            period_d = 2 * math.pi * math.sqrt(inv_a ** -3 / GM_EARTH) / DAY_S
            if period_d > 0.9:
                extra = min(1.2 * period_d, self.SURVEY_MAX_DAYS)
                jd2, pos2 = self._fetch(jd_a + 1.0, jd_a + extra)
                jd, pos = np.concatenate([jd, jd2]), np.concatenate([pos, pos2], axis=1)
        self.vf.add(jd, pos)
        jd, pos = self.vf.jd, self.vf.pos      # sorted, without the duplicated boundary sample (0/0 speed)
        r = norm(pos)
        v = norm(np.diff(pos, axis=1)) / (np.diff(jd) * DAY_S)
        self.r_range = (float(r.min()), float(r.max()))
        self.r_min_km = max(float(r.min()) * 0.97, WGS84_A)
        self.r_max_km = float(r.max()) * 1.03
        self.v_max_km_s = float(v.max()) * 1.05
        a = 0.5 * (r.min() + r.max())
        self.period_min = 2 * math.pi * math.sqrt(a ** 3 / GM_EARTH) / 60.0
        self.surveyed = True

    def prepare(self, ctx, jd_windows):
        pad = 10.0 / 1440.0
        todo = [(a, b) for a, b in jd_windows if not self.vf.covers(a - pad / 2, b + pad / 2)]
        days = sum(b - a for a, b in todo)
        if days > self.MAX_DAYS:
            raise ObserverError(tr('NASA SSCWeb から取得する軌道データが多すぎます（{days:.0f} 日分）。'
                                   '期間を短くしてください', days=days))
        for a, b in todo:
            jd, pos = self._fetch(a - pad, b + pad)
            self.vf.add(jd, pos)
        for a, b in jd_windows:
            if not self.vf.covers(a - 1e-4, b + 1e-4):
                raise ObserverError(tr('NASA SSCWeb から必要な期間の軌道データを取得できませんでした'
                                       '（欠損または提供期間外の可能性があります）'))

    def char_time_s(self):
        return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))

    def describe(self):
        d = {'kind': 'space', 'model': 'sscweb', 'name': self.name, 'id': self.sat_id}
        if self.surveyed:
            d.update(period_min=self.period_min, perigee_km=self.r_range[0] - WGS84_A,
                     apogee_km=self.r_range[1] - WGS84_A)
        return d


# ---------------------------------------------------------------------------
# Tabulated states (CCSDS OEM, OCM trajectories)
# ---------------------------------------------------------------------------
def _lagrange_basis(U, u):
    """Lagrange basis polynomials of the nodes U (n, k) at u (n,), and their derivatives."""
    n, k = U.shape
    d = u[:, None] - U
    L = np.empty((n, k))
    dL = np.empty((n, k))
    for j in range(k):
        others = [m for m in range(k) if m != j]
        den = np.prod([U[:, j] - U[:, m] for m in others], axis=0)
        L[:, j] = np.prod([d[:, m] for m in others], axis=0) / den
        dL[:, j] = sum(np.prod([d[:, m] for m in others if m != q], axis=0) for q in others) / den
    return L, dL


def interpolate_states(seg, s):
    """Positions and velocities (n, 3) of a table of states at times s (n,) in the table's seconds:
    ``seg`` = {'s' (m,), 'pos' (m, 3), 'vel' (m, 3) or None, 'method' ('hermite' | 'lagrange' |
    'linear'), 'npts'}.  Hermite uses positions and velocities (degree 2 npts - 1); Lagrange the
    positions (velocities interpolated likewise when given, else the derivative)."""
    x, P, V = seg['s'], seg['pos'], seg['vel']
    n = x.size
    method = seg['method'] if V is not None or seg['method'] != 'hermite' else 'lagrange'
    k = 2 if method == 'linear' else max(2, min(seg['npts'], n))
    i = np.searchsorted(x, s)
    i0 = np.clip(i - k // 2, 0, n - k)
    idx = i0[:, None] + np.arange(k)[None, :]
    X = x[idx]
    c = 0.5 * (X[:, 0] + X[:, -1])
    hs = (X[:, -1] - X[:, 0]) / (k - 1)
    U = (X - c[:, None]) / hs[:, None]
    u = (s - c) / hs
    Pw = P[idx]
    if method == 'linear':
        f = (u - U[:, 0]) / (U[:, 1] - U[:, 0])
        pos = Pw[:, 0] + f[:, None] * (Pw[:, 1] - Pw[:, 0])
        vel = (V[idx][:, 0] + f[:, None] * (V[idx][:, 1] - V[idx][:, 0]) if V is not None
               else (Pw[:, 1] - Pw[:, 0]) / (X[:, 1] - X[:, 0])[:, None])
        return pos, vel
    L, dL = _lagrange_basis(U, u)
    if method == 'hermite':
        Vw = V[idx] * hs[:, None, None]
        a = np.zeros_like(U)
        for j in range(k):
            a[:, j] = sum(1.0 / (U[:, j] - U[:, m]) for m in range(k) if m != j)
        du = u[:, None] - U
        L2 = L * L
        wp = (1 - 2 * a * du) * L2
        wv = du * L2
        dwp = -2 * a * L2 + (1 - 2 * a * du) * 2 * L * dL
        dwv = L2 + du * 2 * L * dL
        pos = np.einsum('nk,nkc->nc', wp, Pw) + np.einsum('nk,nkc->nc', wv, Vw)
        vel = (np.einsum('nk,nkc->nc', dwp, Pw) + np.einsum('nk,nkc->nc', dwv, Vw)) / hs[:, None]
        return pos, vel
    pos = np.einsum('nk,nkc->nc', L, Pw)
    vel = (np.einsum('nk,nkc->nc', L, V[idx]) if V is not None
           else np.einsum('nk,nkc->nc', dL, Pw) / hs[:, None])
    return pos, vel


def _merge_intervals(ivs, gap=1e-6):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _clip(windows, intervals):
    out = []
    for a, b in windows:
        for lo, hi in intervals:
            x, y = max(a, lo), min(b, hi)
            if y > x:
                out.append((x, y))
    return out


def _period_text(ctx, jd_a, jd_b):
    from .timeutil import iso_from_jd
    return span(iso_from_jd(ctx, jd_a)[:16].replace('T', ' '), iso_from_jd(ctx, jd_b)[:16].replace('T', ' '))


class _EphemerisVF(VectorFunction):
    center = 399
    ephemeris = None

    def __init__(self, name, j0, segments):
        self.target = name
        self.j0 = j0
        self.segs = segments

    def _at(self, t):
        s = ((np.asarray(t.whole, float) - self.j0[0]) + (np.asarray(t.tt_fraction, float) - self.j0[1])) * DAY_S
        shape = np.shape(s)
        s = np.atleast_1d(s)
        pos = np.empty((s.size, 3))
        vel = np.empty((s.size, 3))
        todo = np.ones(s.size, bool)
        for seg in self.segs:              # in priority order (the first segment that covers a time)
            m = todo & (s >= seg['lo'] - 1e-3) & (s <= seg['hi'] + 1e-3)
            if np.any(m):
                pos[m], vel[m] = interpolate_states(seg, s[m])
                todo &= ~m
        if np.any(todo):
            raise ObserverError(tr('CCSDS の軌道データの範囲外の時刻が要求されました'))
        return (pos.T.reshape((3,) + shape) / AU_KM, vel.T.reshape((3,) + shape) * AU_PER_DAY_PER_KM_S,
                None, None)


class EphemerisObserver(Observer):
    """A spacecraft whose orbit is given as tables of states (CCSDS OEM, OCM trajectories):
    ``segments`` = [{'s': seconds of TT since ``j0`` (a TT JD pair), 'pos', 'vel' (geocentric GCRS,
    km and km/s, 'vel' None for positions only), 'method', 'npts', 'lo', 'hi' (the useable span,
    seconds)}] in priority order (the first that covers a time is used)."""

    def __init__(self, ctx, name, segments, j0, info=None, notes=()):
        self.ctx = ctx
        self.name = name
        self.j0 = j0
        self.segs = segments
        self.info = dict(info or {})
        self.notes = list(notes)
        jd0 = j0[0] + j0[1]
        self.intervals = _merge_intervals([(jd0 + g['lo'] / DAY_S, jd0 + g['hi'] / DAY_S) for g in segments])
        self.vf = _EphemerisVF(name, j0, segments)
        r = np.concatenate([norm(g['pos'].T) for g in segments])
        v = np.concatenate([norm(g['vel'].T) if g['vel'] is not None
                            else norm(np.diff(g['pos'], axis=0).T) / np.maximum(np.diff(g['s']), 1e-9)
                            for g in segments if g['s'].size > 1])
        self.r_range = (float(r.min()), float(r.max()))
        self.r_min_km = max(float(r.min()) * 0.98, 100.0)
        self.r_max_km = float(r.max()) * 1.02
        self.v_max_km_s = float(v.max()) * 1.05 + 0.01

    def char_time_s(self):
        return max(60.0, min(self.r_min_km / self.v_max_km_s, 6 * 3600.0))

    def limit_period(self, ctx, jd_a, jd_b, margin):
        lo, hi = self.intervals[0][0], self.intervals[-1][1]
        if hi <= jd_a or lo >= jd_b:
            raise ObserverError(tr('指定期間には CCSDS の軌道データがありません（データの期間 {period}）',
                                   period=_period_text(self.ctx, lo, hi)))
        out = []
        a2, b2 = max(jd_a, lo), min(jd_b, hi)
        if a2 > jd_a or b2 < jd_b:
            out.append(tr('CCSDS の軌道データがある期間（{period}）に限定して計算しました', period=_period_text(self.ctx, lo, hi)))
        gaps = sum(1 for (x, y), (z, w) in zip(self.intervals[:-1], self.intervals[1:]) if z > a2 and y < b2)
        if gaps:
            out.append(tr('CCSDS の軌道データが途切れている {n} か所の期間は計算していません', n=gaps))
        return a2, b2, list(self.notes) + out

    def clip_windows(self, windows):
        return _clip(windows, self.intervals)

    def interval_at(self, jd):
        return min(self.intervals, key=lambda iv: 0 if iv[0] <= jd <= iv[1] else min(abs(jd - iv[0]), abs(jd - iv[1])))

    def prepare(self, ctx, jd_windows):
        for a, b in jd_windows:
            if not any(lo - 1e-8 <= a and b <= hi + 1e-8 for lo, hi in self.intervals):
                raise ObserverError(tr('指定した時刻には CCSDS の軌道データがありません（データの期間 {period}）',
                                       period=', '.join(_period_text(self.ctx, lo, hi) for lo, hi in self.intervals[:4])))

    def warnings_for(self, ctx, jd):
        return list(self.notes)

    def describe(self):
        d = {'kind': 'space', 'model': 'ccsds', 'name': self.name}
        d.update(self.info)
        d.update(perigee_km=self.r_range[0] - WGS84_A, apogee_km=self.r_range[1] - WGS84_A,
                 intervals=[[a, b] for a, b in self.intervals])
        return d


# ---------------------------------------------------------------------------
# Numerically propagated states (CCSDS OPM, OCM states, orbit determination from a TDM)
# ---------------------------------------------------------------------------
MAX_PROP_REVS = 1000            # longest propagation from a state, in revolutions ...
MAX_PROP_DAYS = 20 * 365.25     # ... and in days


class _PropagatedVF(VectorFunction):
    center = 399
    ephemeris = None

    def __init__(self, obs):
        self.obs = obs
        self.target = obs.name

    def _at(self, t):
        pos, vel = self.obs.states(t)
        return pos / AU_KM, vel * AU_PER_DAY_PER_KM_S, None, None


class PropagatedObserver(Observer):
    """A spacecraft whose orbit is propagated numerically (propagate.py) from states:
    ``anchors`` = [(Skyfield Time, state (6,) relative to ``center`` in ICRS axes, km and km/s)];
    with several states each is used up to halfway to the next.  ``maneuvers`` = [(Skyfield Time,
    function(state) -> delta-v (3,))]; ``drag`` / ``srp``: C_D A / m and C_R A / m (m^2/kg)."""

    def __init__(self, ctx, anchors, center='earth', drag=None, srp=None, maneuvers=(), name='', info=None,
                 notes=(), label='OPM'):
        from .propagate import Forces, Trajectory, elements as osc_elements
        self.ctx, self.name, self.center, self.label = ctx, name, center, label
        self.info = dict(info or {})
        self.notes = list(notes)
        self.anchors = []
        for t, x in sorted(anchors, key=lambda a: float(a[0].tt)):
            jd0 = (float(t.whole), float(t.tt_fraction))
            forces = Forces(ctx, jd0, center, drag=drag, srp=srp)
            el = osc_elements(x[:3], x[3:], forces.gm)
            revs = MAX_PROP_REVS * el['period_min'] * 60.0 if el['period_min'] else math.inf
            lim = min(MAX_PROP_DAYS * DAY_S, revs)
            mans = [(((float(tm.whole) - jd0[0]) + (float(tm.tt_fraction) - jd0[1])) * DAY_S, fn) for tm, fn in maneuvers]
            self.anchors.append({'jd0': jd0, 'jd': jd0[0] + jd0[1], 'x': np.asarray(x, float), 'el': el,
                                 'traj': Trajectory(forces, x, mans, (-lim, lim)), 'lim': lim})
        jds = [a['jd'] for a in self.anchors]
        self.bounds = [0.5 * (p + q) for p, q in zip(jds[:-1], jds[1:])]
        self.epoch_jd = jds[0]
        self.vf = _PropagatedVF(self)
        first = self.anchors[0]['el']
        if center == 'earth':
            rp = min(a['el']['r_min_km'] for a in self.anchors)
            ra = max(a['el']['r_max_km'] for a in self.anchors)
            if rp < WGS84_A:
                raise ObserverError(tr('この状態ベクトルの軌道の近地点は地球内部にあります（再突入の軌道）'))
            self.r_min_km = rp * 0.98
            self.r_max_km = min(ra * 1.02 + 50.0, 1e10) if math.isfinite(ra) else 1e10
            a_km = first['a_km']
            self.v_max_km_s = (math.sqrt(GM_EARTH * max(2 / self.r_min_km - 1 / a_km, 1e-12)) * 1.03
                               if math.isfinite(ra) else 20.0)
            self.period_min = first['period_min']
        else:
            self.r_min_km, self.r_max_km, self.v_max_km_s = WGS84_A, 1e10, 50.0
            self.period_min = None

    # -- coverage ------------------------------------------------------------------
    def _span(self, k):
        """TT JDs covered by anchor k: up to halfway to its neighbours, within its limits and before a
        re-entry found by the propagation."""
        a = self.anchors[k]
        lo = a['jd'] - a['lim'] / DAY_S
        hi = a['jd'] + a['lim'] / DAY_S
        if k > 0:
            lo = max(lo, self.bounds[k - 1])
        if k < len(self.anchors) - 1:
            hi = min(hi, self.bounds[k])
        dec = a['traj'].decay_s
        if dec[1] is not None:
            hi = min(hi, a['jd'] + dec[1] / DAY_S)
        if dec[-1] is not None:
            lo = max(lo, a['jd'] + dec[-1] / DAY_S)
        return lo, hi

    def coverage(self):
        spans = [self._span(k) for k in range(len(self.anchors))]
        return min(s[0] for s in spans), max(s[1] for s in spans)

    def _extend(self, jd_a, jd_b):
        for k, a in enumerate(self.anchors):
            lo, hi = self._span(k)
            x, y = max(jd_a, lo), min(jd_b, hi)
            if y >= x:
                a['traj'].extend((x - a['jd']) * DAY_S, (y - a['jd']) * DAY_S)

    def states(self, t):
        """Geocentric GCRS positions and velocities (3, ...) at the Times t."""
        whole = np.asarray(t.whole, float)
        frac = np.asarray(t.tt_fraction, float)
        shape = np.shape(whole + frac)
        whole, frac = np.atleast_1d(whole).ravel(), np.atleast_1d(frac).ravel()
        if whole.size == 1 and frac.size > 1:
            whole = np.full(frac.size, whole[0])
        jd = whole + frac
        pos = np.empty((jd.size, 3))
        vel = np.empty((jd.size, 3))
        k = np.searchsorted(self.bounds, jd)
        lo, hi = self.coverage()
        if np.any(jd < lo - 1e-9) or np.any(jd > hi + 1e-9):
            raise ObserverError(tr('{label} から軌道を伝搬した期間（{period}）の外の時刻が要求されました',
                                   label=self.label, period=_period_text(self.ctx, lo, hi)))
        for j in np.unique(k):
            m = k == j
            a = self.anchors[j]
            s = ((whole[m] - a['jd0'][0]) + (frac[m] - a['jd0'][1])) * DAY_S
            a['traj'].extend(float(s.min()), float(s.max()))
            pos[m], vel[m] = a['traj'].state(s)
        if self.center != 'earth':
            from .propagate import EPH_TARGETS
            tt = self.ctx.ts.tt_jd(whole, frac)
            st = (self.ctx.eph[EPH_TARGETS[self.center]] - self.ctx.earth).at(tt)
            pos += st.position.km.T
            vel += st.velocity.km_per_s.T
        return pos.T.reshape((3,) + shape), vel.T.reshape((3,) + shape)

    def limit_period(self, ctx, jd_a, jd_b, margin):
        lo, hi = self.coverage()
        out = []
        if hi <= jd_a or lo >= jd_b:
            raise ObserverError(tr('{label} から軌道を伝搬できる期間（{period}）の外です', label=self.label,
                                   period=_period_text(self.ctx, lo, hi)))
        self._extend(max(jd_a - margin, lo), min(jd_b + margin, hi))
        from .timeutil import iso_from_jd
        lo2, hi2 = self.coverage()
        decayed = [a['jd'] + a['traj'].decay_s[1] / DAY_S for a in self.anchors if a['traj'].decay_s[1] is not None]
        if decayed:
            out.append(tr('数値積分ではこの衛星は {date} ごろに高度 100 km を下回る（再突入する）ため、それ以降は計算していません',
                          date=iso_from_jd(self.ctx, decayed[0])[:10]))
        elif lo2 > jd_a or hi2 < jd_b:
            days = min(a['lim'] for a in self.anchors) / DAY_S
            out.append(tr('{label} の元期から前後 {days:.0f} 日（{revs} 周）まで軌道を伝搬し、{period} に限定して計算しました',
                          label=self.label, days=days, revs=MAX_PROP_REVS, period=_period_text(self.ctx, lo2, hi2)))
        if lo2 >= jd_b or hi2 <= jd_a:
            raise ObserverError(out[0] if out else tr('{label} から軌道を伝搬できる期間（{period}）の外です',
                                                      label=self.label, period=_period_text(self.ctx, lo2, hi2)))
        self._survey()
        return max(jd_a, lo2), min(jd_b, hi2), list(self.notes) + out

    def _survey(self):
        """The distance and speed bounds from the propagated orbit (the far observers of other centres)."""
        if self.center == 'earth':
            return
        lo, hi = self.coverage()
        jd = np.linspace(lo, hi, 400)
        t = self.ctx.ts.tt_jd(jd)
        p, v = self.states(t)
        r, s = norm(p), norm(v)
        self.r_min_km = max(float(r.min()) * 0.98, WGS84_A)
        self.r_max_km = float(r.max()) * 1.02
        self.v_max_km_s = float(s.max()) * 1.05 + 0.01

    def clip_windows(self, windows):
        return _clip(windows, [self.coverage()])

    def interval_at(self, jd):
        return self.coverage()

    def prepare(self, ctx, jd_windows):
        lo, hi = self.coverage()
        for a, b in jd_windows:
            if a < lo - 1e-8 or b > hi + 1e-8:
                raise ObserverError(tr('{label} から軌道を伝搬できる期間（{period}）の外です', label=self.label,
                                       period=_period_text(self.ctx, lo, hi)))
            self._extend(a, b)

    def char_time_s(self):
        if self.period_min:
            return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))
        return max(60.0, min(self.r_min_km / self.v_max_km_s, 6 * 3600.0))

    def warnings_for(self, ctx, jd):
        age = min(abs(jd - a['jd']) for a in self.anchors)
        out = list(self.notes)
        if age > 30:
            out.append(tr('{label} の元期から {age:.0f} 日離れています。数値積分による軌道の伝搬は、大気抵抗・太陽輻射圧・'
                          '軌道制御などの誤差が日数とともに大きくなるため、結果は目安として扱ってください。',
                          label=self.label, age=age))
        elif age > 7:
            out.append(tr('{label} の元期から {age:.0f} 日離れています（軌道の伝搬の誤差が大きくなっている可能性があります）。',
                          label=self.label, age=age))
        return out

    def describe(self):
        a = self.anchors[0]
        el = a['el']
        d = {'kind': 'space', 'model': 'ccsds', 'name': self.name, 'epoch_jd_tt': a['jd']}
        d.update(self.info)
        if self.center == 'earth' and el['period_min']:
            d.update(period_min=el['period_min'], perigee_km=el['r_min_km'] - WGS84_A,
                     apogee_km=el['r_max_km'] - WGS84_A)
        d.update({k: el[k] for k in ('a_km', 'e', 'i_deg', 'raan_deg', 'argp_deg', 'm_deg')})
        d['state_gcrs'] = [float(v) for v in a['x']]
        d['n_states'] = len(self.anchors)
        return d


# ---------------------------------------------------------------------------
# CelesTrak TLE lookup
# ---------------------------------------------------------------------------
def fetch_tle_celestrak(norad_id):
    norad_id = int(norad_id)
    url = f'https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE'
    try:
        with _urlopen(url, 30) as resp:
            text = resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:     # 404 "No GP data found": unknown or decayed
        if exc.code != 404:
            raise NetworkError(tr('{service} に接続できませんでした: {exc}', service='CelesTrak', exc=exc))
        text = ''
    except _NET_ERRORS as exc:
        raise NetworkError(tr('{service} に接続できませんでした: {exc}', service='CelesTrak', exc=exc))
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or 'No GP data' in text:
        raise ObserverError(tr('CelesTrak に NORAD {norad} の軌道要素が見つかりません', norad=norad_id))
    if lines[0].startswith('1 ') and len(lines) >= 2:
        return f'NORAD {norad_id}', lines[0], lines[1]
    return lines[0].strip(), lines[1], lines[2]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_observer(ctx, spec, parse_time=None):
    kind = spec.get('type')
    name = spec.get('name', '') or ''
    if kind == 'ground':
        return GroundObserver(float(spec['lat']), float(spec['lon']),
                              float(spec.get('elevation_m', 0) or 0), name)
    if kind in ('global', 'geocenter'):
        return GeocenterObserver()
    if kind == 'geo':
        lon = float(spec['lon'])
        return FixedITRSObserver(0.0, lon, 35_786.0, name or tr('静止衛星 {lon:.1f}°', lon=lon))
    if kind == 'fixed':
        return FixedITRSObserver(float(spec.get('lat', 0)), float(spec['lon']),
                                 float(spec['height_km']), name)
    if kind == 'tle':
        line2 = spec['line2']
        if spec.get('m_deg') is not None:      # phase sweep: move the satellite along its orbit
            line2 = tle_with_mean_anomaly(line2, spec['m_deg'])
        return TLEObserver(ctx, spec['line1'], line2, name)
    if kind == 'celestrak':
        nm, l1, l2 = fetch_tle_celestrak(spec['norad'])
        return TLEObserver(ctx, l1, l2, name or nm)
    if kind == 'kepler':
        epoch = parse_time(spec['epoch']) if parse_time else float(spec['epoch_jd_tt'])
        if spec.get('a_km'):
            a = float(spec['a_km'])
        else:
            hp = float(spec.get('perigee_alt_km', spec.get('alt_km', 500)))
            ha = float(spec.get('apogee_alt_km', hp))
            hp, ha = min(hp, ha), max(hp, ha)      # given the other way round
            a = WGS84_A + 0.5 * (hp + ha)
            spec = dict(spec, e=(ha - hp) / (2 * a))
        e = float(spec.get('e', 0))
        sso = bool(spec.get('sso'))
        if sso:
            if not 0 <= e < 1:
                raise ObserverError(tr('離心率は 0 以上 1 未満で指定してください'))
            i_deg = sun_synchronous_inclination(a, e)
        else:
            i_deg = float(spec['i_deg'])
        ltan = spec.get('ltan_h')
        if ltan is not None and ltan != '':
            raan = raan_from_ltan(ctx, epoch, float(ltan) % 24.0)
        else:
            raan = float(spec.get('raan_deg', 0))
        obs = KeplerObserver(ctx, epoch, a, e, i_deg, raan, float(spec.get('argp_deg', 0)),
                             float(spec.get('m_deg', 0)), bool(spec.get('j2', True)), name)
        obs.params.update(sso=sso, ltan_h=ltan_from_raan(ctx, epoch, raan))
        return obs
    if kind == 'horizons':
        return HorizonsObserver(ctx, spec['command'], int(spec.get('step_min', 5) or 5), name)
    if kind == 'sscweb':
        return SSCWebObserver(ctx, spec['id'], name)
    if kind == 'ccsds':
        from .ccsds import build_observer as ccsds_observer
        return ccsds_observer(ctx, spec)
    raise ObserverError(tr('不明な観測者タイプです: {kind}', kind=kind))

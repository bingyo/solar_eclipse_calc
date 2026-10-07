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
import json
import math
import time
import urllib.parse

import numpy as np
from skyfield.api import EarthSatellite, wgs84
from skyfield.vectorlib import VectorFunction

from .constants import (AU_KM, DAY_S, EARTH_OMEGA, GM_EARTH, J2_EARTH, WGS84_A,
                        WGS84_B)
from .context import CACHE_DIR
from .geometry import norm
from .net import urlopen as _urlopen

AU_PER_DAY_PER_KM_S = DAY_S / AU_KM


class ObserverError(ValueError):
    pass


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

    def prepare(self, ctx, jd_windows):
        """Called before a scan with the TT JD windows that will be evaluated."""

    def char_time_s(self):
        """Time scale on which the observer's parallax changes appreciably."""
        return max(self.r_min_km, 1.0) / max(self.v_max_km_s, 1e-6)

    def warnings_for(self, ctx, jd):
        return []

    def describe(self):
        return {'kind': self.kind, 'name': self.name}


class GeocenterObserver(Observer):
    kind = 'geocenter'
    name = '地心'
    r_min_km = 0.0
    r_max_km = 0.0
    v_max_km_s = 0.0

    def __init__(self):
        self.vf = _ZeroVector()

    def char_time_s(self):
        return 1e9


class GroundObserver(Observer):
    kind = 'ground'

    def __init__(self, lat, lon, elevation_m=0.0, name=''):
        if not (-90 <= lat <= 90):
            raise ObserverError('緯度は -90〜90 度で指定してください')
        if not (-180 <= lon <= 360):
            raise ObserverError('経度は -180〜180 度で指定してください')
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
        self.lat, self.lon, self.height_km = float(lat), float(lon), float(height_km)
        self.name = name or f'地球固定点 {lon:.2f}°'
        self.vf = wgs84.latlon(self.lat, self.lon, elevation_m=self.height_km * 1000.0)
        r = WGS84_A + self.height_km
        self.r_min_km = self.r_max_km = r
        self.v_max_km_s = EARTH_OMEGA * r + 1e-3

    def char_time_s(self):
        return 1.0 / EARTH_OMEGA

    def describe(self):
        return {'kind': 'space', 'model': 'fixed', 'name': self.name, 'lat': self.lat,
                'lon': self.lon, 'height_km': self.height_km}


class TLEObserver(Observer):
    def __init__(self, ctx, line1, line2, name=''):
        line1, line2 = line1.strip(), line2.strip()
        if not (line1.startswith('1 ') and line2.startswith('2 ')):
            raise ObserverError('TLE の 1 行目は "1 "、2 行目は "2 " で始まる必要があります')
        try:
            self.vf = EarthSatellite(line1, line2, name or None, ctx.ts)
        except Exception as exc:  # pragma: no cover - sgp4 raises various errors
            raise ObserverError(f'TLE を解釈できません: {exc}')
        m = self.vf.model
        self.line1, self.line2 = line1, line2
        self.name = name or f'NORAD {m.satnum}'
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
            raise ObserverError('この TLE の近地点は地球内部にあります（再突入済みの可能性）')

    def char_time_s(self):
        return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))

    def warnings_for(self, ctx, jd):
        age = abs(jd - self.epoch_jd)
        if age > 30:
            return [f'TLE 元期から {age:.0f} 日離れています。SGP4 の位置誤差は数百 km 以上になり得るため、'
                    '結果は目安として扱ってください。']
        if age > 7:
            return [f'TLE 元期から {age:.0f} 日離れています（位置誤差は数 km〜数十 km 程度）。']
        return []

    def describe(self):
        m = self.vf.model
        # the TLE's own mean elements (TEME of epoch; for checking what was read)
        return {'kind': 'space', 'model': 'tle', 'name': self.name, 'line1': self.line1,
                'line2': self.line2, 'norad': m.satnum, 'period_min': self.period_min,
                'perigee_km': self.r_min_km - WGS84_A,
                'apogee_km': self.a_km * (1 + self.e) - WGS84_A,
                'a_km': self.a_km, 'e': self.e, 'i_deg': math.degrees(m.inclo),
                'raan_deg': math.degrees(m.nodeo), 'argp_deg': math.degrees(m.argpo),
                'm_deg': math.degrees(m.mo), 'mean_motion_rev_per_day': m.no_kozai * 1440 / (2 * math.pi),
                'bstar': m.bstar, 'epoch_jd_tt': self.epoch_jd}


def _tle_checksum(line):
    return sum(int(c) if c.isdigit() else c == '-' for c in line[:68]) % 10


def tle_with_mean_anomaly(line2, m_deg):
    """Line 2 of a TLE with the mean anomaly at epoch (columns 44-51) replaced
    and the checksum redone: the same orbit with the satellite elsewhere on it."""
    line2 = line2.rstrip()
    if len(line2) < 68 or not line2.startswith('2 '):
        raise ObserverError('TLE の 2 行目が短すぎます（69 文字の形式で指定してください）')
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
            raise ObserverError('この軌道の大きさでは太陽同期軌道になりません（高度が高すぎます）')
        i = math.acos(c)
    return math.degrees(i)


class KeplerObserver(Observer):
    def __init__(self, ctx, epoch_jd_tt, a_km, e, i_deg, raan_deg, argp_deg, m_deg,
                 j2=True, name=''):
        if a_km * (1 - e) <= WGS84_A:
            raise ObserverError('近地点高度が地表より低くなっています')
        if not (0 <= e < 1):
            raise ObserverError('離心率は 0 以上 1 未満で指定してください')
        self.name = name or '軌道要素で指定した衛星'
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
    url = HORIZONS_URL + '?' + urllib.parse.urlencode(params)
    with _urlopen(url, 90) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    text = payload.get('result', '')
    if '$$SOE' not in text:
        msg = payload.get('error') or text.strip().splitlines()[-12:]
        if isinstance(msg, list):
            msg = '\n'.join(msg)
        raise ObserverError(f'JPL Horizons からデータを取得できませんでした:\n{msg}')
    body = text.split('$$SOE', 1)[1].split('$$EOE', 1)[0]
    rows = []
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 8:
            continue
        rows.append([float(parts[0])] + [float(v) for v in parts[2:8]])
    if not rows:
        raise ObserverError('JPL Horizons の応答にデータ行がありません')
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
        return self.jd.size > 1 and self.jd[0] <= a and self.jd[-1] >= b

    def _at(self, t):
        x = np.asarray(t.whole + t.tdb_fraction, float)
        shape = x.shape
        x = np.atleast_1d(x)
        jd = self.jd
        if jd.size < 2 or np.any(x < jd[0] - 1e-9) or np.any(x > jd[-1] + 1e-9):
            raise ObserverError('JPL Horizons の取得範囲外の時刻が要求されました')
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
    MAX_POINTS = 60_000

    def __init__(self, ctx, command, step_min=5, name=''):
        command = str(command).strip()
        if not command:
            raise ObserverError('Horizons の天体 ID を指定してください（例: -170 = JWST）')
        self.command = command
        self.step_min = max(1, int(step_min))
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
                f'Horizons から取得するデータ点が多すぎます（{int(n)} 点）。期間を短くするか'
                '刻み幅を大きくしてください。')
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
        """Coarse fetch to learn the distance range (used for pre-filtering)."""
        span = jd_b - jd_a
        step = max(self.step_min, int(math.ceil(span * 1440 / 3000)))
        jd, pos, vel = self._fetch(jd_a - 0.01, jd_b + 0.01, step)
        self.coverage = (float(jd[0]), float(jd[-1]))
        r = norm(pos)
        v = norm(vel)
        self.r_min_km = float(r.min()) * 0.98
        self.r_max_km = float(r.max()) * 1.02
        self.v_max_km_s = float(v.max()) * 1.05 + 0.01
        if self.r_min_km < WGS84_A:
            self.r_min_km = WGS84_A
        return jd, pos, vel

    def prepare(self, ctx, jd_windows):
        # Pad by one output step so that the samples bracket the window.
        pad = self.step_min / 1440.0 + 0.01
        for a, b in jd_windows:
            if not self.vf.covers(a - 0.002, b + 0.002):
                jd, pos, vel = self._fetch(a - pad, b + pad, self.step_min)
                self.vf.add(jd, pos, vel)
        for a, b in jd_windows:
            if not self.vf.covers(a - 1e-4, b + 1e-4):
                raise ObserverError('JPL Horizons から必要な期間の軌道データを取得できませんでした'
                                    '（探査機の軌道データが提供されている期間外の可能性があります）')

    def char_time_s(self):
        return max(60.0, min(self.r_min_km / self.v_max_km_s, 6 * 3600.0))

    def describe(self):
        return {'kind': 'space', 'model': 'horizons', 'name': self.name,
                'command': self.command, 'step_min': self.step_min,
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
    with _urlopen(url, 120, {'Accept': 'application/json'}) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    # SSCWeb's JSON wraps every object as ["java.class.Name", {...}].
    result = payload[1]['Result'][1]
    if result.get('StatusCode') != 'SUCCESS':
        text = result.get('StatusText', ['', []])[1]
        raise ObserverError(f'NASA SSCWeb からデータを取得できませんでした: {" ".join(text) or result}')
    data = result.get('Data', ['', []])[1]
    if not data:
        raise ObserverError(f'NASA SSCWeb に {sat} の {utc_a:%Y-%m-%d %H:%M}〜{utc_b:%Y-%m-%d %H:%M} '
                            'の軌道データがありません')
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
            raise ObserverError('NASA SSCWeb の取得範囲外の時刻が要求されました')
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
                raise ObserverError(f'NASA SSCWeb の衛星一覧を取得できませんでした: {exc}')
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
            raise ObserverError('SSCWeb の衛星 ID を指定してください（例: hinode）')
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
            raise ObserverError(f'NASA SSCWeb に衛星 ID「{self.sat_id}」はありません')
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
            raise ObserverError(f'NASA SSCWeb から取得する軌道データが多すぎます（{days:.0f} 日分）。'
                                '期間を短くしてください')
        for a, b in todo:
            jd, pos = self._fetch(a - pad, b + pad)
            self.vf.add(jd, pos)
        for a, b in jd_windows:
            if not self.vf.covers(a - 1e-4, b + 1e-4):
                raise ObserverError('NASA SSCWeb から必要な期間の軌道データを取得できませんでした'
                                    '（欠損または提供期間外の可能性があります）')

    def char_time_s(self):
        return min(self.r_min_km / self.v_max_km_s, self.period_min * 60 / (2 * math.pi))

    def describe(self):
        d = {'kind': 'space', 'model': 'sscweb', 'name': self.name, 'id': self.sat_id}
        if self.surveyed:
            d.update(period_min=self.period_min, perigee_km=self.r_range[0] - WGS84_A,
                     apogee_km=self.r_range[1] - WGS84_A)
        return d


# ---------------------------------------------------------------------------
# CelesTrak TLE lookup
# ---------------------------------------------------------------------------
def fetch_tle_celestrak(norad_id):
    norad_id = int(norad_id)
    url = f'https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE'
    with _urlopen(url, 30) as resp:
        text = resp.read().decode('utf-8', 'replace')
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or 'No GP data' in text:
        raise ObserverError(f'CelesTrak に NORAD {norad_id} の軌道要素が見つかりません')
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
        return FixedITRSObserver(0.0, lon, 35_786.0, name or f'静止衛星 {lon:.1f}°')
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
            a = WGS84_A + 0.5 * (hp + ha)
            spec = dict(spec, e=(ha - hp) / (2 * a))
        e = float(spec.get('e', 0))
        sso = bool(spec.get('sso'))
        if sso:
            if not 0 <= e < 1:
                raise ObserverError('離心率は 0 以上 1 未満で指定してください')
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
    raise ObserverError(f'不明な観測者タイプです: {kind}')

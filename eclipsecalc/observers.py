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
"""
import json
import math
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
        return {'kind': 'space', 'model': 'tle', 'name': self.name, 'line1': self.line1,
                'line2': self.line2, 'period_min': self.period_min,
                'perigee_km': self.r_min_km - WGS84_A,
                'apogee_km': self.a_km * (1 + self.e) - WGS84_A,
                'epoch_jd_tt': self.epoch_jd}


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
        return TLEObserver(ctx, spec['line1'], spec['line2'], name)
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
        return KeplerObserver(ctx, epoch, a, float(spec.get('e', 0)), float(spec['i_deg']),
                              float(spec.get('raan_deg', 0)), float(spec.get('argp_deg', 0)),
                              float(spec.get('m_deg', 0)), bool(spec.get('j2', True)), name)
    if kind == 'horizons':
        return HorizonsObserver(ctx, spec['command'], int(spec.get('step_min', 5) or 5), name)
    raise ObserverError(f'不明な観測者タイプです: {kind}')

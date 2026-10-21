"""Orbit determination from CCSDS Tracking Data Messages (TDM, CCSDS 503.0-B-2).

A TDM carries the measurements of tracking stations (angles, range, Doppler), not an orbit, and not
the positions of the stations either (they are agreed separately, e.g. in an ICD): they are given
with the TDM.  The orbit at the middle of the tracking arc is estimated by weighted batch least
squares with the numerical propagator (propagate.py: EGM96 8x8, the Sun and the Moon), from a first
guess that is an orbit message or a TLE of the same spacecraft given with the TDM, or else from the
measurements: the positions given by angles and ranges taken together, or Gauss's method on three
angle measurements.  The weights follow the scatter of each type of measurement, and measurements
farther than 4 sigma are left out.

Measurement models (light time included; aberration, refraction and media delays are not):
  * ANGLE_1 / ANGLE_2 with ANGLE_TYPE AZEL (azimuth from north toward east, elevation; the
    receiving station of the PATH) or RADEC (topocentric right ascension and declination in
    REFERENCE_FRAME, EME2000 if not given);
  * RANGE (RANGE_UNITS km or s) for one-way and two- / three-way paths: the distance of a one-way
    path, half the length of a two-leg path (one-way equivalent), with RANGE_MODULUS, TIMETAG_REF,
    the TRANSMIT_DELAY / RECEIVE_DELAY of the participants and the CORRECTION_RANGE that is not
    applied yet; a range that is twice what the angles give (the full round trip) is halved;
  * DOPPLER_INSTANTANEOUS and DOPPLER_INTEGRATED (range rate of the same paths, km/s; the latter
    over INTEGRATION_INTERVAL as set by INTEGRATION_REF).
Frequencies, phase counts, differenced (SINGLE_DIFF) and media / weather data are not used.
"""
import math
import re

import numpy as np
from skyfield.api import wgs84
from skyfield.framelib import itrs

from .constants import C_KM_S, DAY_S, GM_EARTH, WGS84_A
from .i18n import tr
from .observers import ObserverError, PropagatedObserver
from .propagate import Forces, PropagationError, _Cheb, elements as osc_elements, kepler

DEG = math.pi / 180.0
SIGMA0 = {'azel': 0.01 * DEG, 'radec': 2.0 / 206264.8, 'range': 0.01, 'doppler': 1e-5}     # rad, km, km/s
SIGMA_FLOOR = {'azel': 0.5 / 206264.8, 'radec': 0.05 / 206264.8, 'range': 0.0005, 'doppler': 1e-7}
EDIT_SIGMA = 4.0
MAX_OBS = 20_000


class ODError(ObserverError):
    pass


def _err(text, /, **kw):
    return ODError(tr(text, **kw))


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------
def parse_stations(value):
    """Station positions from a list of {'name', 'lat', 'lon', 'elevation_m'} or text lines
    'NAME LAT LON [ELEVATION_M]' (blanks, tabs or commas; # comments) -> {key: (name, lat, lon, m)}."""
    from .ccsds import text_value
    rows = []
    if isinstance(value, str):
        for line in value.splitlines():
            s = line.split('#', 1)[0].replace(',', ' ').replace('\t', ' ').strip()
            if not s:
                continue
            parts = s.split()
            nums = []
            while parts and len(nums) < 3 and re.fullmatch(r'[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?', parts[-1]):
                nums.insert(0, float(parts.pop()))
            if len(nums) == 3 and len(parts) == 0:          # "NAME" was numeric?  keep the first as the name
                parts, nums = [str(nums[0])], nums[1:]
            if len(nums) < 2 or not parts:
                raise _err('観測局の行「{line}」は「名前 緯度 経度 標高(m)」の形で書いてください', line=line.strip()[:60])
            if len(nums) == 2:
                nums.append(0.0)
            rows.append((' '.join(parts), *nums))
    elif isinstance(value, (list, tuple)):
        for v in value:
            if not isinstance(v, dict) or v.get('lat') is None or v.get('lon') is None or not v.get('name'):
                raise _err('観測局は {"name", "lat", "lon", "elevation_m"} の形で指定してください')
            try:
                rows.append((str(v['name']), float(v['lat']), float(v['lon']), float(v.get('elevation_m') or 0.0)))
            except (TypeError, ValueError):
                raise _err('観測局 {name} の緯度・経度・標高を数値として読めません', name=v.get('name'))
    elif value:
        raise _err('観測局は {"name", "lat", "lon", "elevation_m"} の形で指定してください')
    out = {}
    for name, lat, lon, h in rows:
        if not (-90 <= lat <= 90 and -360 <= lon <= 360 and -12_000 <= h <= 100_000):
            raise _err('観測局 {name} の緯度・経度・標高の値が範囲外です', name=name)
        out[text_value(name)] = (name, lat, lon, h)
    return out


# ---------------------------------------------------------------------------
# Batch propagation (the nominal state and its perturbations together)
# ---------------------------------------------------------------------------
class BatchTrajectory:
    """k trajectories from the states X0 (k, 6) at s = 0, propagated together over [s_lo, s_hi]
    (seconds) with the segments chosen for the first, kept as Chebyshev coefficients."""

    NODES = 24
    FRAC = 0.2

    def __init__(self, forces, X0, s_lo, s_hi):
        self.forces = forces
        self.cheb = _Cheb(self.NODES)
        X0 = np.atleast_2d(np.asarray(X0, float))
        self.k = X0.shape[0]
        segs = []
        for direction, target in ((1, s_hi), (-1, s_lo)):
            t, X = 0.0, X0.copy()
            while (target - t) * direction > 1e-6:
                rn = float(np.linalg.norm(X[0, :3]))
                h = self.FRAC * 2 * math.pi * math.sqrt(rn ** 3 / forces.gm)
                guess = kepler(X[0], np.linspace(0, direction * h, 9), forces.gm)
                rmin = float(np.min(np.linalg.norm(guess[:, :3], axis=1)))
                h = min(h, self.FRAC * 2 * math.pi * math.sqrt(rmin ** 3 / forces.gm), abs(target - t), 2 * DAY_S)
                for _ in range(14):
                    res = self._segment(t, X, direction * h)
                    if res is not None:
                        break
                    h *= 0.5
                else:
                    raise _err('軌道決定の数値積分が収束しませんでした')
                R, V = res
                if float(np.min(np.linalg.norm(R, axis=2))) < WGS84_A + 80.0:
                    raise _err('軌道決定の途中で軌道が地表近くまで下がりました（観測と初期軌道が合っていない可能性があります）')
                segs.append((t, direction * h, np.einsum('ij,jkc->ikc', self.cheb.C, R),
                             np.einsum('ij,jkc->ikc', self.cheb.C, V)))
                t += direction * h
                X = np.concatenate([R[-1], V[-1]], axis=1)
        segs.sort(key=lambda g: min(g[0], g[0] + g[1]))
        self.segs = segs
        self.starts = np.array([min(g[0], g[0] + g[1]) for g in segs])
        self.lo = min([s_lo] + [g[0] + g[1] for g in segs if g[1] < 0])
        self.hi = max([s_hi] + [g[0] + g[1] for g in segs if g[1] > 0])

    def _segment(self, t0, X0, h):
        cb, f = self.cheb, self.forces
        w2 = 0.5 * h
        ts = t0 + w2 * (cb.tau + 1)
        G = np.stack([kepler(x, ts - t0, f.gm) for x in X0], axis=1)        # (nodes, k, 6)
        R, V = G[:, :, :3], G[:, :, 3:]
        rmin = float(np.min(np.linalg.norm(R[:, 0], axis=1)))
        deg = 8 if rmin < 3 * WGS84_A else 4 if rmin < 12 * WGS84_A else 2
        f.ensure(min(ts[0], ts[-1]), max(ts[0], ts[-1]))
        sn = np.repeat(ts, self.k)
        prev = math.inf
        for it in range(40):
            A = f.accel(sn, R.reshape(-1, 3), V.reshape(-1, 3), deg).reshape(R.shape)
            Vn = X0[None, :, 3:] + np.einsum('ij,jkc->ikc', cb.M, w2 * A)
            Rn = X0[None, :, :3] + np.einsum('ij,jkc->ikc', cb.M, w2 * Vn)
            d = max(float(np.max(np.abs(Rn - R))), float(np.max(np.abs(Vn - V))) * abs(w2))
            R, V = Rn, Vn
            if d < 1e-13 * rmin + 1e-10:
                return R, V
            if it > 8 and d > 0.8 * prev:
                return None
            prev = d
        return None

    def state(self, s):
        """Positions and velocities (k, n, 3) at times s (n,)."""
        s = np.atleast_1d(np.asarray(s, float))
        j = np.clip(np.searchsorted(self.starts, s, side='right') - 1, 0, len(self.segs) - 1)
        pos = np.empty((self.k, s.size, 3))
        vel = np.empty((self.k, s.size, 3))
        for g in np.unique(j):
            m = j == g
            t0, h, cr, cv = self.segs[g]
            tau = np.clip(2 * (s[m] - t0) / h - 1, -1, 1)
            T = np.cos(np.outer(np.arccos(tau), np.arange(self.cheb.n + 1)))
            pos[:, m] = np.einsum('nj,jkc->knc', T, cr)
            vel[:, m] = np.einsum('nj,jkc->knc', T, cv)
        return pos, vel


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------
def _float(meta, key, default=0.0):
    v = meta.get(key)
    if v in (None, ''):
        return default
    try:
        x = float(v)
    except ValueError:
        raise _err('TDM の {key} の値「{value}」を数値として読めません', key=key, value=str(v)[:30])
    return x


class Measurements:
    """The usable measurements of the TDM segments, as arrays per type, and what was not used."""

    def __init__(self):
        self.rows = {'azel': [], 'radec': [], 'range': [], 'doppler': []}
        self.unused = {}
        self.notes = []
        self.stations_used = set()

    def add_unused(self, key, n=1):
        self.unused[key] = self.unused.get(key, 0) + n


def collect(ctx, tdms, stations, target):
    """Measurements of the spacecraft ``target`` (a participant name) from the TDMs."""
    from .ccsds import epochs, frame_key, text_value
    ms = Measurements()
    tkey = text_value(target)
    for msg in tdms:
        for seg in msg.segments:
            meta = seg['meta']
            parts = {int(k.rsplit('_', 1)[1]): v.strip() for k, v in meta.items()
                     if re.fullmatch(r'PARTICIPANT_\d', k) and v.strip()}
            sc = [n for n, v in parts.items() if text_value(v) == tkey]
            recs = seg['records']
            if not sc:
                continue
            sc = sc[0]
            mode = text_value(meta.get('MODE', 'SEQUENTIAL'))
            if mode == 'SINGLE DIFF':
                ms.add_unused('SINGLE_DIFF', len(recs))
                continue
            path = [int(x) for x in re.findall(r'\d', meta.get('PATH', ''))]
            if not path:
                path = [sc] + [n for n in parts if n != sc][:1]
            if any(p not in parts for p in path):
                raise _err('TDM の PATH {path} に PARTICIPANT_n のない番号があります', path=meta.get('PATH'))
            sta = {}
            for p in path:
                if p == sc:
                    continue
                key = text_value(parts[p])
                if key not in stations:
                    raise _err('観測局 {name} の位置（緯度・経度・標高）を指定してください（TDM には観測局の位置が書かれていません）',
                               name=parts[p])
                sta[p] = stations[key]
            system = meta.get('TIME_SYSTEM', 'UTC')
            corr = text_value(meta.get('CORRECTIONS_APPLIED', 'YES')) == 'NO'
            delay = sum(_float(meta, f'TRANSMIT_DELAY_{p}') for p in path[:-1]) + \
                sum(_float(meta, f'RECEIVE_DELAY_{p}') for p in path[1:])
            legs = len(path) - 1
            # a path the models cover: station(s) and the spacecraft alternating, one or two legs
            simple = (legs == 1 and sc in path) or (legs == 2 and path[1] == sc and path[0] != sc and path[2] != sc)
            if text_value(meta.get('DATA_QUALITY', '')) == 'DEGRADED':
                ms.notes.append(tr('TDM に DATA_QUALITY = DEGRADED（品質に問題のあるデータ）のセグメントがあります'))
            rx = path[-1]
            tx = path[0]
            angle_type = text_value(meta.get('ANGLE_TYPE', ''))
            ref = meta.get('REFERENCE_FRAME', '')
            r_units = text_value(meta.get('RANGE_UNITS', 'KM'))
            modulus = _float(meta, 'RANGE_MODULUS')
            transmit_tag = text_value(meta.get('TIMETAG_REF', 'RECEIVE')) == 'TRANSMIT'
            interval = _float(meta, 'INTEGRATION_INTERVAL')
            iref = text_value(meta.get('INTEGRATION_REF', 'MIDDLE'))
            by_key = {}
            for _, key, ep, val in recs:
                by_key.setdefault(key, []).append((ep, val))
            for key, items in by_key.items():
                if key in ('ANGLE_1', 'ANGLE_2'):
                    continue
                if key not in ('RANGE', 'DOPPLER_INSTANTANEOUS', 'DOPPLER_INTEGRATED'):
                    ms.add_unused(key, len(items))
                    continue
                if not simple:
                    ms.add_unused(f'{key} (PATH={meta.get("PATH", "")})', len(items))
                    continue
                if key == 'RANGE' and r_units == 'RU':
                    ms.add_unused('RANGE (RANGE_UNITS = RU)', len(items))
                    continue
                t = epochs(ctx, [e for e, _ in items], system)
                vals = np.array([float(v) for _, v in items])
                if key == 'RANGE':
                    scale = C_KM_S if r_units == 'S' else 1.0
                    vals = vals * scale
                    mod = modulus * scale
                    if corr:
                        vals = vals + _float(meta, 'CORRECTION_RANGE') * scale
                    for k in range(vals.size):
                        ms.rows['range'].append((t.whole[k], t.tt_fraction[k], vals[k], tx, rx, legs, transmit_tag,
                                                 mod, delay, sta, sc))
                else:
                    if corr:
                        vals = vals + _float(meta, 'CORRECTION_DOPPLER')
                    integ = interval if key == 'DOPPLER_INTEGRATED' else 0.0
                    off = {'START': 0.0, 'MIDDLE': -0.5, 'END': -1.0}.get(iref, -0.5) * integ
                    for k in range(vals.size):
                        ms.rows['doppler'].append((t.whole[k], t.tt_fraction[k], vals[k], tx, rx, legs, transmit_tag,
                                                   integ, off, sta, sc))
                ms.stations_used.update(v[0] for v in sta.values())
            a1 = dict(by_key.get('ANGLE_1', []))
            a2 = dict(by_key.get('ANGLE_2', []))
            common = [e for e in a1 if e in a2]
            if common:
                if len(path) < 2 or path[-2] != sc or rx == sc:
                    ms.add_unused(f'ANGLE (PATH={meta.get("PATH", "")})', len(common))
                elif angle_type not in ('AZEL', 'RADEC'):
                    ms.add_unused(f'ANGLE (ANGLE_TYPE={meta.get("ANGLE_TYPE", "")})', len(common))
                else:
                    t = epochs(ctx, common, system)
                    v1 = np.array([float(a1[e]) for e in common])
                    v2 = np.array([float(a2[e]) for e in common])
                    if corr:
                        v1 = v1 + _float(meta, 'CORRECTION_ANGLE_1')
                        v2 = v2 + _float(meta, 'CORRECTION_ANGLE_2')
                    kind = 'azel' if angle_type == 'AZEL' else 'radec'
                    frame = frame_key(ref or 'EME2000') if kind == 'radec' else None
                    if kind == 'radec' and not ref:
                        ms.notes.append(tr('TDM の RADEC の角度に REFERENCE_FRAME がないため EME2000 として扱いました'))
                    for k in range(len(common)):
                        ms.rows[kind].append((t.whole[k], t.tt_fraction[k], v1[k] * DEG, v2[k] * DEG, rx, sta[rx], frame))
                    ms.stations_used.add(sta[rx][0])
                if len(a1) + len(a2) > 2 * len(common):
                    ms.add_unused('ANGLE_1/ANGLE_2 (unpaired)', len(a1) + len(a2) - 2 * len(common))
    n = sum(len(v) for v in ms.rows.values())
    if n > MAX_OBS:
        raise _err('TDM の観測が多すぎます（{n} 件。{max} 件まで）', n=n, max=MAX_OBS)
    return ms


class _Model:
    """Arrays of the measurements of one type, with the states of the stations at their time tags."""

    def __init__(self, ctx, kind, rows, ref):
        self.ctx, self.kind, self.n = ctx, kind, len(rows)
        whole = np.array([r[0] for r in rows], float)
        frac = np.array([r[1] for r in rows], float)
        self.s = ((whole - ref[0]) + (frac - ref[1])) * DAY_S          # time tags, seconds from the reference
        self.edit = np.ones(self.n, bool)
        self.sigma = SIGMA0[kind]
        self.factor = 1.0
        if kind in ('azel', 'radec'):
            self.obs = np.array([[r[2], r[3]] for r in rows])
            stas = [r[5] for r in rows]
            self.rx_st = stas
            self.rx_pos, self.rx_vel = self._stations(stas, whole, frac)
            self.frame = rows[0][6]
            if kind == 'azel':
                self.enu = self._enu(stas, whole, frac)
            else:
                from .ccsds import frame_rotation
                R = frame_rotation(ctx, self.frame, ctx.ts.tt_jd(whole, frac))
                self.rot = None if R is None else np.moveaxis(R, -1, 0)
            return
        self.obs = np.array([r[2] for r in rows])
        self.legs = np.array([r[5] for r in rows])
        self.transmit = np.array([bool(r[6]) for r in rows])
        self.sc_tx = np.array([r[3] == r[10] for r in rows])        # one-way: the spacecraft sends
        self.sc_rx = np.array([r[4] == r[10] for r in rows])        # one-way: the spacecraft receives
        self.rx_st = [r[9].get(r[4]) for r in rows]
        self.tx_st = [r[9].get(r[3]) for r in rows]
        self.rx_pos, self.rx_vel = self._stations(self.rx_st, whole, frac)
        self.tx_pos, self.tx_vel = self._stations(self.tx_st, whole, frac)
        if kind == 'range':
            self.modulus = np.array([r[7] for r in rows])
            self.delay = np.array([r[8] for r in rows])
            return
        self.modulus = np.zeros(self.n)
        self.delay = np.zeros(self.n)
        self.interval = np.array([r[7] for r in rows])
        self.integ = self.interval > 0
        if np.any(self.integ):
            # a mean range rate: the ranges at the start and at the end of the integration
            sub = [r for r, i in zip(rows, self.integ) if i]
            start = [(r[0], r[1] + r[8] / DAY_S, 0.0, r[3], r[4], r[5], r[6], 0.0, 0.0, r[9], r[10]) for r in sub]
            end = [(r[0], r[1] + (r[8] + r[7]) / DAY_S, 0.0, r[3], r[4], r[5], r[6], 0.0, 0.0, r[9], r[10]) for r in sub]
            self.start = _Model(ctx, 'range', start, ref)
            self.end = _Model(ctx, 'range', end, ref)

    def _stations(self, stas, whole, frac):
        pos = np.zeros((self.n, 3))
        vel = np.zeros((self.n, 3))
        groups = {}
        for k, st in enumerate(stas):
            if st is not None:
                groups.setdefault(st, []).append(k)
        for st, idx in groups.items():
            idx = np.array(idx)
            g = wgs84.latlon(st[1], st[2], elevation_m=st[3]).at(self.ctx.ts.tt_jd(whole[idx], frac[idx]))
            pos[idx] = g.position.km.T
            vel[idx] = g.velocity.km_per_s.T
        return pos, vel

    def _enu(self, stas, whole, frac):
        """(n, 3, 3) matrices GCRS -> (east, north, up) of the stations."""
        R = np.moveaxis(itrs.rotation_at(self.ctx.ts.tt_jd(whole, frac)), -1, 0)
        lat = np.radians([s[1] for s in stas])
        lon = np.radians([s[2] for s in stas])
        sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
        E = np.stack([-so, co, np.zeros_like(so)], axis=1)
        N = np.stack([-sl * co, -sl * so, cl], axis=1)
        U = np.stack([cl * co, cl * so, sl], axis=1)
        return np.einsum('nij,njk->nik', np.stack([E, N, U], axis=1), R)

    def times(self, traj):
        """The times (s) at which the spacecraft sends or reflects the signal (light time solved
        with the first trajectory of ``traj``)."""
        s = self.s
        tb = s.copy()
        for _ in range(3):
            p = traj.state(tb)[0][0]
            if self.kind in ('azel', 'radec'):
                tb = s - np.linalg.norm(p - self.rx_pos, axis=1) / C_KM_S
                continue
            lt_rx = np.linalg.norm(p - self.rx_pos, axis=1) / C_KM_S
            lt_tx = np.linalg.norm(p - self.tx_pos, axis=1) / C_KM_S
            tb = np.where(self.transmit, np.where(self.sc_tx, s, s + lt_tx), np.where(self.sc_rx, s, s - lt_rx))
        return tb

    def ranges(self, traj, tb):
        """One-way-equivalent ranges (k, n): the length of a one-way path, half that of a two-leg path,
        with the stations where they are when the signal leaves or arrives."""
        p = traj.state(tb)[0]
        s = self.s
        # the station whose time is not the tag moves (linearly over the light time) meanwhile
        tt = tb - np.linalg.norm(p[0] - self.tx_pos, axis=1) / C_KM_S
        tr_ = tb + np.linalg.norm(p[0] - self.rx_pos, axis=1) / C_KM_S
        tx = np.where(self.transmit[:, None], self.tx_pos, self.tx_pos + self.tx_vel * (tt - s)[:, None])
        rx = np.where(self.transmit[:, None], self.rx_pos + self.rx_vel * (tr_ - s)[:, None], self.rx_pos)
        down = np.linalg.norm(p - rx[None], axis=2)
        up = np.linalg.norm(p - tx[None], axis=2)
        return np.where(self.legs == 2, 0.5 * (down + up), np.where(self.sc_rx, up, down))

    def predict(self, traj, tb=None):
        """Predicted values (k, n) or (k, n, 2) for all the trajectories of ``traj``."""
        if tb is None:
            tb = self.times(traj)
        if self.kind in ('azel', 'radec'):
            d = traj.state(tb)[0] - self.rx_pos[None]
            if self.kind == 'azel':
                e = np.einsum('nij,knj->kni', self.enu, d)
                return np.stack([np.arctan2(e[..., 0], e[..., 1]),
                                 np.arcsin(e[..., 2] / np.linalg.norm(e, axis=2))], axis=2)
            if self.rot is not None:
                d = np.einsum('nij,knj->kni', self.rot, d)
            return np.stack([np.arctan2(d[..., 1], d[..., 0]), np.arcsin(d[..., 2] / np.linalg.norm(d, axis=2))], axis=2)
        if self.kind == 'range':
            return self.ranges(traj, tb) + np.where(self.legs == 2, 0.5, 1.0) * C_KM_S * self.delay
        p, v = traj.state(tb)
        dn = p - self.rx_pos[None]
        dp = p - self.tx_pos[None]
        rd = np.sum(dn * (v - self.rx_vel[None]), axis=2) / np.linalg.norm(dn, axis=2)
        ru = np.sum(dp * (v - self.tx_vel[None]), axis=2) / np.linalg.norm(dp, axis=2)
        out = np.where(self.legs == 2, 0.5 * (rd + ru), np.where(self.sc_rx, ru, rd))
        if np.any(self.integ):
            a = self.start.ranges(traj, self.start.times(traj))
            b = self.end.ranges(traj, self.end.times(traj))
            out[:, self.integ] = (b - a) / self.interval[self.integ][None]
        return out

    def residuals(self, pred):
        """Residuals obs - pred (k, n) or (k, n, 2): angles wrapped (the first scaled by the cosine of
        the second), ranges with their modulus, round-trip values halved by ``factor``."""
        if self.kind in ('azel', 'radec'):
            d = self.obs[None] - pred
            d[..., 0] = ((d[..., 0] + math.pi) % (2 * math.pi) - math.pi) * np.cos(pred[..., 1])
            return d
        d = self.obs[None] * self.factor - pred
        if self.kind == 'range':
            m = self.modulus * self.factor
            amb = m > 0
            if np.any(amb):
                d[:, amb] -= np.round(d[0, amb] / m[amb])[None] * m[amb][None]
        return d


# ---------------------------------------------------------------------------
# First guesses
# ---------------------------------------------------------------------------
def _los(model, k):
    """Unit vectors (GCRS) of the angle measurements."""
    a, b = model.obs[k, 0], model.obs[k, 1]
    if model.kind == 'azel':
        e = np.stack([np.sin(a) * np.cos(b), np.cos(a) * np.cos(b), np.sin(b)], axis=-1)
        return np.einsum('nji,nj->ni', model.enu[k], e)
    u = np.stack([np.cos(a) * np.cos(b), np.sin(a) * np.cos(b), np.sin(b)], axis=-1)
    if model.rot is not None:
        u = np.einsum('nji,nj->ni', model.rot[k], u)
    return u


def _two_body_fit(x0, s, positions=None, model=None, idx=None, iters=25):
    """A two-body orbit at s = 0 fitted to positions (n, 3) at times s, or to angles of ``model``."""
    x = np.array(x0, float)

    def res(x):
        st = kepler(x, s, GM_EARTH)
        if positions is not None:
            return (st[:, :3] - positions).ravel()
        d = st[:, :3] - model.rx_pos[idx]
        u = d / np.linalg.norm(d, axis=1, keepdims=True)
        return (u - _los(model, idx)).ravel()

    for _ in range(iters):
        r0 = res(x)
        J = np.empty((r0.size, 6))
        for j in range(6):
            dx = np.zeros(6)
            dx[j] = 1e-3 if j < 3 else 1e-6
            J[:, j] = (res(x + dx) - r0) / dx[j]
        step = np.linalg.lstsq(J, -r0, rcond=None)[0]
        lam = 1.0
        c0 = r0 @ r0
        while lam > 1e-4:
            xn = x + lam * step
            try:
                if (lambda r: r @ r)(res(xn)) < c0:
                    break
            except (FloatingPointError, ValueError):
                pass
            lam *= 0.5
        x = x + lam * step
        if np.linalg.norm(lam * step[:3]) < 1e-6:
            break
    return x, res(x)


def _gauss(model, i, j, k, s):
    """Gauss's method on the angle measurements i, j, k -> candidate states at s[j] (km, km/s)."""
    mu = GM_EARTH
    idx = np.array([i, j, k])
    L = _los(model, idx)
    Rs = model.rx_pos[idx]
    t1, t2, t3 = s[i], s[j], s[k]
    tau1, tau3 = t1 - t2, t3 - t2
    tau = tau3 - tau1
    p1, p2, p3 = np.cross(L[1], L[2]), np.cross(L[0], L[2]), np.cross(L[0], L[1])
    D0 = L[0] @ p1
    if abs(D0) < 1e-12:
        return []
    D = np.array([[Rs[a] @ p for p in (p1, p2, p3)] for a in range(3)])
    A = (-D[0, 1] * tau3 / tau + D[1, 1] + D[2, 1] * tau1 / tau) / D0
    B = (D[0, 1] * (tau3 ** 2 - tau ** 2) * tau3 / tau + D[2, 1] * (tau ** 2 - tau1 ** 2) * tau1 / tau) / (6 * D0)
    E = Rs[1] @ L[1]
    R2 = Rs[1] @ Rs[1]
    a = -(A * A + 2 * A * E + R2)
    b = -2 * mu * B * (A + E)
    c = -mu * mu * B * B
    roots = np.roots([1, 0, a, 0, 0, b, 0, 0, c])
    out = []
    for r2 in roots:
        if abs(r2.imag) > 1e-6 * abs(r2) or r2.real < WGS84_A:
            continue
        r2 = r2.real
        rho1 = ((6 * (D[2, 0] * tau1 / tau3 + D[1, 0] * tau / tau3) * r2 ** 3 + mu * D[2, 0] * (tau ** 2 - tau1 ** 2) * tau1 / tau3)
                / (6 * r2 ** 3 + mu * (tau ** 2 - tau3 ** 2)) - D[0, 0]) / D0
        rho2 = A + mu * B / r2 ** 3
        rho3 = ((6 * (D[0, 2] * tau3 / tau1 - D[1, 2] * tau / tau1) * r2 ** 3 + mu * D[0, 2] * (tau ** 2 - tau3 ** 2) * tau3 / tau1)
                / (6 * r2 ** 3 + mu * (tau ** 2 - tau1 ** 2)) - D[2, 2]) / D0
        if min(rho1, rho2, rho3) <= 0:
            continue
        r1, rr2, r3 = Rs[0] + rho1 * L[0], Rs[1] + rho2 * L[1], Rs[2] + rho3 * L[2]
        f1 = 1 - mu * tau1 ** 2 / (2 * r2 ** 3)
        g1 = tau1 - mu * tau1 ** 3 / (6 * r2 ** 3)
        f3 = 1 - mu * tau3 ** 2 / (2 * r2 ** 3)
        g3 = tau3 - mu * tau3 ** 3 / (6 * r2 ** 3)
        v2 = (-f3 * r1 + f1 * r3) / (f1 * g3 - f3 * g1)
        out.append(np.concatenate([rr2, v2]))
    return out


def first_guess(models, apriori_state=None):
    """A state at s = 0 from an a-priori orbit, from positions (angles with ranges at the same times
    and stations), or from Gauss's method on angles -> (state, how)."""
    with np.errstate(all='ignore'):           # candidates that fail are skipped
        return _first_guess(models, apriori_state)


def _first_guess(models, apriori_state):
    if apriori_state is not None:
        return np.asarray(apriori_state, float), 'apriori'
    ang = [m for m in models if m.kind in ('azel', 'radec')]
    rng = next((m for m in models if m.kind == 'range'), None)
    if ang and rng is not None:
        a = ang[0]
        # an angle and a range of the same station at the same time give a position
        ts = {(round(v, 3), rng.rx_st[k]): k for k, v in enumerate(rng.s)}
        pairs = [(k, ts[round(v, 3), a.rx_st[k]]) for k, v in enumerate(a.s) if (round(v, 3), a.rx_st[k]) in ts]
        pairs = [(i, j) for i, j in pairs if rng.legs[j] == 2 or not rng.sc_rx[j]]
        if len(pairs) >= 3 and all(rng.modulus[j] == 0 for _, j in pairs):
            i = np.array([p[0] for p in pairs])
            j = np.array([p[1] for p in pairs])
            u = _los(a, i)
            rho = rng.obs[j] - np.where(rng.legs[j] == 2, 0.5, 1.0) * C_KM_S * rng.delay[j]
            best = None
            si = a.s[i]
            # a fix in the middle of the longest run of fixes of one station, with the velocity from
            # its neighbours in time
            first = rng.rx_st[pairs[0][1]]
            same = np.array([rng.rx_st[q] == first for _, q in pairs])
            order = np.flatnonzero(same)[np.argsort(si[same])]
            runs = np.split(order, np.flatnonzero(np.diff(si[order]) > 600.0) + 1)
            run = max(runs, key=len)
            k0 = run[len(run) // 2]
            ka, kb = run[max(len(run) // 2 - 1, 0)], run[min(len(run) // 2 + 1, len(run) - 1)]
            for f in (1.0, 0.5):           # the range may be the whole round trip
                pos = a.rx_pos[i] + (rho * f)[:, None] * u
                if np.min(np.linalg.norm(pos, axis=1)) < WGS84_A or not si[kb] > si[ka]:
                    continue
                vel = (pos[kb] - pos[ka]) / (si[kb] - si[ka])
                try:
                    # a two-body orbit through the fixes of that run, then through all of them
                    x, _ = _two_body_fit(np.concatenate([pos[k0], vel]), si[run] - si[k0], positions=pos[run])
                    x = kepler(x, np.array([-si[k0]]), GM_EARTH)[0]
                    x, r = _two_body_fit(x, si, positions=pos)
                except (FloatingPointError, ValueError, np.linalg.LinAlgError):
                    continue
                if not np.all(np.isfinite(x)):
                    continue
                rms = float(np.sqrt(np.mean(r * r)))
                if best is None or rms < best[0]:
                    best = (rms, x)
            if best is not None:
                return best[1], 'positions'
    if ang:
        a = max(ang, key=lambda m: m.n)
        if a.n >= 3:
            order = np.argsort(a.s)
            cands = []
            # three measurements spread over the densest pass, then over the whole arc
            s = a.s[order]
            gaps = np.flatnonzero(np.diff(s) > 1800.0)
            passes = np.split(np.arange(s.size), gaps + 1)
            longest = max(passes, key=lambda p: (len(p), s[p[-1]] - s[p[0]]))
            triples = []
            if len(longest) >= 3:
                triples.append((longest[0], longest[len(longest) // 2], longest[-1]))
                triples.append((longest[len(longest) // 4], longest[len(longest) // 2], longest[3 * len(longest) // 4]))
            if s.size >= 3:
                triples.append((0, s.size // 2, s.size - 1))
            for i, j, k in triples:
                if len({i, j, k}) < 3:
                    continue
                ii, jj, kk = order[i], order[j], order[k]
                for x2 in _gauss(a, ii, jj, kk, a.s):
                    x0 = kepler(x2, np.array([-a.s[jj]]), GM_EARTH)[0]     # back to s = 0
                    try:
                        x, r = _two_body_fit(x0, a.s, model=a, idx=np.arange(a.n))
                    except (FloatingPointError, ValueError, np.linalg.LinAlgError):
                        continue
                    if not np.all(np.isfinite(x)) or np.linalg.norm(x[:3]) < WGS84_A:
                        continue
                    cands.append((float(np.sqrt(np.mean(r * r))), x))
            if cands:
                return min(cands, key=lambda c: c[0])[1], 'gauss'
    raise _err('TDM から初期軌道を決められません。角度（ANGLE_1・ANGLE_2）の観測を含む TDM にするか、'
               '同じ衛星の軌道のメッセージ（OPM・OMM など）か TLE を一緒に入力してください')


# ---------------------------------------------------------------------------
# Least squares
# ---------------------------------------------------------------------------
def _weighted(models, traj):
    """Weighted residuals (k, m) of all the measurements kept (the first row: the nominal)."""
    out = []
    tbs = [m.times(traj) for m in models]
    for m, tb in zip(models, tbs):
        d = m.residuals(m.predict(traj, tb))
        d = d[:, m.edit]
        out.append((d / m.sigma).reshape(traj.k, -1))
    return np.concatenate(out, axis=1)


def _check_factors(models, traj):
    """A range or Doppler of two-leg paths given as the whole round trip (twice the one-way
    equivalent) -> halved; returns the notes."""
    notes = []
    for m in models:
        if m.kind not in ('range', 'doppler') or not np.any(m.legs == 2):
            continue
        pred = m.predict(traj)[0]
        two = m.legs == 2
        obs = m.obs[two]
        p = pred[two]
        if m.kind == 'range' and np.any(m.modulus[two] > 0):
            continue
        ok = np.abs(p) > (1.0 if m.kind == 'range' else 1e-3)
        if np.count_nonzero(ok) < 3:
            continue
        ratio = float(np.median(obs[ok] / p[ok]))
        if abs(ratio - 2.0) < 0.15:
            m.factor = 0.5
            notes.append(tr('TDM の {key} は往復の値として扱いました（片道に換算した値の 2 倍になっていたため）',
                            key='RANGE' if m.kind == 'range' else 'DOPPLER'))
    return notes


def determine(ctx, ms, apriori=None, force_kw=None):
    """The orbit from the measurements -> (Time of the epoch, state (6,) GCRS, statistics, notes)."""
    ref_rows = [r for rows in ms.rows.values() for r in rows]
    if not ref_rows:
        raise _err('TDM に軌道決定に使える観測（角度・距離・ドップラー）がありません')
    jd = np.array([r[0] + r[1] for r in ref_rows])
    mid = 0.5 * (jd.min() + jd.max())
    tref = ctx.ts.tt_jd(math.floor(mid - 0.5) + 0.5, mid - (math.floor(mid - 0.5) + 0.5))
    ref = (float(tref.whole), float(tref.tt_fraction))
    models = [_Model(ctx, k, rows, ref) for k, rows in ms.rows.items() if rows]
    s_all = np.concatenate([m.s for m in models])
    s_lo, s_hi = float(s_all.min()) - 600.0, float(s_all.max()) + 600.0
    ap = None
    if apriori is not None:
        st = apriori.vf.at(tref)
        ap = np.concatenate([st.position.km, st.velocity.km_per_s])
    x, how = first_guess(models, ap)
    forces = Forces(ctx, ref, 'earth', **(force_kw or {}))
    notes = list(ms.notes)
    n_types = sum(m.n for m in models)
    if n_types < 6:
        raise _err('TDM の観測が少なすぎて軌道を決められません（{n} 件）', n=n_types)

    def fit(x, rounds=12):
        cost = None
        for _ in range(rounds):
            dr = max(1e-2, 1e-6 * float(np.linalg.norm(x[:3])))
            dv = dr * 1e-3
            X = np.vstack([x, x + np.diag([dr, dr, dr, dv, dv, dv])])
            traj = BatchTrajectory(forces, X, s_lo, s_hi)
            W = _weighted(models, traj)
            r0 = W[0]
            H = (W[1:] - r0[None]).T / np.array([dr, dr, dr, dv, dv, dv])[None]
            c0 = float(r0 @ r0)
            step = np.linalg.lstsq(H, -r0, rcond=None)[0]
            lam = 1.0
            while True:
                xn = x + lam * step
                try:
                    tn = BatchTrajectory(forces, xn[None], s_lo, s_hi)
                    cn = float(np.sum(_weighted(models, tn)[0] ** 2))
                except (ODError, PropagationError, FloatingPointError):
                    cn = math.inf
                if cn <= c0 or lam < 1e-3:
                    break
                lam *= 0.5
            if cn > c0:
                return x, c0, H
            x = xn
            cost = cn
            if np.linalg.norm(lam * step[:3]) < 1e-5 and (c0 - cn) <= 1e-6 * max(c0, 1.0):
                break
        return x, cost, H

    # the angles (and one-way data) first when the guess came from the measurements, then all
    if how != 'apriori' and any(m.kind in ('azel', 'radec') for m in models):
        keep = [m for m in models if m.kind in ('azel', 'radec')]
        saved = models
        models = keep
        x, _, _ = fit(x, 6)
        models = saved
    traj = BatchTrajectory(forces, x[None], s_lo, s_hi)
    notes += _check_factors(models, traj)
    x, cost, H = fit(x)
    # weights from the scatter of each type, and editing of outliers
    for _ in range(3):
        traj = BatchTrajectory(forces, x[None], s_lo, s_hi)
        changed = False
        for m in models:
            d = m.residuals(m.predict(traj))[0]
            mag = np.linalg.norm(d, axis=1) / math.sqrt(2) if d.ndim == 2 else np.abs(d)
            kept = mag[m.edit]
            rms = float(np.sqrt(np.mean(kept ** 2))) if kept.size else m.sigma
            sigma = max(rms, SIGMA_FLOOR[m.kind])
            edit = mag < EDIT_SIGMA * sigma
            if np.count_nonzero(edit) < 0.5 * m.n:          # never drop most of a type
                edit = mag <= np.quantile(mag, 0.5)
            if not np.array_equal(edit, m.edit) or abs(sigma - m.sigma) > 0.1 * m.sigma:
                changed = True
            m.edit, m.sigma = edit, sigma
        if not changed:
            break
        x, cost, H = fit(x, 6)
    traj = BatchTrajectory(forces, x[None], s_lo, s_hi)
    stats = {'types': {}, 'n_used': 0, 'n_rejected': 0, 'first_guess': how}
    unit = {'azel': ('arcsec', 206264.806), 'radec': ('arcsec', 206264.806), 'range': ('m', 1000.0),
            'doppler': ('mm/s', 1e6)}
    for m in models:
        d = m.residuals(m.predict(traj))[0][m.edit]
        rms = float(np.sqrt(np.mean(d ** 2))) if d.size else None
        name, f = unit[m.kind]
        stats['types'][m.kind] = {'n': int(np.count_nonzero(m.edit)), 'rejected': int(m.n - np.count_nonzero(m.edit)),
                                  'rms': None if rms is None else rms * f, 'unit': name,
                                  'factor': getattr(m, 'factor', 1.0)}
        stats['n_used'] += int(np.count_nonzero(m.edit))
        stats['n_rejected'] += int(m.n - np.count_nonzero(m.edit))
    try:
        cov = np.linalg.inv(H.T @ H)
        stats['sigma_pos_km'] = float(math.sqrt(max(np.trace(cov[:3, :3]), 0.0)))
        stats['sigma_vel_km_s'] = float(math.sqrt(max(np.trace(cov[3:, 3:]), 0.0)))
    except (np.linalg.LinAlgError, TypeError):
        stats['sigma_pos_km'] = None
    stats['arc_s'] = float(s_all.max() - s_all.min())
    stats['arc_start_jd'] = float(jd.min())
    stats['arc_end_jd'] = float(jd.max())
    el = osc_elements(x[:3], x[3:])
    if stats['arc_s'] < 0.5 * (el['period_min'] or 1e9) * 60:
        notes.append(tr('軌道決定に使った観測の期間が {min:.0f} 分と軌道の周期に比べて短いため、決めた軌道の誤差が大きい可能性があります',
                        min=stats['arc_s'] / 60))
    if stats.get('sigma_pos_km') and stats['sigma_pos_km'] > 1.0:
        notes.append(tr('軌道決定の位置の推定誤差（1σ）が {km:.1f} km と大きくなっています', km=stats['sigma_pos_km']))
    return tref, x, stats, notes


def tdm_observer(ctx, msgs, tdms, objs, stations, query, name):
    """The observer of a spacecraft tracked in the TDMs: the orbit determined from them."""
    from . import ccsds
    parts = ccsds.participants(tdms)
    keys = {ccsds.text_value(p): p for p in parts}
    if query and ccsds.text_value(query) in keys:
        target = keys[ccsds.text_value(query)]
    else:
        # the participant whose position is not given (the others are stations), or the object of an
        # orbit message given with the TDM
        cands = [p for p in parts if ccsds.text_value(p) not in stations]
        named = [p for p in cands if any(ccsds._matches(o['ident'], p) for o in objs)]
        if query:
            q = [p for p in cands if any(ccsds._matches(o['ident'], p) and ccsds._matches(o['ident'], query) for o in objs)]
            cands = q or cands
        if len(cands) > 1 and len(named) == 1:
            cands = named
        if not stations and len(cands) != 1:
            raise _err('TDM には観測局の位置が書かれていないため、衛星以外の参加者（観測局）の位置を指定してください（参加者: {list}）',
                       list=', '.join(parts))
        if len(cands) != 1:
            raise _err('TDM の参加者（{list}）のうち、どれが衛星かを指定してください（ほかの参加者は観測局として位置を指定します）',
                       list=', '.join(parts))
        target = cands[0]
    apriori_obj = [o for o in objs if ccsds._matches(o['ident'], target)] or (objs if len(objs) == 1 else [])
    apriori = None
    notes = []
    if apriori_obj:
        apriori = ccsds.orbit_observer(ctx, apriori_obj[0], '')
    ms = collect(ctx, tdms, stations, target)
    if not any(ms.rows.values()):
        raise _err('TDM に {name} の観測（角度・距離・ドップラー）がありません', name=target)
    force_kw = {}
    if apriori is not None and getattr(apriori, 'anchors', None):
        f = apriori.anchors[0]['traj'].forces
        force_kw = {'drag': f.drag, 'srp': f.srp}
    tref, x, stats, od_notes = determine(ctx, ms, apriori, force_kw)
    notes += od_notes
    if ms.unused:
        notes.append(tr('TDM の次の観測は軌道決定に使っていません: {list}',
                        list=', '.join(f'{k} {n}' for k, n in sorted(ms.unused.items()))))
    msg = tdms[0]
    info = ccsds.message_info(msg)
    info.update(message='TDM', target=target, stations=sorted(ms.stations_used), od=stats,
                apriori=apriori.describe().get('message', apriori.describe().get('model')) if apriori else None)
    return PropagatedObserver(ctx, [(tref, x)], 'earth', drag=force_kw.get('drag'), srp=force_kw.get('srp'),
                              name=name or target, info=info, notes=notes, label='TDM')

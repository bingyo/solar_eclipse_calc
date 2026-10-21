"""Checks of the geometry given for comparing with solar images and of the
numerics behind it: the P angle and the solar-north-up (helioprojective)
X / Y, the depth of the body in the limb, the sensitivity of the contact
times to the radii, the evaluation at arbitrary (image) times, the instant
of the maximum, the step of the JPL Horizons tables, leap seconds, the times
of observations, and the test whether the Earth hides the Sun from a
spacecraft.

Run with  python tests/test_geometry.py  (offline, except the Hinode sunrise
of test_earth_occultation_hinode, which uses the NASA SSCWeb orbit cached in
data/cache/ by tests/test_hinode.py or fetched on the first run).
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from skyfield.framelib import itrs  # noqa: E402

from eclipsecalc.constants import (AU_KM, C_KM_S, DAY_S, GM_EARTH, GM_MOON, MOON_K_INTERNAL, RAD2ARCSEC,  # noqa: E402
                                   SUN_RADIUS_OBSERVED, WGS84_A, WGS84_B)
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.geometry import norm  # noqa: E402
from eclipsecalc.local import LocalSearch, Params, bisect_roots, earth_disk  # noqa: E402
from eclipsecalc.observers import (AU_PER_DAY_PER_KM_S, GeocenterObserver, GroundObserver,  # noqa: E402
                                   HorizonsObserver, KeplerObserver, ObserverError, SSCWebObserver,
                                   _HorizonsVF, _KeplerVF, build_observer, hermite_step_min)
from eclipsecalc.timeutil import iso_from_jd, iso_utc, parse_obs_time, parse_utc  # noqa: E402

CTX = get_context()


def solar_P(jd_tt):
    """Position angle of the solar rotation axis (Meeus, Astronomical Algorithms ch. 29; as in
    tools/validate_hinode_images.py)."""
    T = (jd_tt - 2451545.0) / 36525
    L0 = 280.46646 + 36000.76983 * T
    M = math.radians(357.52911 + 35999.05029 * T)
    C = (1.914602 - 0.004817 * T) * math.sin(M) + 0.019993 * math.sin(2 * M) + 0.000289 * math.sin(3 * M)
    om = math.radians(125.04 - 1934.136 * T)
    lam = math.radians(L0 + C - 0.00569 - 0.00478 * math.sin(om))
    eps = math.radians(23.4392911 + 0.00256 * math.cos(om))
    K = math.radians(73.6667 + 1.3958333 * (jd_tt - 2396758.0) / 36525)
    return math.degrees(math.atan(-math.cos(lam) * math.tan(eps)) +
                        math.atan(-math.cos(lam - K) * math.tan(math.radians(7.25))))


_cache = {}


def mercury_2019(params=None):
    """Geocentric transit of Mercury of 2019-11-11 -> (LocalSearch, event, contacts by label)."""
    params = params or Params()
    key = tuple(sorted(params.as_dict().items()))
    if key not in _cache:
        ls = LocalSearch(CTX, GeocenterObserver(), 'mercury', params,
                         parse_utc(CTX, '2019-11-10'), parse_utc(CTX, '2019-11-13'))
        ev = ls.run()
        assert len(ev) == 1
        _cache[key] = ls, ev[0], {c['label']: c for c in ev[0]['contacts']}
    return _cache[key]


def test_p_angle_and_helioprojective():
    ls, e, c = mercury_2019()
    # P against Meeus' formula (Carrington's elements) over the years
    x = np.linspace(-1500, 3000, 97) + 0.37
    d = ls.details(x)
    dP = np.array([p - solar_P(jd) for p, jd in zip(d['p_angle'], d['jd_tt'])])
    assert np.abs(dP).max() <= 0.01, np.abs(dP).max()
    assert 26.0 < d['p_angle'].max() < 26.4 and -26.4 < d['p_angle'].min() < -26.0
    # (xi, eta) turned by P and back; position angle from solar north toward solar east (-X)
    P = np.radians(d['p_angle'])
    xi = -d['hpc_x'] * np.cos(P) + d['hpc_y'] * np.sin(P)
    eta = d['hpc_x'] * np.sin(P) + d['hpc_y'] * np.cos(P)
    assert np.abs(xi - d['xi']).max() < 1e-9 and np.abs(eta - d['eta']).max() < 1e-9
    pa = np.degrees(np.arctan2(-d['hpc_x'], d['hpc_y'])) % 360
    dpa = (pa - d['pa_solar'] + 180) % 360 - 180
    assert np.abs(dpa).max() < 1e-9
    # Mercury enters on the solar east limb (X < 0) and leaves on the west limb, north of the
    # centre at the maximum (75.9" from it, NASA)
    assert c['C1']['hpc_x'] < -900 and c['C4']['hpc_x'] > 900
    assert abs(c['MAX']['hpc_y'] - 75.9) < 0.5 and abs(c['MAX']['hpc_x']) < 5


def test_limb_depth_at_contacts():
    _, _, c = mercury_2019()
    for lab, depth in (('C1', -1.0), ('C2', 1.0), ('C3', 1.0), ('C4', -1.0)):
        assert abs(c[lab]['limb_depth'] - depth) < 1e-4, (lab, c[lab]['limb_depth'])
        assert abs(c[lab]['limb_dist'] + depth * c[lab]['rho_b']) < 1e-3, (lab, c[lab]['limb_dist'])
    assert c['MAX']['limb_dist'] < -800 and c['MAX']['dt_drs'] is None and c['MAX']['dt_drb'] is None


def _radius_shift(dist_km, r_km, d_arcsec):
    """The radius (km) larger by d_arcsec seen from dist_km."""
    return dist_km * math.sin(math.asin(r_km / dist_km) + d_arcsec / RAD2ARCSEC) - r_km


def _check_sensitivity(c0, c1, key, dist_key, r0, r1, labels):
    for lab in labels:
        d = (math.asin(r1 / c0[lab][dist_key]) - math.asin(r0 / c0[lab][dist_key])) * RAD2ARCSEC
        expect = c0[lab][key] * d
        got = (c1[lab]['jd_tt'] - c0[lab]['jd_tt']) * DAY_S
        assert abs(got - expect) < 0.01 * abs(expect) + 0.002, (lab, key, got, expect)


def test_contact_sensitivity_transit():
    p0 = Params()
    _, _, c0 = mercury_2019(p0)
    assert c0['C1']['dt_drs'] < -9 and c0['C4']['dt_drs'] > 9       # ~0.1"/s: 10 s per arcsec
    rs1 = SUN_RADIUS_OBSERVED + _radius_shift(c0['C1']['sun_dist_km'], SUN_RADIUS_OBSERVED, 0.1)
    _, _, c1 = mercury_2019(Params(sun_radius_km=rs1))
    _check_sensitivity(c0, c1, 'dt_drs', 'sun_dist_km', SUN_RADIUS_OBSERVED, rs1, ('C1', 'C2', 'C3', 'C4'))
    rb0 = p0.mercury_radius_km
    rb1 = rb0 + _radius_shift(c0['C1']['body_dist_km'], rb0, 0.1)
    _, _, c2 = mercury_2019(Params(mercury_radius_km=rb1))
    _check_sensitivity(c0, c2, 'dt_drb', 'body_dist_km', rb0, rb1, ('C1', 'C2', 'C3', 'C4'))


def test_contact_sensitivity_total_eclipse():
    # Total eclipse of 2024-04-08 near the point of greatest eclipse: a larger Sun shortens
    # totality (C2 later, C3 earlier), a larger Moon lengthens it.
    obs = GroundObserver(25.29, -104.14, 0)
    a, b = parse_utc(CTX, '2024-04-08T16:00'), parse_utc(CTX, '2024-04-08T21:00')

    def run(p):
        e = LocalSearch(CTX, obs, 'moon', p, a, b).run()[0]
        assert e['type'] == 'total'
        return {x['label']: x for x in e['contacts']}
    p0 = Params()
    c0 = run(p0)
    assert c0['C2']['dt_drs'] > 0 > c0['C3']['dt_drs'] and c0['C2']['dt_drb'] < 0 < c0['C3']['dt_drb']
    rs1 = p0.sun_radius_km + _radius_shift(c0['C2']['sun_dist_km'], p0.sun_radius_km, 0.1)
    _check_sensitivity(c0, run(Params(sun_radius_km=rs1)), 'dt_drs', 'sun_dist_km', p0.sun_radius_km, rs1,
                       ('C1', 'C2', 'C3', 'C4'))
    ri1 = p0.moon_radius_int_km + _radius_shift(c0['C2']['body_dist_km'], p0.moon_radius_int_km, 0.1)
    _check_sensitivity(c0, run(Params(moon_radius_int_km=ri1)), 'dt_drb', 'body_dist_km',
                       p0.moon_radius_int_km, ri1, ('C2', 'C3'))
    re1 = p0.moon_radius_ext_km + _radius_shift(c0['C1']['body_dist_km'], p0.moon_radius_ext_km, 0.1)
    _check_sensitivity(c0, run(Params(moon_radius_ext_km=re1)), 'dt_drb', 'body_dist_km',
                       p0.moon_radius_ext_km, re1, ('C1', 'C4'))
    assert abs(p0.moon_radius_int_km - MOON_K_INTERNAL * WGS84_A) < 1e-9


def test_geometry_at():
    ls, e, c = mercury_2019()
    x = np.array([c['C1']['jd_tt'], e['jd_max'], c['C3']['jd_tt'] + 30 / DAY_S]) - ls.jd0
    g = ls.geometry_at(x)
    d = ls.details(x)
    for k in ('hpc_x', 'hpc_y', 'sep_arcsec', 'p_angle', 'limb_depth', 'jd_tt'):
        assert np.allclose(g[k], d[k], rtol=0, atol=1e-9), k
    assert g['time'] == d['time'] and len(g['hpc_vx']) == 3
    h = 5.0 / DAY_S
    dm, dp = ls.details(x - h), ls.details(x + h)
    for k, rate in (('hpc_x', 'hpc_vx'), ('hpc_y', 'hpc_vy'), ('sep_arcsec', 'sep_rate')):
        assert np.abs((dp[k] - dm[k]) / 10.0 - g[rate]).max() < 1e-5, rate
    # Mercury crosses the Sun westward at ~0.1"/s; at C1 the separation decreases at 1/|dt_drs|
    assert 0.09 < g['hpc_vx'][1] < 0.11
    assert abs(g['sep_rate'][0] + 1 / abs(c['C1']['dt_drs'])) < 1e-4


def test_maximum_reproducible():
    # Partial eclipse of 2032-11-03 at Tokyo (slow: the minimum of the separation is flat): the
    # instant of the maximum does not depend on the period searched (a saved result is computed
    # again over another period) and is the minimum of a dense independent fit.
    obs = GroundObserver(35.6812, 139.7671, 40)
    jd = []
    for start in ('2032-10-20', '2032-11-01', '2032-11-02T21:00'):
        ls = LocalSearch(CTX, obs, 'moon', Params(), parse_utc(CTX, start), parse_utc(CTX, '2032-11-05'))
        e = ls.run()[0]
        jd.append(e['jd_max'])
    assert (max(jd) - min(jd)) * DAY_S < 1e-4, np.array(jd) - jd[0]
    dt = np.linspace(-30.0, 30.0, 1201)
    c = np.polyder(np.polyfit(dt, ls.E.geom(e['xm'] + dt / DAY_S)['sep'] ** 2, 4))
    r = np.roots(c)
    r = r[np.isreal(r)].real
    assert np.abs(r).min() < 1e-3, r


def test_hermite_step():
    leo = hermite_step_min(WGS84_A + 500, 7.613)
    assert 2.0 < leo < 2.6, leo
    assert 20 < hermite_step_min(42_164, 3.075) < 25          # geostationary: 60 min gives 0.5 km
    # cubic Hermite interpolation of a circular low orbit tabulated at the step: error <= 10 m,
    # and well above it at twice the step
    kep = _KeplerVF('leo', 2461000.5, WGS84_A + 500, 0.0, 97.4, 30.0, 0.0, 0.0, j2=False)
    for step, lo, hi in ((math.floor(leo), 0.0, 0.010), (2 * math.floor(leo), 0.02, 1.0)):
        jd = 2461000.5 + np.arange(0, 400) * step / 1440
        p, v, _, _ = kep._at(CTX.ts.tdb_jd(jd))
        vf = _HorizonsVF('leo')
        vf.add(jd, p * AU_KM, v / AU_PER_DAY_PER_KM_S)
        t = CTX.ts.tdb_jd(jd[1:-1] - 0.37 * step / 1440)
        err = norm(vf._at(t)[0] - kep._at(t)[0]) * AU_KM
        assert lo < err.max() < hi, (step, err.max())


class _TableHorizons(HorizonsObserver):
    """A HorizonsObserver whose tables come from a two-body orbit instead of JPL Horizons."""

    def __init__(self, kep, step_min, max_points=None):
        super().__init__(CTX, 'test', step_min, 'test')
        self.kep, self.fetches = kep, []
        if max_points:
            self.MAX_POINTS = max_points

    def _fetch(self, jd_a, jd_b, step_min):
        n = (jd_b - jd_a) * 1440 / step_min
        if n > self.MAX_POINTS:
            raise ObserverError(f'{n:.0f} points')
        self.fetches.append((jd_a, jd_b, step_min))
        jd = jd_a + np.arange(int(n) + 1) * step_min / 1440.0
        p, v, _, _ = self.kep._at(CTX.ts.tdb_jd(jd))
        return jd, p * AU_KM, v / AU_PER_DAY_PER_KM_S


def test_horizons_step_made_finer():
    # 600 km orbit asked with the default step of 60 min: 2 min is used (warning), long windows
    # are fetched in parts, and the eclipse agrees with the same orbit computed directly.
    epoch = parse_utc(CTX, '2027-08-02T00:00')
    el = dict(a=WGS84_A + 600, e=0.001, i=97.8, raan=40.0, argp=0.0, m0=0.0)
    obs = _TableHorizons(_KeplerVF('t', epoch, *el.values(), j2=False), 60, max_points=150)
    a, b = parse_utc(CTX, '2027-08-02T06:00'), parse_utc(CTX, '2027-08-02T14:00')
    ls = LocalSearch(CTX, obs, 'moon', Params(), a, b)
    ev = ls.run()
    assert obs.requested_step_min == 60 and obs.step_min == 2, (obs.step_min, obs.step_limit_min)
    d = obs.describe()
    assert d['step_min'] == 2 and d['step_min_requested'] == 60
    assert any('60 分から 2 分' in w for w in ls.warnings), ls.warnings
    steps = [f[2] for f in obs.fetches]
    assert steps[0] == 60 and set(steps[1:]) == {2} and len(steps) >= 4, obs.fetches
    assert all((hi - lo) * 1440 / s <= obs.MAX_POINTS for lo, hi, s in obs.fetches)
    ref = LocalSearch(CTX, KeplerObserver(CTX, epoch, *el.values(), j2=False), 'moon', Params(), a, b).run()
    assert len(ev) == len(ref) > 0
    for e, r in zip(ev, ref):
        for x, y in zip(e['contacts'], r['contacts']):
            assert x['label'] == y['label'] and abs(x['jd_tt'] - y['jd_tt']) * DAY_S < 0.01, (x['label'], x['time'], y['time'])
    # all the windows of a search together stay within MAX_TOTAL_POINTS
    small = _TableHorizons(_KeplerVF('t', epoch, *el.values(), j2=False), 60)
    small.MAX_TOTAL_POINTS = 300
    try:
        LocalSearch(CTX, small, 'moon', Params(), a, b).run()
        assert False, 'no error'
    except ObserverError as exc:
        assert '刻み 2 分' in str(exc) and '期間を短く' in str(exc), exc
    # an orbit as far as the Moon keeps the step asked for
    far = _TableHorizons(_KeplerVF('f', epoch, 400_000.0, 0.0, 30.0, 0.0, 0.0, 0.0, j2=False), 60)
    far.survey(a, b)
    assert far.step_min == far.requested_step_min == 60 and far.step_limit_min > 300



class _Circle:
    """Geocentric states of a circular motion (radius r_km, speed v_km_s, in the ICRS x / z plane)
    about a centre that follows ``centre(t)`` -> (position km, velocity km/s), for _TableHorizons."""

    def __init__(self, centre, r_km, v_km_s):
        self.centre, self.r, self.w = centre, r_km, v_km_s / r_km

    def _at(self, t):
        cp, cv = self.centre(t)
        ph = self.w * (np.asarray(t.tdb, float) - 2461000.5) * DAY_S
        c, s, z = np.cos(ph), np.sin(ph), np.zeros_like(ph)
        p = cp + self.r * np.array([c, z, s])
        v = cv + self.r * self.w * np.array([-s, z, c])
        return p / AU_KM, v * AU_PER_DAY_PER_KM_S, None, None


class _Flyby:
    """A hyperbolic flyby of the Earth in the ICRS x / y plane: perigee rp_km at the TDB JD jd_p,
    speed v_inf far from the Earth."""

    def __init__(self, jd_p, rp_km, v_inf):
        self.jd_p, self.A = jd_p, GM_EARTH / v_inf ** 2
        self.e = 1.0 + rp_km / self.A
        self.n = math.sqrt(GM_EARTH / self.A ** 3)

    def _at(self, t):
        A, e = self.A, self.e
        M = self.n * (np.asarray(t.tdb, float) - self.jd_p) * DAY_S
        F = np.arcsinh(M / e)
        for _ in range(60):
            F = F - (e * np.sinh(F) - F - M) / (e * np.cosh(F) - 1.0)
        q, dF, z = math.sqrt(e * e - 1.0), self.n / (e * np.cosh(F) - 1.0), np.zeros_like(F)
        p = A * np.array([e - np.cosh(F), q * np.sinh(F), z])
        v = A * dF * np.array([-np.sinh(F), q * np.cosh(F), z])
        return p / AU_KM, v * AU_PER_DAY_PER_KM_S, None, None


def _moon_state(t):
    m = (CTX.moon - CTX.earth).at(t)
    return m.position.km, m.velocity.km_per_s


def test_horizons_step_moon_and_flyby():
    # The step of a Horizons table follows the motion about the Moon and the perigee of a flyby,
    # which the geocentric distances of a coarse survey do not show.
    a = parse_utc(CTX, '2027-08-02T00:00')
    # a lunar orbiter 100 km above the surface (as LRO): 4 min instead of 60 (hundreds of km)
    orbit = _Circle(_moon_state, 1838.0, math.sqrt(GM_MOON / 1838.0))
    lro = _TableHorizons(orbit, 60)
    lro.survey(a, a + 2)
    assert lro.step_min == 4 and not lro.step_unresolved, (lro.step_min, lro.step_limit_min)
    assert any('60 分から 4 分' in w for w in lro.step_warnings()), lro.step_warnings()
    lro.prepare(CTX, [(a + 0.5, a + 0.7)])
    t = CTX.ts.tdb_jd(a + 0.5 + np.linspace(0.0, 0.2, 2001))
    err = norm(lro.vf._at(t)[0] - orbit._at(t)[0]) * AU_KM
    assert err.max() < 0.01, err.max()
    # a flyby of the Earth (perigee 7,000 km at 5 km/s far away) between two samples of the
    # survey: the step of the perigee (1 min) rather than that of the nearest samples (3 min)
    jd_p = a - 0.01 + 1.0 + 0.5 / 24
    fly = _TableHorizons(_Flyby(jd_p, 7000.0, 5.0), 60)
    jd, pos, vel = fly.survey(a, a + 2)
    vp = math.sqrt(25.0 + 2 * GM_EARTH / 7000.0)
    assert norm(pos).min() > 12_000 and fly.step_min == 1, (norm(pos).min(), fly.step_limit_min)
    assert abs(fly.step_limit_min - hermite_step_min(7000.0, vp)) < 0.01, fly.step_limit_min
    # ... but not when the perigee falls after the table (the flyby is not in the period)
    late = _TableHorizons(_Flyby(a + 6.0, 7000.0, 5.0), 60)
    late.survey(a, a + 2)
    assert late.step_limit_min > 30, late.step_limit_min
    # a sharp turn that neither the Earth nor the Moon explains (2,000 km about a point 10^6 km
    # away, period 3.5 h): the step measured on the survey and a warning that it may be too coarse
    odd = _TableHorizons(_Circle(lambda t: (np.array([1e6, 0.0, 0.0])[:, None], np.zeros((3, 1))),
                                 2000.0, 1.0), 60)
    odd.survey(a, a + 2)
    assert odd.step_unresolved and odd.step_min < 10, (odd.step_min, odd.step_limit_min)
    assert any('60 分刻みの下調べ' in w for w in odd.step_warnings()), odd.step_warnings()

def test_leap_second_strings():
    t = CTX.ts.utc(2016, 12, 31, 23, 59, np.array([59.0, 59.9996, 60.0, 60.5, 60.9994, 60.9996, 61.2]))
    assert iso_utc(t) == ['2016-12-31T23:59:59.000Z', '2016-12-31T23:59:60.000Z', '2016-12-31T23:59:60.000Z',
                          '2016-12-31T23:59:60.500Z', '2016-12-31T23:59:60.999Z', '2017-01-01T00:00:00.000Z',
                          '2017-01-01T00:00:00.200Z']
    # rounding to the ms across an ordinary minute, hour, day and year
    t = CTX.ts.utc(2017, 12, 31, 23, 59, np.array([59.9994, 59.9996]))
    assert iso_utc(t) == ['2017-12-31T23:59:59.999Z', '2018-01-01T00:00:00.000Z']
    assert iso_utc(CTX.ts.utc(2015, 6, 30, 23, 59, 60.25)) == '2015-06-30T23:59:60.250Z'


def test_parse_obs_time():
    ref = iso_from_jd(CTX, parse_utc(CTX, '2017-08-21T16:53:59.204'))
    assert ref == '2017-08-21T16:53:59.204Z'
    for text in ('2017-08-21T16:53:59.204Z', '2017-08-21 16:53:59.204', '2017-08-21t16:53:59.204z',
                 '2017/08/21 16:53:59.204', '21-Aug-2017 16:53:59.204 UT', '21-AUG-2017 16:53:59.204',
                 ' 21-aug-2017 16:53:59.204 UTC ', '2017-Aug-21 16:53:59.204', '2017-08-21T16:53:59.204+00:00'):
        assert iso_from_jd(CTX, parse_obs_time(CTX, text)) == ref, text
    assert iso_from_jd(CTX, parse_obs_time(CTX, '2017-08-21')) == '2017-08-21T00:00:00.000Z'
    assert iso_from_jd(CTX, parse_obs_time(CTX, '21-Aug-2017 16:53')) == '2017-08-21T16:53:00.000Z'
    assert iso_from_jd(CTX, parse_obs_time(CTX, '2016-12-31T23:59:60.5Z')) == '2016-12-31T23:59:60.500Z'
    # the JSOC style of SDO is TAI (UTC + 37 s in 2017) and needs its suffix
    assert iso_from_jd(CTX, parse_obs_time(CTX, '2017.08.21_16:54:36.204_TAI')) == ref
    assert iso_from_jd(CTX, parse_obs_time(CTX, '2017-08-21 16:54:36.204 TAI')) == ref
    # before 1972 the times are UT, as everywhere in the calculator
    assert abs(parse_obs_time(CTX, '1960-06-01 12:00') - parse_utc(CTX, '1960-06-01T12:00')) < 1e-12
    for bad in ('2017-02-30T00:00:00', '2017-08-21T24:00:00', '2017-08-21T16:60:00', '2016-06-30T23:59:60.5',
                '21-Agu-2017 16:53', '2017-08-21T16:53:59.2.1', '16:53:59', '', 'yesterday',
                '2017.08.21_16:53:59.204', '2017-08-21_16:53:59'):
        try:
            parse_obs_time(CTX, bad)
            assert False, bad
        except ValueError as exc:
            assert f'「{bad.strip()}」' in str(exc), (bad, exc)


# --- Earth occultation of the Sun seen from a spacecraft -------------------------
def _tangent_margin(E, obs, x):
    """Independent test of the Sun centre against the WGS84 ellipsoid: the ray from the
    spacecraft toward the Sun in the Earth's frame (first-order aberration by the Earth's
    barycentric velocity), in the ITRS, is outside the ellipsoid iff the same ray in coordinates
    in which the ellipsoid is a sphere misses that sphere -> clearance (km, > 0: visible)."""
    g = E.geom(x)
    st = obs.vf.at(g['t'])
    u = g['S'] / norm(g['S'])
    w = (g['o'].velocity.km_per_s - st.velocity.km_per_s) / C_KM_S
    u = u + w - np.sum(u * w, axis=0) * u
    R = itrs.rotation_at(g['t'])
    scale = np.array([1.0, 1.0, WGS84_A / WGS84_B])[:, None]
    r = np.einsum('ij...,j...->i...', R, st.position.km) * scale
    u = np.einsum('ij...,j...->i...', R, u) * scale
    lam = np.maximum(-np.sum(r * u, axis=0) / np.sum(u * u, axis=0), 0.0)
    g['vis'] = norm(r + lam * u) - WGS84_A
    return g


def _boundaries(obs, body, a, b, step_s=20.0):
    """Times (TT JD) at which the Sun centre is hidden / shown: by the calculator, by the
    independent test and by the earlier test (astrometric direction, GCRS z as the pole)."""
    ls = LocalSearch(CTX, obs, body, Params(include_invisible=True), parse_utc(CTX, a), parse_utc(CTX, b))
    if isinstance(obs, SSCWebObserver):
        obs.survey(ls.jd_a, ls.jd_b)
    obs.prepare(CTX, [(ls.jd_a - 0.01, ls.jd_b + 0.01)])
    E = ls.E

    def old(x):
        g = E.geom(x)
        se, reff, rn = earth_disk(obs.vf.at(g['t']).position.km, g['S'])
        g['vis'] = se - np.arcsin(np.minimum(reff / rn, 1.0))
        return g
    x = np.arange(ls.jd_a - ls.jd0, ls.jd_b - ls.jd0, step_s / DAY_S)
    out = []
    for f in (lambda x: E.geom(x, need_vis=True), lambda x: _tangent_margin(E, obs, x), old):
        v = f(x)['vis']
        i = np.nonzero((v[:-1] < 0) != (v[1:] < 0))[0]
        out.append(ls.jd0 + bisect_roots(f, 'vis', x[i], x[i + 1], v[i], tol_days=1e-10))
    return out


def test_earth_occultation_geostationary():
    # Around the equinox a geostationary satellite passes the Earth's shadow every night; the
    # boundaries agree with the exact tangency to 5 ms (the astrometric direction gave 1.2 s).
    new, exact, old = _boundaries(build_observer(CTX, {'type': 'geo', 'lon': 140.0}), 'moon',
                                  '2026-03-19T12:00', '2026-03-21T12:00')
    assert new.size == exact.size == old.size == 4
    assert np.abs(new - exact).max() * DAY_S < 0.005, (new - exact) * DAY_S
    assert 1.0 < np.abs(old - exact).min() * DAY_S and np.abs(old - exact).max() * DAY_S < 1.5


def test_earth_occultation_hinode():
    # Hinode (680 km) at sunset and sunrise during the Venus transit of 2012: within 0.03 s of the
    # exact tangency (the remainder is the circular Earth limb of earth_disk; the astrometric
    # direction gave 0.09-0.21 s).
    new, exact, old = _boundaries(SSCWebObserver(CTX, 'hinode'), 'venus', '2012-06-05T21:50', '2012-06-06T00:10')
    assert new.size == exact.size == old.size == 4, iso_from_jd(CTX, exact)
    assert np.abs(new - exact).max() * DAY_S < 0.03, (new - exact) * DAY_S
    assert 0.05 < np.abs(old - exact).min() * DAY_S and np.abs(old - exact).max() * DAY_S < 0.3, (old - exact) * DAY_S
    assert iso_from_jd(CTX, exact[1])[:19] == '2012-06-05T22:24:40'


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS  {name}')
            except Exception as exc:     # an error fails this test only
                failed += 1
                print(f'FAIL  {name}: {exc!r}')
    sys.exit(1 if failed else 0)

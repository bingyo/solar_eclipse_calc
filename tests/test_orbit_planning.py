"""Checks of the aids for satellites that are not launched yet: the
sun-synchronous inclination, the local time of the ascending node (LTAN)
and the search over all positions along the orbit (mean-anomaly sweep).

Run with  python tests/test_orbit_planning.py  (test_hinode_ltan needs
internet on the first run).
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc import server  # noqa: E402
from eclipsecalc.constants import WGS84_A  # noqa: E402
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.observers import (ObserverError, SSCWebObserver, build_observer,  # noqa: E402
                                   ltan_from_raan, mean_equator_matrix, mean_sun_ra_deg,
                                   sun_synchronous_inclination)
from eclipsecalc.timeutil import parse_utc  # noqa: E402

CTX = get_context()
PLAN = {'type': 'kepler', 'perigee_alt_km': 680, 'apogee_alt_km': 680, 'sso': True,
        'ltan_h': 18.0, 'argp_deg': 0, 'j2': True, 'name': 'plan'}


def test_mean_sun():
    # Mean longitude of the Sun at J2000.0 is 280.46 deg.
    assert abs(mean_sun_ra_deg(CTX, 2451545.0) - 280.461) < 0.01


def test_sun_synchronous_inclination():
    # Standard values: 700 km -> 98.19 deg, 800 km -> 98.60 deg.
    assert abs(sun_synchronous_inclination(WGS84_A + 700) - 98.19) < 0.01
    assert abs(sun_synchronous_inclination(WGS84_A + 800) - 98.60) < 0.02


def test_ltan_stays_fixed_for_sso():
    obs = build_observer(CTX, dict(PLAN, epoch='2027-01-01T00:00:00', ltan_h=10.5),
                         parse_time=lambda s: parse_utc(CTX, s))
    vf, jd0 = obs.vf, obs.params['epoch_jd_tt']
    assert abs(obs.params['ltan_h'] - 10.5) < 1e-6
    for days in (90, 180, 365):      # the node turns on the mean equator of the epoch
        raan = math.degrees(vf.raan0 + vf.raan_dot * days * 86400.0) % 360
        assert abs(ltan_from_raan(CTX, jd0 + days, raan, ref_jd=jd0) - 10.5) < 0.02, days


def test_hinode_ltan():
    # Hinode flies a dawn-dusk sun-synchronous orbit (ascending node ~18 h).
    for date in ('2007-03-19T02:00', '2011-01-04T09:00', '2017-08-21T17:00'):
        jd = parse_utc(CTX, date)
        obs = SSCWebObserver(CTX, 'hinode')
        obs.prepare(CTX, [(jd - 0.01, jd + 0.01)])
        st = obs.vf.at(CTX.ts.tt_jd(jd))
        r, v = st.position.km, st.velocity.km_per_s
        h = np.cross(r, v)
        raan = math.degrees(math.atan2(h[0], -h[1])) % 360
        inc = math.degrees(math.acos(h[2] / np.linalg.norm(h)))
        assert abs(ltan_from_raan(CTX, jd, raan) - 18.0) < 0.5, date
        assert abs(inc - sun_synchronous_inclination(np.linalg.norm(r))) < 0.3, date


def test_phase_sweep_total_eclipse_2027():
    # Total eclipse of 2027-08-02 seen from a planned 680 km dawn-dusk orbit:
    # a partial eclipse is certain, totality depends on the position along the orbit.
    req = server.SweepRequest(phenomena=['moon'], step_deg=10, start='2027-08-02', end='2027-08-03',
                              observer=dict(PLAN, epoch='2027-08-02T00:00:00'), settings={})
    r = server.phase_sweep(req)
    assert len(r['groups']) == 1
    g = r['groups'][0]
    assert g['n_visible'] == g['n_phases'] == 36
    assert g['count_min'] >= 2
    assert g['mag_min'] < 0.5 and g['mag_max'] > 1.0
    assert 0 < len(g['central_phases']) < 6
    # The sweep must agree with an ordinary search at the same mean anomaly.
    m = g['central_phases'][0]
    best = next(row['best'] for row in g['rows'] if row['m_deg'] == m)
    s = server.search(server.SearchRequest(phenomena=['moon'], start='2027-08-02', end='2027-08-03',
                                           observer=dict(PLAN, epoch='2027-08-02T00:00:00', m_deg=m),
                                           settings={}))
    e = max(s['events'], key=lambda e: e['magnitude'])
    assert e['type'] == best['type'] and abs(e['magnitude'] - best['magnitude']) < 1e-9
    assert e['max'] == best['max']


def test_input_checks():
    # Values that are not numbers (JSON allows NaN and Infinity) are refused with a message
    # instead of giving empty or meaningless results; an eccentricity of 1 or more is called so.
    kep = {'type': 'kepler', 'epoch_jd_tt': 2461000.5, 'a_km': 7000.0, 'e': 0.0, 'i_deg': 98.0, 'raan_deg': 0.0}
    for spec, key in (({'type': 'ground', 'lat': 35, 'lon': 139, 'elevation_m': math.inf}, '標高'),
                      ({'type': 'geo', 'lon': math.nan}, '経度'),
                      (dict(kep, e=1.2), '離心率'),
                      (dict(kep, a_km=math.nan), '数値')):
        try:
            build_observer(CTX, spec)
            assert False, spec
        except ObserverError as exc:
            assert key in str(exc), (spec, exc)
    # perigee and apogee given the other way round are the same orbit
    o = build_observer(CTX, {'type': 'kepler', 'epoch_jd_tt': 2461000.5, 'perigee_alt_km': 800,
                             'apogee_alt_km': 500, 'i_deg': 98.0, 'raan_deg': 0.0}).describe()
    assert abs(o['perigee_km'] - 500) < 1e-6 and abs(o['apogee_km'] - 800) < 1e-6


def test_secular_rates():
    # Secular rates given with the elements (mean motion, rates of the node and of the perigee)
    # replace those of the J2 model, and the orbit follows them; the rates not given keep the model.
    kep = {'type': 'kepler', 'epoch_jd_tt': 2461000.5, 'a_km': 7078.137, 'e': 0.0, 'i_deg': 98.19,
           'raan_deg': 30.0, 'argp_deg': 90.0, 'm_deg': 10.0}
    keys = ('n_rev_day', 'raan_rate_deg_day', 'argp_rate_deg_day')
    ref = build_observer(CTX, kep)
    d = ref.describe()
    assert d['rate_source'] == dict.fromkeys(keys, 'j2')
    assert build_observer(CTX, dict(kep, j2=False)).describe()['rate_source'] == dict.fromkeys(keys, 'two_body')
    t = CTX.ts.tt_jd(2461000.5 + np.array([0.0, 3.7, 30.2]))
    same = build_observer(CTX, dict(kep, **{k: d[k] for k in keys}))      # the model's own rates as numbers
    assert np.abs(same.vf.at(t).position.km - ref.vf.at(t).position.km).max() < 1e-6
    rates = {'n_rev_day': 14.6, 'raan_rate_deg_day': 0.97, 'argp_rate_deg_day': -1.8}
    obs = build_observer(CTX, dict(kep, **rates))
    o = obs.describe()
    assert o['rate_source'] == dict.fromkeys(keys, 'input') and all(abs(o[k] - v) < 1e-12 for k, v in rates.items())
    assert abs(o['period_min'] - 1440 / 14.6) < 1e-9 and not obs.notes
    # 10 days later: the node and the argument of latitude (circular orbit: argp + M) from the
    # position, on the mean equator of the epoch in which the rates are measured
    days = 10.0
    frame = mean_equator_matrix(CTX, 2461000.5)
    st = obs.vf.at(CTX.ts.tt_jd(2461000.5 + days))
    r, v = frame @ st.position.km, frame @ st.velocity.km_per_s
    h = np.cross(r, v)
    h /= np.linalg.norm(h)
    raan = math.radians(o['raan_date_deg'] + 0.97 * days)
    node = np.array([math.cos(raan), math.sin(raan), 0.0])
    assert abs(math.degrees(math.atan2(h[0], -h[1])) - math.degrees(raan)) < 1e-7
    u = math.degrees(math.atan2(np.cross(node, r) @ h, node @ r))
    want = o['argp_date_deg'] + 10.0 + (-1.8 + 14.6 * 360.0) * days
    assert abs((u - want + 180) % 360 - 180) < 1e-6, (u, want % 360)
    # a mean motion far from that of the semi-major axis: a warning (given with the results even
    # when nothing is found), and beyond 10 % an error; rates that are not numbers are refused
    n = d['n_rev_day']
    assert '周回/日' in build_observer(CTX, dict(kep, n_rev_day=n * 1.05)).notes[0]
    s = server.search(server.SearchRequest(phenomena=['moon'], start='2027-01-10', end='2027-01-11', settings={},
                                           observer=dict(kep, epoch='2027-01-01T00:00:00', n_rev_day=n * 1.05)))
    assert not s['events'] and any('周回/日' in w for w in s['warnings']), s['warnings']
    for spec, key in ((dict(kep, n_rev_day=n * 360), '周回/日'), (dict(kep, n_rev_day=0), '正の値'),
                      (dict(kep, n_rev_day='15 rev'), '数値'), (dict(kep, raan_rate_deg_day=math.nan), '数値')):
        try:
            build_observer(CTX, spec)
            assert False, spec
        except ObserverError as exc:
            assert key in str(exc), (spec, exc)
    # empty values (as from the web UI) leave the rates to the model
    assert build_observer(CTX, dict(kep, n_rev_day='', raan_rate_deg_day=None)).describe()['rate_source'] == d['rate_source']


def test_j2_about_pole_of_date():
    # J2 turns the orbit about the mean pole of the epoch (0.15 deg from the J2000 pole in 2027):
    # the inclination to that equator stays, the one to the J2000 equator does not.
    kep = {'type': 'kepler', 'epoch': '2027-08-01T00:00:00', 'a_km': 6798.0, 'e': 0.0005, 'i_deg': 51.6,
           'raan_deg': 120.0, 'argp_deg': 60.0, 'm_deg': 0.0}
    jd0 = parse_utc(CTX, kep['epoch'])
    obs = build_observer(CTX, kep, parse_time=lambda s: parse_utc(CTX, s))
    pole = mean_equator_matrix(CTX, jd0)[2]
    assert 0.15 < math.degrees(math.acos(pole[2])) < 0.16
    st = obs.vf.at(CTX.ts.tt_jd(jd0 + np.linspace(0, 60, 121)))
    h = np.cross(st.position.km.T, st.velocity.km_per_s.T)
    h /= np.linalg.norm(h, axis=1)[:, None]
    to_pole, to_z = np.degrees(np.arccos(h @ pole)), np.degrees(np.arccos(h[:, 2]))
    o = obs.describe()
    assert np.ptp(to_pole) < 1e-9 and abs(to_pole[0] - o['i_date_deg']) < 1e-9 and np.ptp(to_z) > 0.25
    assert o['i_deg'] == 51.6 and abs(o['i_date_deg'] - 51.6) > 0.1        # the elements given stay as given
    # A planned orbit: the sun-synchronous inclination and the node of a local time are of the equator
    # of date; the numbers given with them are of the J2000 equator.
    plan = {'type': 'kepler', 'epoch': kep['epoch'], 'perigee_alt_km': 680, 'apogee_alt_km': 680, 'argp_deg': 0}
    i_sso = sun_synchronous_inclination(WGS84_A + 680)
    for extra, want in (({'sso': True, 'ltan_h': 18.0}, {'i_date_deg': i_sso, 'ltan_h': 18.0}),
                        ({'sso': True, 'raan_deg': 77.0}, {'i_date_deg': i_sso, 'raan_deg': 77.0}),
                        ({'i_deg': 97.0, 'ltan_h': 10.5}, {'i_deg': 97.0, 'ltan_h': 10.5}),
                        ({'i_deg': 97.0, 'raan_deg': 77.0}, {'i_deg': 97.0, 'raan_deg': 77.0})):
        o = build_observer(CTX, dict(plan, **extra), parse_time=lambda s: parse_utc(CTX, s)).describe()
        assert all(abs(o[k] - v) < 1e-9 for k, v in want.items()), (extra, o)


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

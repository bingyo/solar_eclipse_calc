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
from eclipsecalc.observers import (SSCWebObserver, build_observer, ltan_from_raan,  # noqa: E402
                                   mean_sun_ra_deg, sun_synchronous_inclination)
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
    for days in (90, 180, 365):
        raan = math.degrees(vf.raan0 + vf.raan_dot * days * 86400.0) % 360
        assert abs(ltan_from_raan(CTX, jd0 + days, raan) - 10.5) < 0.02, days


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


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS  {name}')
            except AssertionError as exc:
                failed += 1
                print(f'FAIL  {name}: {exc}')
    sys.exit(1 if failed else 0)

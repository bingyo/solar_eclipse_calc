"""Validation against published NASA (Espenak/Meeus) predictions.

Run with either
    python -m pytest tests
or
    python tests/test_validation.py

NASA values use a solar radius of 959.63" at 1 AU, the two-k lunar radius
convention and planetary semi-diameters of 3.36" / 8.41" at 1 AU, so the
"NASA compatible" parameter preset is used where contact times are compared.
Small differences (< 2 s) also come from Delta T and ephemeris versions.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc.constants import (MERCURY_RADIUS_NASA, SUN_RADIUS_NASA,  # noqa: E402
                                   VENUS_RADIUS_NASA)
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.eclipse_map import solar_eclipse_map  # noqa: E402
from eclipsecalc.global_eclipse import search_global  # noqa: E402
from eclipsecalc.local import LocalSearch, Params  # noqa: E402
from eclipsecalc.observers import GeocenterObserver, GroundObserver  # noqa: E402
from eclipsecalc.timeutil import parse_utc  # noqa: E402

CTX = get_context()
NASA = Params(sun_radius_km=SUN_RADIUS_NASA, mercury_radius_km=MERCURY_RADIUS_NASA,
              venus_radius_km=VENUS_RADIUS_NASA)


def seconds(iso):
    h, m, s = iso[11:23].split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def close_time(iso, ref, tol_s):
    d = seconds(iso) - seconds(ref)
    d = (d + 43200) % 86400 - 43200
    assert abs(d) <= tol_s, f'{iso} vs {ref}: {d:+.2f} s'


def _global(a, b, params=NASA):
    return search_global(CTX, parse_utc(CTX, a), parse_utc(CTX, b), params)


def _geocentric(body, a, b, params=NASA):
    ev = LocalSearch(CTX, GeocenterObserver(), body, params,
                     parse_utc(CTX, a), parse_utc(CTX, b)).run()
    assert len(ev) == 1
    return ev[0]


def test_21st_century_statistics():
    # NASA Five Millennium Canon: 2001-2100 has 224 solar eclipses
    # (77 partial, 72 annular, 68 total, 7 hybrid).
    ev = _global('2001-01-01', '2101-01-01', Params())
    kinds = {}
    for e in ev:
        kinds[e['type']] = kinds.get(e['type'], 0) + 1
    assert kinds == {'partial': 77, 'annular': 72, 'total': 68, 'hybrid': 7}, kinds


def test_global_2017_total():
    e = _global('2017-08-01', '2017-09-01')[0]
    assert e['type'] == 'total' and e['saros'] == 145
    assert abs(e['gamma'] - 0.4367) < 2e-4
    assert abs(e['magnitude'] - 1.0306) < 2e-4
    assert abs(e['central_duration_s'] - 160) < 1.5          # 2m40s
    assert abs(e['path_width_km'] - 115) < 2
    assert abs(e['ge_lat'] - 36.967) < 0.02 and abs(e['ge_lon'] + 87.672) < 0.03


def test_global_2024_total():
    e = _global('2024-04-01', '2024-05-01')[0]
    assert e['type'] == 'total' and e['saros'] == 139
    assert abs(e['gamma'] - 0.3431) < 2e-4
    assert abs(e['magnitude'] - 1.0566) < 2e-4
    assert abs(e['central_duration_s'] - 268) < 1.5          # 4m28s
    assert abs(e['path_width_km'] - 197.5) < 2


def test_global_2023_hybrid_and_annular():
    ev = _global('2023-04-01', '2023-11-01')
    h, a = ev
    assert h['type'] == 'hybrid' and abs(h['magnitude'] - 1.0132) < 2e-4
    assert abs(h['central_duration_s'] - 76) < 1.5            # 1m16s
    assert a['type'] == 'annular' and abs(a['magnitude'] - 0.9520) < 2e-4
    assert abs(a['central_duration_s'] - 317) < 1.5           # 5m17s


def test_venus_transit_2012():
    e = _geocentric('venus', '2012-06-01', '2012-06-10')
    c = {x['label']: x for x in e['contacts']}
    close_time(c['C1']['time'], '2012-06-05T22:09:38', 1.5)
    close_time(c['C2']['time'], '2012-06-05T22:27:34', 1.5)
    close_time(c['MAX']['time'], '2012-06-06T01:29:36', 1.5)
    close_time(c['C3']['time'], '2012-06-06T04:31:39', 1.5)
    close_time(c['C4']['time'], '2012-06-06T04:49:35', 1.5)
    assert abs(e['min_sep_arcsec'] - 554.4) < 0.5
    assert abs(c['C1']['pa'] - 41) < 1.0


def test_venus_transit_2004():
    e = _geocentric('venus', '2004-06-01', '2004-06-10')
    c = {x['label']: x for x in e['contacts']}
    close_time(c['C1']['time'], '2004-06-08T05:13:29', 1.5)
    close_time(c['MAX']['time'], '2004-06-08T08:19:44', 1.5)
    close_time(c['C4']['time'], '2004-06-08T11:25:59', 1.5)


def test_mercury_transit_2019():
    e = _geocentric('mercury', '2019-11-01', '2019-11-30')
    c = {x['label']: x for x in e['contacts']}
    # Espenak, RASC Observer's Handbook 2019 (eclipsewise.com/oh/tm2019.html)
    close_time(c['C1']['time'], '2019-11-11T12:35:27', 1.0)
    close_time(c['C2']['time'], '2019-11-11T12:37:08', 1.0)
    close_time(c['MAX']['time'], '2019-11-11T15:19:48', 1.0)
    close_time(c['C3']['time'], '2019-11-11T18:02:33', 1.0)
    close_time(c['C4']['time'], '2019-11-11T18:04:14', 1.0)
    assert abs(e['min_sep_arcsec'] - 75.9) < 0.1
    assert abs(c['C1']['pa'] - 109.8) < 0.3 and abs(c['C4']['pa'] - 298.7) < 0.3


def test_path_limit_consistency():
    """The umbral limit from the shadow-cone/envelope model must agree with
    the independent Skyfield topocentric engine to well under 100 m."""
    p = Params()
    m = solar_eclipse_map(CTX, parse_utc(CTX, '2024-04-08T18:00'), p)
    north = m['umbra_limits']['N'][0]
    central = np.array([c[:2] for c in m['central_line'][0]])
    pt = north[len(north) // 2]
    k = np.argmin(np.hypot(central[:, 0] - pt[0], (central[:, 1] - pt[1]) * np.cos(np.radians(pt[0]))))
    c = central[k]
    span_km = np.hypot(c[0] - pt[0], (c[1] - pt[1]) * np.cos(np.radians(pt[0]))) * 111.2
    for km_inside, expect_total in ((-0.1, False), (0.1, True)):
        f = km_inside / span_km
        la, lo = pt[0] + f * (c[0] - pt[0]), pt[1] + f * (c[1] - pt[1])
        ev = LocalSearch(CTX, GroundObserver(la, lo, 0), 'moon', p, pt[2] - 0.2, pt[2] + 0.2).run()[0]
        assert (ev['type'] == 'total') == expect_total, (km_inside, ev['type'])


def test_saros_numbers():
    ev = _global('2019-01-01', '2028-01-01', Params())
    got = {e['max'][:10]: e['saros'] for e in ev}
    expected = {'2019-07-02': 127, '2020-12-14': 142, '2023-04-20': 129, '2024-04-08': 139,
                '2026-08-12': 126, '2027-08-02': 136}
    for d, s in expected.items():
        assert got[d] == s, (d, got[d], s)


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

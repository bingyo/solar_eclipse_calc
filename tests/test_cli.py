"""Checks of the command-line tool (cli.py): for every kind of observer it
must give the same events as the web UI's search with the same input.

Run with  python tests/test_cli.py  (no internet needed).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import cli  # noqa: E402
from eclipsecalc import server  # noqa: E402

PLAN = {'type': 'kepler', 'perigee_alt_km': 680, 'apogee_alt_km': 680, 'sso': True,
        'ltan_h': 18.0, 'argp_deg': 0, 'j2': True, 'name': 'plan'}
# ISS, 2008-09-20 (the example TLE of the SGP4 documentation)
ISS_TLE = ('ISS (ZARYA)\n'
           '1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927\n'
           '2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537\n')


def _run_cli(argv):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'r.json')
        assert cli.main(argv + ['--format', 'json', '-o', out]) == 0
        with open(out, encoding='utf-8') as f:
            return json.load(f)


def _same_events(got, ref, n_min):
    assert len(got['events']) == len(ref['events']) >= n_min
    for a, b in zip(got['events'], ref['events']):
        assert a['type'] == b['type'] and a['max'] == b['max'] and a['c1'] == b['c1']
        assert abs(a['magnitude'] - b['magnitude']) < 1e-9
        assert a['vis_fraction'] == b['vis_fraction']


def test_six_elements():
    # The planned orbit typed in as six elements must give the same events as
    # the altitude / LTAN input of the web UI.
    plan = dict(PLAN, epoch='2027-08-02T00:00:00', m_deg=40)
    kw = dict(phenomena=['moon'], start='2027-08-02', end='2027-08-03', settings={})
    ref = server.search(server.SearchRequest(observer=plan, **kw))
    o = ref['observer']
    got = _run_cli(['--epoch', plan['epoch'], '--a', repr(o['a_km']), '--e', repr(o['e']),
                    '--i', repr(o['i_deg']), '--raan', repr(o['raan_deg']), '--argp', '0',
                    '--m', '40', '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    _same_events(got, ref, 2)


def test_tle_file():
    kw = dict(phenomena=['moon'], start='2008-07-25', end='2008-08-05', settings={})
    lines = ISS_TLE.splitlines()
    ref = server.search(server.SearchRequest(
        observer={'type': 'tle', 'line1': lines[1], 'line2': lines[2], 'name': lines[0]}, **kw))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'iss.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(ISS_TLE)
        got = _run_cli(['--tle', path, '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    assert got['observer']['name'] == 'ISS (ZARYA)'
    _same_events(got, ref, 1)


def test_ground_city():
    # --city takes the position from the presets; Luxor sees 6 min 23 s of totality in 2027.
    kw = dict(phenomena=['moon'], start='2027-08-01', end='2027-08-03', settings={})
    ref = server.search(server.SearchRequest(
        observer={'type': 'ground', 'lat': 25.6872, 'lon': 32.6396, 'elevation_m': 80}, **kw))
    got = _run_cli(['--city', 'ルクソール', '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    _same_events(got, ref, 1)
    e = got['events'][0]
    assert e['type'] == 'total' and abs(e['central_duration_s'] - 382.6) < 0.5


def test_ground_settings():
    # Ground-only settings reach the search: Tokyo, Mercury transit of 2032 at sunset.
    kw = dict(phenomena=['mercury'], start='2032-11-01', end='2032-11-30')
    obs = {'type': 'ground', 'lat': 35.6812, 'lon': 139.7671, 'elevation_m': 40}
    ref = server.search(server.SearchRequest(observer=obs, settings={'min_sun_alt_deg': 5.0,
                                                                      'refraction': False}, **kw))
    got = _run_cli(['--lat', '35.6812', '--lon', '139.7671', '--elev', '40', '--min-sun-alt', '5',
                    '--no-refraction', '--start', kw['start'], '--end', kw['end'],
                    '--phenomena', 'mercury'])
    _same_events(got, ref, 1)
    assert got['params']['min_sun_alt_deg'] == 5.0 and got['params']['refraction'] is False


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

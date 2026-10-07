"""Checks of the command-line tool (cli.py): for every kind of observer it
must give the same events as the web UI's search with the same input, and
the JSON interface used by AI agents (request echo/replay, dry run, errors
and exit codes) must keep its shape.

Run with  python tests/test_cli.py  (no internet needed).
"""
import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import cli  # noqa: E402
from eclipsecalc import server  # noqa: E402
from eclipsecalc.observers import tle_with_mean_anomaly  # noqa: E402

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



def test_planned_orbit_flags():
    # --alt --sso --ltan are the web UI's "高度・太陽同期で指定"
    plan = dict(PLAN, epoch='2027-08-02T00:00:00', m_deg=40)
    kw = dict(phenomena=['moon'], start='2027-08-02', end='2027-08-03', settings={})
    ref = server.search(server.SearchRequest(observer=plan, **kw))
    got = _run_cli(['--epoch', plan['epoch'], '--alt', '680', '--sso', '--ltan', '18:00', '--m', '40',
                    '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    _same_events(got, ref, 2)
    assert got['observer']['sso'] and abs(got['observer']['ltan_h'] - 18.0) < 1e-9


def test_geostationary_and_global():
    kw = dict(phenomena=['moon'], start='2026-01-01', end='2027-01-01', settings={})
    ref = server.search(server.SearchRequest(observer={'type': 'geo', 'lon': 140.7}, **kw))
    got = _run_cli(['--geo-lon', '140.7', '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    _same_events(got, ref, 3)
    kw = dict(phenomena=['moon'], start='2027-01-01', end='2028-01-01', settings={})
    ref = server.search(server.SearchRequest(observer={'type': 'global'}, **kw))
    got = _run_cli(['--global', '--start', kw['start'], '--end', kw['end'], '--phenomena', 'moon'])
    assert [e['max'] for e in got['events']] == [e['max'] for e in ref['events']]
    e = next(e for e in got['events'] if e['type'] == 'total')
    r = next(r for r in ref['events'] if r['max'] == e['max'])
    assert e['max'].startswith('2027-08-02') and abs(e['gamma'] - r['gamma']) < 1e-12


def test_request_replay_and_detail():
    # The "request" echoed in the JSON output reproduces the calculation with --request.
    first = _run_cli(['--city', 'ルクソール', '--start', '2027-08-01', '--end', '2027-08-03',
                      '--phenomena', 'moon', '--detail'])
    assert first['ok'] and 'id' not in first['events'][0]
    labels = [c['label'] for c in first['events'][0]['contacts']]
    assert labels == ['C1', 'C2', 'MAX', 'C3', 'C4'], labels
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'req.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(first['request'], f, ensure_ascii=False)
        again = _run_cli(['--request', path])
    assert again['request'] == first['request']
    _same_events(again, first, 1)


def _cli_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv + ['--format', 'json'])
    return code, json.loads(out.getvalue())


def test_dry_run():
    code, d = _cli_json(['--epoch', '2027-01-01', '--alt', '500', '--sso', '--ltan', '10:30',
                         '--start', '2027-01-01', '--end', '2027-02-01', '--dry-run'])
    o = d['observer']
    assert code == 0 and d['ok'] and d['dry_run'] and 'events' not in d
    assert abs(o['period_min'] - 94.6) < 0.2 and abs(o['i_deg'] - 97.4) < 0.05 and abs(o['ltan_h'] - 10.5) < 1e-6


def test_errors_and_exit_codes():
    # wrong options -> 2, input that cannot be calculated -> 1; always JSON with --format json
    code, d = _cli_json(['--city', '東京', '--start', '2027-01-01'])
    assert code == 2 and d == {'ok': False, 'error': d['error'], 'exit_code': 2} and '--end' in d['error']
    code, d = _cli_json(['--city', '東京', '--lat', '35', '--start', '2027-01-01', '--end', '2027-02-01'])
    assert code == 2
    code, d = _cli_json(['--epoch', '2027-01-01', '--a', '6000', '--e', '0', '--i', '98', '--raan', '0',
                         '--start', '2027-01-01', '--end', '2027-02-01'])
    assert code == 1 and '近地点' in d['error']
    code, d = _cli_json(['--epoch', '2027-13-01', '--a', '7000', '--e', '0', '--i', '98', '--raan', '0',
                         '--start', '2027-01-01', '--end', '2027-02-01'])
    assert code == 1 and '元期' in d['error']
    code, d = _cli_json(['--city', '東京', '--start', '2027-02-29', '--end', '2027-03-01'])
    assert code == 1 and '開始日' in d['error']
    code, d = _cli_json(['--city', '東京', '--start', '2027-02-01', '--end', '2027-01-01'])
    assert code == 1 and not d['ok']


def test_tle_dry_run():
    # The TLE's own elements and the warning about its age (judged at the far end
    # of the period) are shown before calculating.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'iss.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(ISS_TLE)
        code, d = _cli_json(['--tle', path, '--start', '2008-09-20', '--end', '2008-12-20', '--dry-run'])
        o = d['observer']
        assert code == 0 and o['norad'] == 25544
        for k, v in (('i_deg', 51.6416), ('raan_deg', 247.4627), ('e', 0.0006703), ('argp_deg', 130.5360),
                     ('m_deg', 325.0288), ('mean_motion_rev_per_day', 15.72125391)):
            assert abs(o[k] - v) < 1e-6, (k, o[k])
        assert len(d['warnings']) == 1 and ' 90 日' in d['warnings'][0], d['warnings']
        code, d = _cli_json(['--tle', path, '--start', '2008-09-20', '--end', '2009-12-20', '--sweep', '30',
                             '--dry-run'])
        assert code == 1 and '1 年以内' in d['error']


def test_tle_with_mean_anomaly():
    line2 = ISS_TLE.splitlines()[2]
    assert tle_with_mean_anomaly(line2, 325.0288) == line2          # same checksum as the original
    moved = tle_with_mean_anomaly(line2, 370.0)
    assert moved[43:51] == ' 10.0000' and moved[:43] == line2[:43] and moved[51:68] == line2[51:68]
    assert int(moved[68]) == sum(int(c) if c.isdigit() else c == '-' for c in moved[:68]) % 10


def test_tle_sweep():
    # Mean-anomaly sweep of a TLE: each phase must agree with an ordinary search
    # of the TLE with that mean anomaly.
    lines = ISS_TLE.splitlines()
    tle = {'type': 'tle', 'line1': lines[1], 'line2': lines[2], 'name': lines[0]}
    kw = dict(phenomena=['moon'], start='2008-07-25', end='2008-08-05', settings={})
    r = server.phase_sweep(server.SweepRequest(observer=tle, step_deg=45, **kw))
    assert abs(r['observer']['m_deg'] - 325.0288) < 1e-9 and r['warnings']
    g = r['groups'][0]
    assert g['n_phases'] == 8 and g['n_visible'] >= 1 and g['count_max'] >= 1
    row = next(row for row in g['rows'] if row['best'])
    s = server.search(server.SearchRequest(observer=dict(tle, line2=tle_with_mean_anomaly(lines[2], row['m_deg'])),
                                           **kw))
    e = max(s['events'], key=lambda e: e['magnitude'])
    assert e['max'] == row['best']['max'] and abs(e['magnitude'] - row['best']['magnitude']) < 1e-9
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'iss.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(ISS_TLE)
        got = _run_cli(['--tle', path, '--sweep', '45', '--start', kw['start'], '--end', kw['end'],
                        '--phenomena', 'moon'])
    assert [x['best'] for x in got['groups'][0]['rows']] == _strip(g['rows'])


def _strip(rows):
    return cli._strip_ids([x['best'] for x in rows])

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

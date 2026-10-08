"""Checks of the command-line tool (cli.py): for every kind of observer it
must give the same events as the web UI's search with the same input, and
the JSON interface used by AI agents (request echo/replay, dry run, errors
and exit codes) must keep its shape, and every language must work.

Run with  python tests/test_cli.py  (no internet needed).
"""
import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import cli  # noqa: E402
from eclipsecalc import i18n, server  # noqa: E402
from eclipsecalc.observers import tle_with_mean_anomaly  # noqa: E402

PLAN = {'type': 'kepler', 'perigee_alt_km': 680, 'apogee_alt_km': 680, 'sso': True,
        'ltan_h': 18.0, 'argp_deg': 0, 'j2': True, 'name': 'plan'}
# ISS, 2008-09-20 (the example TLE of the SGP4 documentation)
ISS_TLE = ('ISS (ZARYA)\n'
           '1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927\n'
           '2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537\n')
# a 344 km orbit with strong drag: SGP4 has it re-enter in May 2025
DECAY_TLE = ('1 99999U          24245.00000000  .00000000  00000-0  40000-3 0    02\n'
             '2 99999  97.0000   0.0000 0001000  90.0000   0.0000 15.75223245    09\n')


def _run_cli(argv):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'r.json')
        assert cli.main(argv + ['--format', 'json', '-o', out, '--lang', 'ja']) == 0
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
        with open(path, 'w', encoding='utf-8') as f:       # the whole output works as well
            json.dump(first, f, ensure_ascii=False)
        whole = _run_cli(['--request', path])
    assert again['request'] == first['request'] == whole['request']
    _same_events(again, first, 1)


def _cli_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv + ['--format', 'json'] + ([] if '--lang' in argv else ['--lang', 'ja']))
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
    for argv in (['--geo-lon', 'nan'], ['--city', '東京', '--min-sun-alt', 'inf'],      # not numbers
                 ['--geo-lon', '140', '--elev', '5'], ['--city', '東京', '--horizons-step', '2']):  # do not apply
        code, d = _cli_json(argv + ['--start', '2027-01-01', '--end', '2027-02-01', '--dry-run'])
        assert code == 2, (argv, d)
    code, d = _cli_json(['--city', '東京', '--tz', '-05:00', '--start', '2027-01-01', '--end', '2027-02-01',
                         '--dry-run'])
    assert code == 0, d
    with tempfile.TemporaryDirectory() as tmp:
        req, out = os.path.join(tmp, 'req.json'), os.path.join(tmp, 'out.json')
        with open(req, 'w', encoding='utf-8') as f:
            f.write('{"observer": {"type": "geo", "lon": NaN}, "start": "2027-01-01", "end": "2027-02-01"}')
        code, d = _cli_json(['--request', req])
        assert code == 1 and 'NaN' in d['error'], d
        code, d = _cli_json(['--request', req, '--m', '10'])
        assert code == 2, d
        # a failed run does not leave an earlier result in the -o file
        with contextlib.redirect_stderr(io.StringIO()):
            assert cli.main(['--city', '東京', '--start', '2027-01-01', '--end', '2027-02-01', '--dry-run',
                             '--format', 'json', '-o', out]) == 0
        assert _cli_json(['--city', '東京', '--start', '2027-02-01', '--end', '2027-01-01', '-o', out])[0] == 1
        with open(out, encoding='utf-8') as f:
            assert json.load(f)['ok'] is False


def test_formatting():
    # durations round to the nearest second (minute above an hour) without "60" carrying over
    tok = i18n.set_lang('ja')
    try:
        assert [cli._dur(s) for s in (59.6, 119.6, 3599.6, 5430.2)] == ['1分00秒', '2分00秒', '1時間00分', '1時間31分']
        assert cli._dur(59.96, True) == '1分00.0秒' and cli._dur(382.6, True) == '6分22.6秒'
        # a time on another day than the maximum (local time) shows its date
        assert cli._time_on('2027-08-02T08:40:18Z', '2027-08-02T10:05:18Z', -10) == '08-01 22:40:18'
        assert cli._time_on('2027-08-02T11:26:33Z', '2027-08-02T10:05:18Z', -10) == '01:26:33'
        assert cli.observer_text({'kind': 'geocenter', 'name': '地心'}) == '地心'
    finally:
        i18n.reset_lang(tok)


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


def test_tle_decay_and_checksum():
    l1, l2 = DECAY_TLE.splitlines()
    obs = {'type': 'tle', 'line1': l1, 'line2': l2}
    r = server.search(server.SearchRequest(observer=obs, phenomena=['moon'], start='2024-09-01', end='2025-09-01'))
    assert any('2025-05' in w for w in r['warnings']), r['warnings']
    assert r['events'] and all(e['max'] < '2025-05' for e in r['events'])
    try:
        server.search(server.SearchRequest(observer=obs, phenomena=['moon'], start='2025-08-01', end='2025-09-01'))
        assert False, 'computed after the re-entry'
    except server.HTTPException as exc:
        assert exc.status_code == 400 and '2025-05' in exc.detail
    with tempfile.TemporaryDirectory() as tmp:        # --dry-run tells the same
        path = os.path.join(tmp, 'decay.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(DECAY_TLE)
        code, d = _cli_json(['--tle', path, '--start', '2024-09-01', '--end', '2025-09-01', '--dry-run'])
        assert code == 0 and any('2025-05' in w for w in d['warnings']), d
        code, d = _cli_json(['--tle', path, '--start', '2025-08-01', '--end', '2025-09-01', '--dry-run'])
        assert code == 1 and '2025-05' in d['error'], d
    # a mistyped TLE (the checksum does not match) is computed, with a warning
    lines = ISS_TLE.splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'typo.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join([lines[0], lines[1], lines[2][:19] + '8' + lines[2][20:]]))
        code, d = _cli_json(['--tle', path, '--start', '2008-09-20', '--end', '2008-09-25', '--dry-run'])
    assert code == 0 and any('チェックサム' in w for w in d['warnings']), d


def test_unreachable_service():
    # A service that cannot be reached is named in the message (502, exit status 1), not reported
    # as "an error occurred during the computation" (500).
    import urllib.error
    from eclipsecalc import observers

    def offline(*args, **kw):
        raise urllib.error.URLError('offline (test)')
    saved, observers._urlopen = observers._urlopen, offline
    try:
        for obs, service in (({'type': 'horizons', 'command': 'test-offline', 'step_min': 60}, 'JPL Horizons'),
                             ({'type': 'celestrak', 'norad': 25544}, 'CelesTrak')):
            try:
                server.search(server.SearchRequest(observer=obs, phenomena=['moon'], start='2027-01-01',
                                                   end='2027-02-01'))
                assert False, obs
            except server.HTTPException as exc:
                assert exc.status_code == 502 and service in exc.detail, (exc.status_code, exc.detail)
        code, d = _cli_json(['--horizons', 'test-offline', '--start', '2027-01-01', '--end', '2027-02-01'])
        assert code == 1 and 'JPL Horizons' in d['error'], d
    finally:
        observers._urlopen = saved


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


def _cli_text(argv, lang):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv + ['--lang', lang])
        except SystemExit as exc:       # --help
            code = exc.code
    return code, out.getvalue(), err.getvalue()


JAPANESE = re.compile(r'[ぁ-ゖァ-ヺ一-鿿]')     # kana and kanji


def test_languages():
    # In every language the help, the tables (ground with contacts, whole Earth, satellite and
    # sweep), CSV, dry run and errors work, and no Japanese is left outside ja and zh.
    with tempfile.TemporaryDirectory() as tmp:
        tle = os.path.join(tmp, 'iss.txt')
        with open(tle, 'w', encoding='utf-8') as f:
            f.write(ISS_TLE)
        runs = [
            ['--help'],
            ['--city', 'ルクソール', '--start', '2027-08-01', '--end', '2027-08-03', '--detail', '--tz', '2'],
            ['--global', '--start', '2027-01-01', '--end', '2028-01-01'],
            ['--global', '--start', '2032-11-01', '--end', '2032-11-30', '--phenomena', 'mercury', '--format', 'csv'],
            ['--tle', tle, '--sweep', '45', '--start', '2008-07-25', '--end', '2008-08-05', '--phenomena', 'moon'],
            ['--tle', tle, '--start', '2008-07-25', '--end', '2008-08-05', '--phenomena', 'moon', '--detail'],
            ['--epoch', '2027-01-01', '--alt', '500', '--sso', '--ltan', '10:30', '--start', '2027-01-01',
             '--end', '2027-02-01', '--dry-run'],
            ['--geo-lon', '140.7', '--start', '2026-01-01', '--end', '2027-01-01', '--phenomena', 'moon'],
            ['--list-cities'],
        ]
        errors = [(['--city', 'Tokyo', '--start', '2027-01-01'], 2), (['--a', 'x'], 2),
                  (['--city', 'Tokyo', '--format', 'xml'], 2),
                  (['--city', 'Atlantis', '--start', '2027-01-01', '--end', '2027-02-01'], 1),
                  (['--epoch', '2027-01-01', '--a', '6000', '--e', '0', '--i', '98', '--raan', '0',
                    '--start', '2027-01-01', '--end', '2027-02-01'], 1)]
        for lang in i18n.LANGUAGES:
            for argv in runs:
                code, out, err = _cli_text(argv, lang)
                assert code == 0, (lang, argv, err)
                if lang not in ('ja', 'zh'):
                    assert not JAPANESE.search(out + err), (lang, argv, ''.join(JAPANESE.findall(out + err)))
            for argv, want in errors:
                code, out, err = _cli_text(argv, lang)
                assert code == want, (lang, argv, err)
                if lang not in ('ja', 'zh'):
                    assert not JAPANESE.search(err), (lang, argv, err)
    code, d = _cli_json(['--city', 'Tokyo', '--start', '2027-01-01', '--end', '2027-02-01', '--dry-run',
                         '--lang', 'fr'])
    assert code == 0 and d['lang'] == 'fr' and d['request']['observer']['name'] == 'Tokyo'
    code, d = _cli_json(['--city', 'Tokyo', '--start', '2027-02-01', '--end', '2027-01-01', '--lang', 'en'])
    assert code == 1 and d['error'] == 'The end date must be after the start date', d


def test_city_in_any_language():
    for name in ('東京', 'Tokyo', 'токио', '东京', 'टोक्यो', 'tok'):
        assert cli._find_city(name)['name'] == '東京', name
    for name in ('ルクソール', 'Luxor', 'Louxor (Égypte)', 'луксор'):
        assert cli._find_city(name)['name'] == 'ルクソール (エジプト)', name


def test_choose_lang():
    names = (cli.LANG_ENV, 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG')
    saved = {k: os.environ.pop(k, None) for k in names}
    try:
        assert cli.choose_lang(['--lang', 'fr']) == 'fr' and cli.choose_lang(['--x', '--lang=hi']) == 'hi'
        os.environ[cli.LANG_ENV] = 'ru'
        assert cli.choose_lang([]) == 'ru' and cli.choose_lang(['--lang', 'es']) == 'es'
        assert cli.choose_lang(['--lang', 'xx']) == 'ru'      # argparse then rejects it, in Russian
        del os.environ[cli.LANG_ENV]
        os.environ['LANG'] = 'zh_CN.UTF-8'
        assert cli.choose_lang([]) == 'zh'
        os.environ['LANGUAGE'] = 'de:fr'
        assert cli.choose_lang([]) == 'fr'
        os.environ['LANG'] = 'C.UTF-8'
        assert cli.system_lang() is None
    finally:
        for k in names:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


def _cli_texts():
    """The Japanese texts of cli.py (each is the key of a message), docstrings aside."""
    tree = ast.parse(open(cli.__file__, encoding='utf-8').read())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and JAPANESE.search(n.value)}


def test_translations_complete():
    # Every message (of cli.py and of the web API) has all six translations with the same fields.
    from eclipsecalc.presets import CITIES
    fields = lambda s: sorted(re.findall(r'\{(\w*)', s))      # noqa: E731
    missing = (_cli_texts() | {c[0] for c in CITIES}) - set(i18n.MESSAGES)
    assert not missing, sorted(missing)
    for k, tr in i18n.MESSAGES.items():
        assert sorted(tr) == sorted(i18n.LANGUAGES[1:]), k
        for lang, v in tr.items():
            assert v and fields(v) == fields(k), (k, lang, v)
    assert all(len(v) == 16 for v in i18n.COMPASS.values()) and sorted(i18n.COMPASS) == sorted(i18n.LANGUAGES)


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS  {name}')
            except Exception as exc:
                failed += 1
                print(f'FAIL  {name}: {exc!r}')
    sys.exit(1 if failed else 0)

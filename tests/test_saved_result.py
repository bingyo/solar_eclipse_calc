"""Checks of opening a saved result in the web UI: the details of each event of
a saved list (and of a saved single event) are computed again with /api/restore
from the saved request, and must be the same event as when it was first computed.

Run with  python tests/test_saved_result.py  (no internet needed).
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc import server  # noqa: E402

ISS = ('1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927',
       '2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537')


def _t(iso):
    return dt.datetime.fromisoformat(iso.replace('Z', '+00:00'))


def _close(a, b, tol_s=0.01):
    assert abs((_t(a) - _t(b)).total_seconds()) < tol_s, (a, b)


def _saved(obj):
    '''What a saved file gives back: JSON without the server's ids.'''
    return json.loads(json.dumps(obj))


def _restore(request, event):
    return server.restore(server.RestoreRequest(request=_saved(request), event=_saved(event)))


def _same_local(got, ref):
    assert got['body'] == ref['body'] and got['type'] == ref['type']
    assert [c['label'] for c in got['contacts']] == [c['label'] for c in ref['contacts']]
    for a, b in zip(got['contacts'], ref['contacts']):
        _close(a['time'], b['time'])
    assert abs(got['magnitude'] - ref['magnitude']) < 1e-6
    assert abs(got['vis_fraction'] - ref['vis_fraction']) < 1e-6


def test_ground_list():
    req = {'phenomena': ['moon', 'mercury'], 'start': '2030-01-01', 'end': '2036-01-01',
           'observer': {'type': 'ground', 'lat': 35.6812, 'lon': 139.7671, 'elevation_m': 40, 'name': '東京'},
           'settings': {'refraction': True}}
    r = server.search(server.SearchRequest(**req))
    assert len(r['events']) >= 2
    for e in r['events']:
        ref = server.event_detail(e['id'])
        assert ref['request']['observer'] == req['observer']
        got = _restore(req, e)
        assert got['id'] != e['id'] and got['request']['observer'] == req['observer']
        _same_local(got, ref)
        # a single event saved from the detail view carries its own request
        again = _restore(ref['request'], ref)
        _same_local(again, ref)


def test_global_list():
    req = {'phenomena': ['moon', 'venus'], 'start': '2012-01-01', 'end': '2013-01-01',
           'observer': {'type': 'global'}, 'settings': {}}
    r = server.search(server.SearchRequest(**req))
    kinds = {(e['kind'], e['body']) for e in r['events']}
    assert ('global', 'moon') in kinds and ('local', 'venus') in kinds, kinds
    for e in r['events']:
        ref = server.event_detail(e['id'])
        got = _restore(req, e)
        assert got['kind'] == e['kind']
        if e['kind'] == 'global':
            for k in ('max', 'p1', 'p4'):
                _close(got[k], e[k])
            assert got['type'] == e['type'] and abs(got['gamma'] - e['gamma']) < 1e-6
        else:
            assert got['observer_kind'] == 'geocenter'
            _same_local(got, ref)
    # a place clicked on the map of a saved global eclipse
    g = next(e for e in r['events'] if e['kind'] == 'global')
    loc = server.local_at(server.LocalRequest(event_id=g['id'], lat=g['ge_lat'], lon=g['ge_lon']))
    assert loc['found'] and loc['request']['observer']['type'] == 'ground'
    _same_local(_restore(loc['request'], loc), loc)


def test_tle_sweep():
    tle = {'type': 'tle', 'line1': ISS[0], 'line2': ISS[1], 'name': 'ISS'}
    req = {'phenomena': ['moon'], 'start': '2008-07-25', 'end': '2008-08-05', 'observer': tle,
           'settings': {}, 'step_deg': 45}
    r = server.phase_sweep(server.SweepRequest(**req))
    rows = [row for row in r['groups'][0]['rows'] if row['best']]
    assert len(rows) >= 2
    for row in rows[:3]:
        e = row['best']
        ref = server.event_detail(e['id'])
        assert ref['request']['observer']['m_deg'] == row['m_deg']
        _same_local(_restore(req, e), ref)


def test_errors():
    for request, event in (({}, {'jd_max': 2461000.0}),
                           ({'observer': {'type': 'global'}}, {})):
        try:
            _restore(request, event)
        except server.HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError('no error')
    # no event at that time
    req = {'observer': {'type': 'ground', 'lat': 35, 'lon': 135}, 'start': '2030-01-01', 'end': '2031-01-01',
           'settings': {}}
    try:
        _restore(req, {'body': 'moon', 'jd_max': 2462700.0})
    except server.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError('no error')


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

"""Validation against eclipses and transits actually observed by the solar
satellite Hinode (SOLAR-B, NORAD 29479, ~680 km sun-synchronous orbit).

Hinode's historical orbit is taken from NASA SSCWeb (observer type
``sscweb``; positions every 60 s, consistent with SGP4/TLE to ~1 km).
Two kinds of reference data are used:

* Predictions by M. Soma (NAOJ) published by the Hinode team, computed from
  JAXA orbital elements issued 2-4 weeks before each event.  Because the
  orbits differ (atmospheric drag makes along-track prediction errors grow),
  contact times differ by an almost constant offset per event; the test
  checks that offset and, more strictly, the scatter around it, which
  reflects the geometry of the computation itself.
* Facts reported from the actual observations (eclipse magnitude, times of
  images taken during the eclipse, Earth-shadow interruptions).

Run with  python tests/test_hinode.py  (needs internet on the first run;
SSCWeb data are cached in data/cache/).  The image measurements that tie
the computation directly to the XRT/SOT pictures are in
tools/validate_hinode_images.py.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc.constants import MOON_K_EXTERNAL, WGS84_A  # noqa: E402
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.global_eclipse import search_global  # noqa: E402
from eclipsecalc.local import LocalSearch, Params  # noqa: E402
from eclipsecalc.observers import SSCWebObserver  # noqa: E402
from eclipsecalc.timeutil import parse_utc  # noqa: E402

CTX = get_context()
# Soma's figures use R_sun = 696,000 km (e.g. 975.89" on 2011-01-04) and a
# single lunar radius for all contacts.
SOMA_PARAMS = Params(include_invisible=True, sun_radius_km=696_000.0,
                     moon_radius_ext_km=MOON_K_EXTERNAL * WGS84_A,
                     moon_radius_int_km=MOON_K_EXTERNAL * WGS84_A)

HINODE = 'https://hinode.nao.ac.jp/news/topics/'
ISAS = 'https://www.isas.jaxa.jp/home/solar/'

# --- Soma (NAOJ) predictions, UTC --------------------------------------------
# tol: maximum |ours - Soma| (s); spread: maximum deviation from the event's
# mean offset (s).
SOMA = [
    dict(key='2007-03-19', body='moon', span=('2007-03-19T00:30', '2007-03-19T05:30'),
         src=HINODE + '070319Eclipse/', note='orbital elements No.51 of 2007-02-28',
         tol=4.0, spread=1.6, passes=[
             dict(type='partial', C1='01:22:28.3', MAX='01:28:44.5', C4='01:35:10.2'),
             dict(type='total', C1='02:48:58.5', C2='02:55:36.4', MAX='02:55:44.3',
                  C3='02:55:52.4', C4='03:02:27.1'),
             dict(type='partial', C1='04:21:16.8', MAX='04:24:47.4', C4='04:28:14.6')]),
    dict(key='2011-01-04', body='moon', span=('2011-01-04T06:30', '2011-01-04T12:00'),
         src=HINODE + '110104AnnularEclipse/', note='orbital elements No.255 of 2010-12-08',
         tol=8.0, spread=1.6, passes=[
             dict(type='partial', C1='07:41:29.7', MAX='07:48:10.9', C4='07:55:02.3'),
             dict(type='annular', C1='09:08:57.3', C2='09:16:06.3', MAX='09:16:12.8',
                  C3='09:16:19.3', C4='09:23:20.4'),
             dict(type='partial', C1='10:40:41.4', MAX='10:45:41.2', C4='10:50:32.9')]),
    dict(key='2011-06-01', body='moon', span=('2011-06-01T19:30', '2011-06-01T23:30'),
         src=HINODE + '110601PartialEclipse/', note='orbital elements No.280 of 2011-05-11',
         tol=7.0, spread=1.0, passes=[
             dict(type='partial', C1='20:36:13.5', MAX='20:41:56.2', C4='20:47:51.6'),
             dict(type='partial', C1='22:06:11.3', MAX='22:11:11.3', C4='22:16:06.0')]),
    # The page gives the maximum as "6:41:37 (JST) (21:41:57 UT)"; the JST value
    # is the consistent one (UT typo).  Pass 4 is the fourth of four eclipses.
    dict(key='2012-05-20', body='moon', span=('2012-05-20T21:00', '2012-05-21T03:00'),
         src=ISAS + 'eclipse20120521/', tol=1.5, spread=1.0, passes=[
             dict(type='partial', C1='21:33:47', MAX='21:41:37', C4='21:50:04'),
             dict(type='partial', MAX='2012-05-21T02:01:22')]),
    # C1 (in the Earth's shadow, 22:15:46) disagrees by 28 s while the other
    # three contacts agree to 0.5 s; our Sun-Venus separation varies smoothly
    # (0.065"/s) there, so the published C1 is treated as an outlier.
    dict(key='2012-06-05', body='venus', span=('2012-06-05T21:30', '2012-06-06T05:30'),
         src=HINODE + '120606VenusTransit/', tol=1.5, spread=1.0, passes=[
             dict(type='transit', C2='22:28:57', C3='2012-06-06T04:36:29',
                  C4='2012-06-06T04:48:56')]),
    dict(key='2014-10-23', body='moon', span=('2014-10-23T19:30', '2014-10-24T00:30'),
         src=ISAS + 'eclipse20141023/', tol=6.0, spread=1.6, passes=[
             dict(type='partial', C1='20:18:53', MAX='20:25:11', C4='20:31:38'),
             dict(type='annular', C1='21:46:18', C2='21:53:21', MAX='21:53:29', C3='21:53:37',
                  C4='22:00:38'),
             dict(type='partial', C1='23:16:38', MAX='23:22:22', C4='23:28:00')]),
    dict(key='2016-03-09', body='moon', span=('2016-03-08T23:30', '2016-03-09T05:00'),
         src=ISAS + 'eclipse20160309/', tol=22.0, spread=4.5, passes=[
             dict(type='partial', C1='00:01:13', MAX='00:08:31', C4='00:16:13'),
             dict(type='partial', C1='04:15:09', MAX='04:20:38', C4='04:25:55')]),
    dict(key='2017-08-21', body='moon', span=('2017-08-21T16:00', '2017-08-21T20:30'),
         src=ISAS + 'eclipse20170821/', tol=8.0, spread=1.6, passes=[
             dict(type='partial', C1='16:50:19', MAX='16:57:38', C4='17:05:39'),
             dict(type='partial', C1='19:35:42', MAX='19:43:28', C4='19:50:35')]),
]

_cache = {}


def hinode(body, a, b, params=SOMA_PARAMS):
    key = (body, a, b, tuple(sorted(params.as_dict().items())))
    if key not in _cache:
        obs = SSCWebObserver(CTX, 'hinode', 'Hinode')
        _cache[key] = LocalSearch(CTX, obs, body, params, parse_utc(CTX, a), parse_utc(CTX, b)).run()
    return _cache[key]


def jd(day, t):
    return parse_utc(CTX, t if 'T' in t else f'{day}T{t}')


def contacts(e):
    out = {}
    for c in e['contacts']:
        out.setdefault(c['label'], c['jd_tt'])
    out['MAX'] = e['jd_max']
    return out


def nearest(events, t):
    return min(events, key=lambda e: abs(e['jd_max'] - t))


def compare_soma(ref):
    """Returns [(pass, label, ours - Soma in s)] and checks the event types."""
    day = ref['key']
    ev = hinode(ref['body'], *ref['span'])
    rows = []
    for i, p in enumerate(ref['passes']):
        anchor = p.get('MAX') or p.get('C2')
        e = nearest(ev, jd(day, anchor))
        assert e['type'] == p['type'], (day, i + 1, e['type'], p['type'])
        c = contacts(e)
        for lab in ('C1', 'C2', 'MAX', 'C3', 'C4'):
            if lab in p:
                rows.append((i + 1, lab, (c[lab] - jd(day, p[lab])) * 86400.0))
    return rows


def test_soma_predictions():
    lines = []
    for ref in SOMA:
        rows = compare_soma(ref)
        d = np.array([r[2] for r in rows])
        mean = d.mean()
        lines.append(f"  {ref['key']} {ref['body']:5s} mean {mean:+6.2f} s  spread {np.abs(d - mean).max():4.2f} s  "
                     + ' '.join(f'p{p}{lab}:{v:+.1f}' for p, lab, v in rows))
        assert np.abs(d).max() <= ref['tol'], (ref['key'], rows)
        assert np.abs(d - mean).max() <= ref['spread'], (ref['key'], rows)
    print('\n'.join(lines))


# --- facts from the actual observations --------------------------------------
def test_2006_mercury_transit():
    # SOT stills: no Mercury yet at 19:12:27; centre half-way through the limb
    # at 19:13:02; at C3 time 00:08:02 Mercury had just begun to leave.
    e = hinode('mercury', '2006-11-08T18:00', '2006-11-09T01:30', Params(include_invisible=True))[0]
    c = contacts(e)
    assert c['C1'] > jd('2006-11-08', '19:12:27')
    assert c['C1'] < jd('2006-11-08', '19:13:02') < c['C2']
    assert c['C3'] < jd('2006-11-09', '00:08:02') < c['C3'] + 30 / 86400


def test_2007_02_17_orbit_only_eclipse():
    # Partial eclipse seen only from orbit; SOT/XRT images 16:09:00-16:11:27 UT
    # show the Moon's northern limb over the southern part of the Sun.
    ev = hinode('moon', '2007-02-17T12:00', '2007-02-17T20:00')
    assert len(ev) == 1 and ev[0]['type'] == 'partial'
    c = contacts(ev[0])
    assert c['C1'] < jd('2007-02-17', '16:09:00') and jd('2007-02-17', '16:11:27') < c['C4']
    pa = next(x['pa'] for x in ev[0]['contacts'] if x['label'] == 'MAX')
    assert 90 < pa < 270, pa
    assert search_global(CTX, parse_utc(CTX, '2007-02-10'), parse_utc(CTX, '2007-02-25'), Params()) == []


def test_2007_03_19_total_from_orbit():
    ev = hinode('moon', '2007-03-19T00:30', '2007-03-19T05:30')
    assert [e['type'] for e in ev] == ['partial', 'total', 'partial']
    c = contacts(ev[1])
    assert c['C2'] < jd('2007-03-19', '02:55:40') < c['C3']          # XRT "during totality"
    for e, ts in zip(ev, (('01:27:38', '01:29:22'), ('02:52:06', '02:59:22'), ('04:24:15', '04:25:08'))):
        for t in ts:                                                  # SOT images of each pass
            assert e['jd_c1'] < jd('2007-03-19', t) < e['jd_c4']


def test_2009_07_22():
    # XRT: start + 2 min = 00:52, maximum 00:58, end - 2 min = 01:05 UT; magnitude 0.73, ~17 min
    e = hinode('moon', '2009-07-21T23:00', '2009-07-22T05:00')[0]
    assert abs(e['magnitude'] - 0.73) < 0.01
    for t, ref in ((e['jd_c1'], '00:50'), (e['jd_max'], '00:58'), (e['jd_c4'], '01:07')):
        assert abs(t - jd('2009-07-22', ref)) * 1440 < 1.0
    assert abs(e['duration_s'] / 60 - 17) < 1


def test_2011_06_01():
    # Pass 1 magnitude 0.51; passes at 20:36-20:47 (max 20:41) and 22:06-22:16 (max 22:11) UT
    ev = hinode('moon', '2011-06-01T19:30', '2011-06-01T23:30')
    assert len(ev) == 2 and abs(ev[0]['magnitude'] - 0.51) < 0.01
    for e, (a, m, b) in zip(ev, (('20:36', '20:41', '20:47'), ('22:06', '22:11', '22:16'))):
        for t, ref in ((e['jd_c1'], a), (e['jd_max'], m), (e['jd_c4'], b)):
            assert abs(t - jd('2011-06-01', ref)) * 1440 < 1.0


def test_2012_05_20_four_partials():
    ev = hinode('moon', '2012-05-20T21:00', '2012-05-21T03:00')
    assert [e['type'] for e in ev] == ['partial'] * 4
    assert abs(ev[0]['magnitude'] - 0.80) < 0.02                     # "about 80 %"


def test_2012_06_05_venus_earth_shadow():
    # C1 could not be observed because Hinode was in the Earth's shadow
    # (about 22:07-22:26 UT in the Hinode team's figure); SOT started 22:26:07.
    e = hinode('venus', '2012-06-05T21:30', '2012-06-06T05:30', Params(include_invisible=True))[0]
    vis = {c['label']: c['visible'] for c in e['contacts']}
    assert not vis['C1'] and vis['C2'] and vis['C3']
    first = parse_utc(CTX, e['visible_intervals'][0][0][:-1])
    assert jd('2012-06-05', '22:23') < first < jd('2012-06-05', '22:26:07')


def test_2012_11_13():
    # The press release quotes a magnitude of 0.993 (just outside totality);
    # the computation gives a 15 s total eclipse, and the Moon centres
    # measured in the XRT images confirm the computed track (see
    # tools/validate_hinode_images.py), so only the robust facts are checked.
    ev = hinode('moon', '2012-11-13T19:00', '2012-11-14T02:00')
    assert len(ev) == 2 and ev[0]['magnitude'] > 0.99
    assert abs(ev[0]['jd_max'] - jd('2012-11-13', '20:25')) * 1440 < 1.0
    assert abs(ev[0]['duration_s'] / 60 - 17) < 1.5
    assert abs((ev[1]['jd_max'] - ev[0]['jd_max']) * 24 - 4) < 0.5    # "about 4 hours later"


def test_2014_10_23_annular():
    ev = hinode('moon', '2014-10-23T19:30', '2014-10-24T00:30')
    assert [e['type'] for e in ev] == ['partial', 'annular', 'partial']
    # The XRT image published as the annular maximum (21:53:33) was taken at
    # third contact: the measured Moon-Sun separation (54") is just above the
    # annular limit (50"), consistent with the actual orbit being ~1.7 s ahead
    # of SSCWeb's (tools/validate_hinode_images.py).
    c = contacts(ev[1])
    t = jd('2014-10-23', '21:53:33')
    assert c['C2'] - 3 / 86400 < t < c['C3'] + 3 / 86400


def test_2016_03_09():
    ev = hinode('moon', '2016-03-08T23:30', '2016-03-09T05:00')
    assert len(ev) == 2
    assert abs(ev[0]['jd_max'] - jd('2016-03-09', '00:08:07')) * 86400 < 10   # XRT "maximum" image


def test_2017_08_21():
    ev = hinode('moon', '2017-08-21T16:00', '2017-08-21T20:30')
    assert len(ev) == 2
    assert abs(ev[0]['magnitude'] - 0.714) < 0.006
    assert abs(ev[0]['obscuration'] - 0.645) < 0.007


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

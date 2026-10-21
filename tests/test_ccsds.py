"""Checks of the CCSDS input (eclipsecalc/ccsds.py, od.py, propagate.py): the Orbit Data Messages
(OEM, OPM, OMM, OCM; CCSDS 502.0-B-3) and the Tracking Data Message (TDM; CCSDS 503.0-B-2), in KVN
and XML, must be read as the standards define them and give the orbit they describe: an OMM the
orbit of the TLE it was made from, an OEM its states, an OPM or OCM the numerical propagation of
its state (close to SGP4 over a day, with the maneuvers where they belong), and a TDM the orbit its
measurements were made from.  Wrong input must get a clear message in every language, and cli.py
and the web API must accept the messages.

Run with  python tests/test_ccsds.py  (no internet needed).
"""
import ast
import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from skyfield.api import wgs84  # noqa: E402
from skyfield.framelib import ICRS_to_J2000, itrs  # noqa: E402

import cli  # noqa: E402
from eclipsecalc import ccsds, i18n, server  # noqa: E402
from eclipsecalc.constants import C_KM_S, DAY_S, GM_EARTH  # noqa: E402
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.observers import ObserverError, TLEObserver, build_observer  # noqa: E402
from eclipsecalc.propagate import Forces, Trajectory, elements  # noqa: E402

CTX = get_context()
# ISS, 2008-09-20 (the example TLE of the SGP4 documentation, as tests/test_cli.py)
NAME = 'ISS (ZARYA)'
L1 = '1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927'
L2 = '2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537'
TLE = TLEObserver(CTX, L1, L2, NAME)

OMM_KVN = """CCSDS_OMM_VERS = 3.0
COMMENT the TLE above as an OMM, with a day-of-year epoch
CREATION_DATE = 2008-09-21T00:00:00
ORIGINATOR = TEST
OBJECT_NAME = ISS (ZARYA)
OBJECT_ID = 1998-067A
CENTER_NAME = EARTH
REF_FRAME = TEME
TIME_SYSTEM = UTC
MEAN_ELEMENT_THEORY = SGP4
EPOCH = 2008-264T12:25:40.104192
MEAN_MOTION = 15.72125391 [rev/day]
ECCENTRICITY = 0.0006703
INCLINATION = 51.6416 [deg]
RA_OF_ASC_NODE = 247.4627 [deg]
ARG_OF_PERICENTER = 130.5360 [deg]
MEAN_ANOMALY = 325.0288 [deg]
EPHEMERIS_TYPE = 0
CLASSIFICATION_TYPE = U
NORAD_CAT_ID = 25544
ELEMENT_SET_NO = 292
REV_AT_EPOCH = 56353
BSTAR = -0.11606E-4 [1/ER]
MEAN_MOTION_DOT = -0.00002182 [rev/day**2]
MEAN_MOTION_DDOT = 0.0 [rev/day**3]
"""
OMM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ndm xmlns="urn:ccsds:schema:ndmxml" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <omm id="CCSDS_OMM_VERS" version="3.0">
  <header><COMMENT>an NDM bundle with one OMM</COMMENT>
   <CREATION_DATE>2008-09-21T00:00:00</CREATION_DATE><ORIGINATOR>TEST</ORIGINATOR></header>
  <body><segment>
   <metadata><OBJECT_NAME>ISS (ZARYA)</OBJECT_NAME><OBJECT_ID>1998-067A</OBJECT_ID>
    <CENTER_NAME>EARTH</CENTER_NAME><REF_FRAME>TEME</REF_FRAME><TIME_SYSTEM>UTC</TIME_SYSTEM>
    <MEAN_ELEMENT_THEORY>SGP4</MEAN_ELEMENT_THEORY></metadata>
   <data>
    <meanElements><EPOCH>2008-09-20T12:25:40.104192</EPOCH><MEAN_MOTION units="rev/day">15.72125391</MEAN_MOTION>
     <ECCENTRICITY>0.0006703</ECCENTRICITY><INCLINATION units="deg">51.6416</INCLINATION>
     <RA_OF_ASC_NODE>247.4627</RA_OF_ASC_NODE><ARG_OF_PERICENTER>130.5360</ARG_OF_PERICENTER>
     <MEAN_ANOMALY>325.0288</MEAN_ANOMALY></meanElements>
    <tleParameters><NORAD_CAT_ID>25544</NORAD_CAT_ID><BSTAR>-0.11606E-4</BSTAR>
     <MEAN_MOTION_DOT>-0.00002182</MEAN_MOTION_DOT><MEAN_MOTION_DDOT>0.0</MEAN_MOTION_DDOT></tleParameters>
   </data>
  </segment></body>
 </omm>
</ndm>
"""
# as CelesTrak gives it (gp.php?FORMAT=JSON / CSV)
OMM_JSON = json.dumps([{
    'OBJECT_NAME': 'ISS (ZARYA)', 'OBJECT_ID': '1998-067A', 'EPOCH': '2008-09-20T12:25:40.104192',
    'MEAN_MOTION': 15.72125391, 'ECCENTRICITY': 0.0006703, 'INCLINATION': 51.6416, 'RA_OF_ASC_NODE': 247.4627,
    'ARG_OF_PERICENTER': 130.536, 'MEAN_ANOMALY': 325.0288, 'EPHEMERIS_TYPE': 0, 'CLASSIFICATION_TYPE': 'U',
    'NORAD_CAT_ID': 25544, 'ELEMENT_SET_NO': 292, 'REV_AT_EPOCH': 56353, 'BSTAR': -1.1606e-05,
    'MEAN_MOTION_DOT': -2.182e-05, 'MEAN_MOTION_DDOT': 0}])
OMM_CSV = ('OBJECT_NAME,OBJECT_ID,EPOCH,MEAN_MOTION,ECCENTRICITY,INCLINATION,RA_OF_ASC_NODE,ARG_OF_PERICENTER,'
           'MEAN_ANOMALY,EPHEMERIS_TYPE,CLASSIFICATION_TYPE,NORAD_CAT_ID,ELEMENT_SET_NO,REV_AT_EPOCH,BSTAR,'
           'MEAN_MOTION_DOT,MEAN_MOTION_DDOT\n'
           'ISS (ZARYA),1998-067A,2008-09-20T12:25:40.104192,15.72125391,.0006703,51.6416,247.4627,130.536,'
           '325.0288,0,U,25544,292,56353,-.11606e-4,-.2182e-4,0\n')

STATIONS = {'USUDA': (36.1325, 138.3628, 1456.0), 'KATSUURA': (35.2025, 140.3075, 50.0),
            'OKINAWA': (26.498, 127.864, 50.0), 'MASUDA': (30.556, 131.016, 50.0)}
STATIONS_TEXT = '# name lat lon elevation_m\n' + '\n'.join(f'{k} {v[0]} {v[1]} {v[2]}' for k, v in STATIONS.items())


def _times(y, mo, d, seconds):
    return CTX.ts.utc(y, mo, d, 0, 0, np.asarray(seconds, float))


def _gcrs(obs, t):
    """Geocentric GCRS positions (n, 3) of an observer at the Times t."""
    return obs.vf.at(t).position.km.T


def _eme2000(t):
    """The SGP4 states of the TLE at the Times t in EME2000 axes (n, 6)."""
    st = TLE.vf.at(t)
    return np.concatenate([(ICRS_to_J2000 @ st.position.km).T, (ICRS_to_J2000 @ st.velocity.km_per_s).T], axis=1)


def _iso(t):
    return [s + '.000' for s in t.utc_strftime('%Y-%m-%dT%H:%M:%S')]


def _oem(t, name=NAME, meta=''):
    """A KVN OEM of the TLE at the Times t (whole seconds)."""
    lines = ['CCSDS_OEM_VERS = 3.0', 'CREATION_DATE = 2008-07-30T00:00:00', 'ORIGINATOR = TEST', '', 'META_START',
             f'OBJECT_NAME = {name}', 'OBJECT_ID = 1998-067A', 'CENTER_NAME = EARTH', 'REF_FRAME = EME2000',
             'TIME_SYSTEM = UTC', f'START_TIME = {_iso(t)[0]}', f'STOP_TIME = {_iso(t)[-1]}', 'INTERPOLATION = HERMITE',
             'INTERPOLATION_DEGREE = 7'] + ([meta] if meta else []) + ['META_STOP', '']
    for s, x in zip(_iso(t), _eme2000(t)):
        lines.append(s + ' ' + ' '.join(f'{v:.6f}' for v in x[:3]) + ' ' + ' '.join(f'{v:.9f}' for v in x[3:]))
    return '\n'.join(lines) + '\n'


def _opm(t, x, extra='', head=''):
    """A KVN OPM of the state x (EME2000) at the Time t (whole seconds)."""
    return (f'CCSDS_OPM_VERS = 3.0\n{head}CREATION_DATE = 2008-07-31T00:00:00\nORIGINATOR = TEST\n'
            f'OBJECT_NAME = {NAME}\nOBJECT_ID = 1998-067A\nCENTER_NAME = EARTH\nREF_FRAME = EME2000\n'
            f'TIME_SYSTEM = UTC\nCOMMENT State Vector\nEPOCH = {_iso(t)[0]}\n'
            + ''.join(f'{k} = {v:.9f} [{u}]\n' for k, v, u in zip(('X', 'Y', 'Z', 'X_DOT', 'Y_DOT', 'Z_DOT'), x,
                                                                    ('km',) * 3 + ('km/s',) * 3)) + extra)


SPACECRAFT = 'MASS = 300000.0 [kg]\nDRAG_AREA = 1500.0 [m**2]\nDRAG_COEFF = 2.2\n'
KANA = re.compile(r'[ぁ-ゖァ-ヺ]')


def _cli_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv + ['--format', 'json'] + ([] if '--lang' in argv else ['--lang', 'ja']))
    return code, json.loads(out.getvalue())


def _error(spec, lang='ja'):
    """The message of the ObserverError that the request spec raises, in ``lang``."""
    token = i18n.set_lang(lang)
    try:
        build_observer(CTX, dict({'type': 'ccsds'}, **spec))
    except ObserverError as exc:
        return str(exc)
    finally:
        i18n.reset_lang(token)
    raise AssertionError('no error: ' + str(spec)[:80])


# ---------------------------------------------------------------------------
def test_omm_is_the_tle():
    # The OMM of a TLE (KVN with a day-of-year epoch, XML in an NDM bundle with a namespace,
    # CelesTrak JSON and CSV) gives the orbit of the TLE (SGP4 with the same elements).
    t = _times(2008, 9, 20, np.linspace(-2, 2, 97) * DAY_S)
    ref = _gcrs(TLE, t)
    for text, fmt in ((OMM_KVN, 'KVN'), (OMM_XML, 'XML'), (OMM_JSON, 'JSON'), (OMM_CSV, 'CSV')):
        msgs = ccsds.parse(text)
        assert [(m.kind, m.fmt) for m in msgs] == [('OMM', fmt)], text[:40]
        obs = build_observer(CTX, {'type': 'ccsds', 'text': text})
        assert obs.name == NAME and abs(obs.epoch_jd - TLE.epoch_jd) < 1e-9
        assert np.abs(_gcrs(obs, t) - ref).max() < 1e-3, fmt            # 1 m (the TLE rounds its numbers)
        assert ccsds.mean_elements_only({'text': text})
    d = build_observer(CTX, {'type': 'ccsds', 'text': OMM_KVN}).describe()
    assert d['model'] == 'ccsds' and d['message'] == 'OMM' and abs(d['i_deg'] - 51.6416) < 0.01


def test_oem_interpolation_and_events():
    # An OEM of the TLE (1-minute states) gives back the TLE between its states, and the same
    # events as the TLE (the total eclipse of 2008-08-01 seen from the ISS).
    t = _times(2008, 7, 31, np.arange(0, 2 * DAY_S + 1, 60.0))
    text = _oem(t)
    obs = build_observer(CTX, {'type': 'ccsds', 'text': text})
    tq = _times(2008, 7, 31, np.linspace(30, 2 * DAY_S - 30, 2000) + 0.123)
    # Hermite interpolation follows the velocities, and those of SGP4 differ from the derivatives
    # of its positions by up to 3 cm/s: within 2 m; Lagrange (positions only): within 5 mm
    assert np.abs(_gcrs(obs, tq) - _gcrs(TLE, tq)).max() < 2e-3
    lagrange = build_observer(CTX, {'type': 'ccsds', 'text': text.replace('= HERMITE', '= LAGRANGE')})
    assert np.abs(_gcrs(lagrange, tq) - _gcrs(TLE, tq)).max() < 5e-6
    d = obs.describe()
    assert d['message'] == 'OEM' and d['segments'][0]['n_states'] == t.tt.size and len(d['intervals']) == 1
    assert obs.info['interpolation_error_km'] < 1e-4
    kw = dict(phenomena=['moon'], start='2008-07-31', end='2008-08-02', settings={})
    got = server.search(server.SearchRequest(observer={'type': 'ccsds', 'text': text}, **kw))
    ref = server.search(server.SearchRequest(observer={'type': 'tle', 'line1': L1, 'line2': L2, 'name': NAME}, **kw))
    assert len(got['events']) == len(ref['events']) >= 2 and not got['warnings'] and ref['warnings']
    for a, b in zip(got['events'], ref['events']):
        assert a['type'] == b['type'] and abs(a['magnitude'] - b['magnitude']) < 1e-6
        for key in ('c1', 'max'):
            assert abs(_secs(a[key]) - _secs(b[key])) < 0.002, (key, a[key], b[key])
    # outside its states an OEM is not extrapolated: a longer period is limited to the data, with
    # a gap between two segments left out
    gap = text.replace('META_STOP', 'USEABLE_STOP_TIME = 2008-08-01T00:00:00.000\nMETA_STOP')
    t2 = _times(2008, 8, 1, np.arange(3 * 3600, DAY_S + 1, 60.0))
    obs2 = build_observer(CTX, {'type': 'ccsds', 'text': [gap, _oem(t2)]})
    assert len(obs2.intervals) == 2
    a, b, notes = obs2.limit_period(CTX, t.tt[0] - 5, t.tt[-1] + 5, 0.01)
    assert abs(a - t.tt[0]) < 1e-6 and abs(b - t.tt[-1]) < 1e-6
    assert any('限定して計算' in n for n in notes) and any('途切れている 1 か所' in n for n in notes), notes
    try:
        obs2.vf.at(_times(2008, 8, 1, [3600.0]))
        raise AssertionError('time in the gap')
    except ObserverError as exc:
        assert '範囲外' in str(exc)


def _secs(iso):
    t = CTX.ts.utc(int(iso[:4]), int(iso[5:7]), int(iso[8:10]), int(iso[11:13]), int(iso[14:16]), float(iso[17:23]))
    return float(t.tt) * DAY_S


def test_oem_xml_and_other_frames():
    # The same states in XML, in GCRF, in the TOD frame and relative to the Moon give the same orbit.
    t = _times(2008, 8, 1, np.arange(0, 3 * 3600 + 1, 60.0))
    x = _eme2000(t)
    ref = build_observer(CTX, {'type': 'ccsds', 'text': _oem(t)})
    tq = _times(2008, 8, 1, np.linspace(100, 3 * 3600 - 100, 50))
    p_ref = _gcrs(ref, tq)
    st = TLE.vf.at(t)
    gcrs = np.concatenate([st.position.km.T, st.velocity.km_per_s.T], axis=1)
    tod = np.concatenate([np.einsum('ijn,jn->ni', t.M, st.position.km), np.einsum('ijn,jn->ni', t.M, st.velocity.km_per_s)],
                         axis=1)
    moon = (CTX.eph['moon'] - CTX.earth).at(t)
    lunar = gcrs - np.concatenate([moon.position.km.T, moon.velocity.km_per_s.T], axis=1)
    sv = ''.join(f'<stateVector><EPOCH>{s}</EPOCH>' + ''.join(f'<{k}>{v:.9f}</{k}>' for k, v in
                                                            zip(('X', 'Y', 'Z', 'X_DOT', 'Y_DOT', 'Z_DOT'), row))
                 + '</stateVector>\n' for s, row in zip(_iso(t), x))
    xml = ('<oem id="CCSDS_OEM_VERS" version="3.0"><header><CREATION_DATE>2008-08-01T00:00:00</CREATION_DATE>'
           '<ORIGINATOR>TEST</ORIGINATOR></header><body><segment><metadata><OBJECT_NAME>ISS (ZARYA)</OBJECT_NAME>'
           '<OBJECT_ID>1998-067A</OBJECT_ID><CENTER_NAME>EARTH</CENTER_NAME><REF_FRAME>EME2000</REF_FRAME>'
           '<TIME_SYSTEM>UTC</TIME_SYSTEM><START_TIME>2008-08-01T00:00:00</START_TIME><STOP_TIME>2008-08-01T03:00:00'
           '</STOP_TIME><INTERPOLATION>HERMITE</INTERPOLATION><INTERPOLATION_DEGREE>7</INTERPOLATION_DEGREE>'
           '</metadata><data>\n' + sv + '</data></segment></body></oem>')
    texts = [xml]
    for frame, center, rows in (('GCRF', 'EARTH', gcrs), ('TOD', 'EARTH', tod), ('ICRF', 'MOON', lunar)):
        body = '\n'.join(s + ' ' + ' '.join(f'{v:.9f}' for v in row) for s, row in zip(_iso(t), rows))
        texts.append(_oem(t).split('META_STOP')[0].replace('REF_FRAME = EME2000', f'REF_FRAME = {frame}')
                     .replace('CENTER_NAME = EARTH', f'CENTER_NAME = {center}') + 'META_STOP\n' + body + '\n')
    for text in texts:
        obs = build_observer(CTX, {'type': 'ccsds', 'text': text})
        assert np.abs(_gcrs(obs, tq) - p_ref).max() < 1e-5, text[:120]


def test_opm_propagation():
    # The state of an OPM is propagated numerically (EGM96 8x8, Sun, Moon): within 3 km of SGP4
    # over a day either side (this TLE has no decay: its BSTAR is negative).  With the mass, area
    # and drag coefficient of the ISS, drag lowers the orbit and puts it 27 km ahead after a day
    # (3/2 a t^2 with the drag deceleration a, about 2e-6 m/s^2).  The same in XML, and in other
    # time systems.
    t0 = _times(2008, 8, 1, [0.0])
    x0 = _eme2000(t0)[0]
    plain = build_observer(CTX, {'type': 'ccsds', 'text': _opm(t0, x0)})
    for hours, km in ((1, 1.0), (6, 3.0), (24, 5.0)):
        tq = _times(2008, 8, 1, np.linspace(-hours, hours, 121) * 3600)
        err = np.linalg.norm(_gcrs(plain, tq) - _gcrs(TLE, tq), axis=1).max()
        assert err < km, (hours, err)
    text = _opm(t0, x0, SPACECRAFT)
    obs = build_observer(CTX, {'type': 'ccsds', 'text': text})
    assert obs.info['message'] == 'OPM' and obs.info['drag'] and 'drag' in obs.info['forces']
    assert any('大気抵抗は平均的な太陽活動' in n for n in obs.notes)
    t1 = _times(2008, 8, 1, [DAY_S])
    d = (obs.vf.at(t1).position.km - plain.vf.at(t1).position.km)[:, 0]
    v = plain.vf.at(t1).velocity.km_per_s[:, 0]
    along = d @ v / np.linalg.norm(v)
    assert 15 < along < 45 and np.linalg.norm(d) < along * 1.01, d
    tq = _times(2008, 8, 1, np.linspace(-DAY_S, DAY_S, 77))
    p = _gcrs(obs, tq)
    xml = ('<?xml version="1.0"?>\n<opm id="CCSDS_OPM_VERS" version="3.0"><header><CREATION_DATE>2008-07-31T00:00:00'
           '</CREATION_DATE><ORIGINATOR>TEST</ORIGINATOR></header><body><segment><metadata>'
           '<OBJECT_NAME>ISS (ZARYA)</OBJECT_NAME><OBJECT_ID>1998-067A</OBJECT_ID><CENTER_NAME>EARTH</CENTER_NAME>'
           '<REF_FRAME>EME2000</REF_FRAME><TIME_SYSTEM>UTC</TIME_SYSTEM></metadata><data>'
           '<stateVector><EPOCH>2008-08-01T00:00:00.000</EPOCH>'
           + ''.join(f'<{k} units="km">{v:.9f}</{k}>' for k, v in zip(('X', 'Y', 'Z', 'X_DOT', 'Y_DOT', 'Z_DOT'), x0))
           + '</stateVector><spacecraftParameters><MASS>300000.0</MASS><DRAG_AREA>1500.0</DRAG_AREA>'
           '<DRAG_COEFF>2.2</DRAG_COEFF></spacecraftParameters></data></segment></body></opm>')
    assert np.abs(_gcrs(build_observer(CTX, {'type': 'ccsds', 'text': xml}), tq) - p).max() < 1e-6
    # the same epoch in TAI (TAI - UTC = 33 s in 2008), GPS (TAI - 19 s), TT (TAI + 32.184 s) and
    # as a day of the year (2008 is a leap year: 1 August is day 214)
    for system, epoch in (('TAI', '2008-08-01T00:00:33.000'), ('GPS', '2008-08-01T00:00:14.000'),
                          ('TT', '2008-08-01T00:01:05.184'), ('UTC', '2008-214T00:00:00')):
        other = (text.replace('TIME_SYSTEM = UTC', f'TIME_SYSTEM = {system}')
                 .replace('EPOCH = 2008-08-01T00:00:00.000', f'EPOCH = {epoch}'))
        assert np.abs(_gcrs(build_observer(CTX, {'type': 'ccsds', 'text': other}), tq) - p).max() < 1e-5, system


def test_opm_maneuvers():
    # An impulsive maneuver changes the orbit from its time on (here +10 m/s along-track one hour
    # after the epoch: the semi-major axis grows as vis-viva says); before it, nothing changes.
    # A maneuver at the epoch acts after the epoch only.
    t0 = _times(2008, 8, 1, [0.0])
    x0 = _eme2000(t0)[0]
    plain = build_observer(CTX, {'type': 'ccsds', 'text': _opm(t0, x0)})
    assert any('大気抵抗の計算に必要な値' in n for n in plain.notes)
    man = ('MAN_EPOCH_IGNITION = {t}\nMAN_DURATION = 0.0 [s]\nMAN_DELTA_MASS = -1.0 [kg]\nMAN_REF_FRAME = RTN\n'
           'MAN_DV_1 = 0.0 [km/s]\nMAN_DV_2 = 0.010 [km/s]\nMAN_DV_3 = 0.0 [km/s]\n')
    burn = build_observer(CTX, {'type': 'ccsds', 'text': _opm(t0, x0, man.format(t='2008-08-01T01:00:00.000'))})
    assert burn.info['maneuvers'] == 1
    before = _times(2008, 8, 1, np.linspace(-6 * 3600, 3600 - 1e-3, 40))
    assert np.abs(_gcrs(burn, before) - _gcrs(plain, before)).max() < 1e-7
    tb = _times(2008, 8, 1, [3600 - 1e-3, 3600 + 1e-3])
    st = burn.vf.at(tb)
    r, v = st.position.km.T, st.velocity.km_per_s.T
    rn = np.linalg.norm(r[0])
    t_hat = np.cross(np.cross(r[0], v[0]), r[0])
    v_new = v[0] + 0.010 * t_hat / np.linalg.norm(t_hat)
    a_expected = 1 / (2 / rn - v_new @ v_new / GM_EARTH)
    a_after = elements(r[1], v[1], GM_EARTH)['a_km']
    assert abs(a_after - a_expected) < 0.01 and a_after - elements(r[0], v[0], GM_EARTH)['a_km'] > 15, a_after
    at_epoch = build_observer(CTX, {'type': 'ccsds', 'text': _opm(t0, x0, man.format(t='2008-08-01T00:00:00.000'))})
    tq = _times(2008, 8, 1, [-60.0, -1.0, 1.0, 60.0])
    dp = np.linalg.norm(_gcrs(at_epoch, tq) - _gcrs(plain, tq), axis=1)
    assert dp[0] < 1e-7 and dp[1] < 1e-7 and 0.009 < dp[2] < 0.011 and 0.5 < dp[3] < 0.7, dp


def test_ocm():
    # An OCM with times relative to EPOCH_TZERO: a sequence of states is interpolated (like an
    # OEM); one state in the Earth-fixed EFG frame, or as osculating Keplerian elements, is
    # propagated from the right GCRS state with the drag of its PHYS block (as the OPM of that
    # state with the same spacecraft); an impulsive maneuver block is applied.
    t = _times(2008, 8, 1, np.arange(0, 2 * 3600 + 1, 60.0))
    x = _eme2000(t)
    head = ('CCSDS_OCM_VERS = 3.0\nCREATION_DATE = 2008-08-01T00:00:00\nORIGINATOR = TEST\nMETA_START\n'
            'OBJECT_NAME = ISS (ZARYA)\nINTERNATIONAL_DESIGNATOR = 1998-067A\nTIME_SYSTEM = UTC\n'
            'EPOCH_TZERO = 2008-08-01T00:00:00\nMETA_STOP\n')
    rows = '\n'.join(f'{s:.1f} ' + ' '.join(f'{v:.9f}' for v in row) for s, row in zip(np.arange(0, 7201, 60.0), x))
    text = (head + 'TRAJ_START\nCENTER_NAME = EARTH\nTRAJ_REF_FRAME = EME2000\nTRAJ_TYPE = CARTPV\n'
            'INTERPOLATION = HERMITE\nINTERPOLATION_DEGREE = 7\n' + rows + '\nTRAJ_STOP\n')
    obs = build_observer(CTX, {'type': 'ccsds', 'text': text})
    tq = _times(2008, 8, 1, np.linspace(10, 7190, 300))
    assert type(obs).__name__ == 'EphemerisObserver' and np.abs(_gcrs(obs, tq) - _gcrs(TLE, tq)).max() < 2e-3
    t1 = _times(2008, 8, 1, [1800.0])
    opm = build_observer(CTX, {'type': 'ccsds', 'text': _opm(t1, _eme2000(t1)[0], SPACECRAFT)})
    p_itrs, v_itrs = TLE.vf.at(t1).frame_xyz_and_velocity(itrs)
    efg = np.concatenate([p_itrs.km[:, 0], v_itrs.km_per_s[:, 0]])
    st = TLE.vf.at(t1)
    el = elements(st.position.km[:, 0], st.velocity.km_per_s[:, 0], GM_EARTH)
    kep = [el[k] for k in ('a_km', 'e', 'i_deg', 'raan_deg', 'argp_deg', 'nu_deg')]
    for ttype, frame, row in (('CARTPV', 'EFG', efg), ('KEPLERIAN', 'GCRF', kep)):
        one = (head + f'TRAJ_START\nCENTER_NAME = EARTH\nTRAJ_REF_FRAME = {frame}\nTRAJ_TYPE = {ttype}\n'
               + '1800.0 ' + ' '.join(f'{v:.10f}' for v in row) + '\nTRAJ_STOP\n'
               'PHYS_START\nWET_MASS = 300000.0 [kg]\nDRAG_CONST_AREA = 1500.0 [m**2]\nDRAG_COEFF_NOM = 2.2\nPHYS_STOP\n')
        prop = build_observer(CTX, {'type': 'ccsds', 'text': one})
        assert type(prop).__name__ == 'PropagatedObserver' and prop.info['drag'], ttype
        assert np.abs(_gcrs(prop, t1) - st.position.km.T).max() < 1e-5, ttype
        tq2 = _times(2008, 8, 1, np.linspace(-3 * 3600, 6 * 3600, 50))
        assert np.abs(_gcrs(prop, tq2) - _gcrs(opm, tq2)).max() < 1e-3, ttype
    man = ('MAN_START\nMAN_ID = DV1\nMAN_DEVICE_ID = THR1\nMAN_REF_FRAME = RTN\n'
           'MAN_COMPOSITION = TIME_RELATIVE, MAN_DURA, DV_X, DV_Y, DV_Z\n5400.0 0.0 0.0 0.010 0.0\nMAN_STOP\n')
    one = (head + 'TRAJ_START\nCENTER_NAME = EARTH\nTRAJ_REF_FRAME = EFG\nTRAJ_TYPE = CARTPV\n1800.0 '
           + ' '.join(f'{v:.10f}' for v in efg) + '\nTRAJ_STOP\n')
    a, b = (build_observer(CTX, {'type': 'ccsds', 'text': one + extra}) for extra in ('', man))
    assert b.info['maneuvers'] == 1 and a.info['maneuvers'] == 0
    tq3 = _times(2008, 8, 1, [5000.0, 5399.0, 5460.0])
    dp = np.linalg.norm(_gcrs(a, tq3) - _gcrs(b, tq3), axis=1)
    assert dp[0] < 1e-7 and dp[1] < 1e-7 and 0.5 < dp[2] < 0.7, dp


def test_times_and_frames():
    # CCSDS times: day of the year, leap seconds (only where UTC has one), the time systems; the
    # sidereal angle of the force model stays continuous over many chunks of its tables.
    e = lambda s, system='UTC': ccsds.epoch(CTX, s, system)          # noqa: E731
    diff = lambda a, b: ((a.whole - b.whole) + (a.tt_fraction - b.tt_fraction)) * DAY_S   # noqa: E731
    assert abs(diff(e('2008-214T12:00:00'), e('2008-08-01T12:00:00'))) < 1e-6
    assert abs(diff(e('2009-01-01T00:00:00'), e('2008-12-31T23:59:60')) - 1.0) < 1e-6
    assert abs(diff(e('2008-12-31T23:59:60.5'), e('2008-12-31T23:59:59.5')) - 1.0) < 1e-6
    assert abs(diff(e('2008-08-01T00:00:00', 'TDB'), e('2008-08-01T00:00:00', 'TT')) - 0.0) < 0.002
    assert abs(diff(e('2008-08-01T00:00:00', 'BDT'), e('2008-08-01T00:00:00', 'TAI')) - 33.0) < 1e-6
    assert abs(diff(e('2008-08-01T00:00:00Z'), e('2008-08-01T00:00:00'))) < 1e-9
    for text, want in (('2008-12-30T23:59:60', 'うるう秒ではありません'), ('2008-13-01T00:00:00', '解釈できません'),
                       ('2008-366T00:00:00', ''), ('2007-366T00:00:00', '解釈できません')):
        try:
            e(text)
            assert want == '', text
        except ObserverError as exc:
            assert want and want in str(exc), (text, exc)
    try:
        e('2008-12-31T23:59:60', 'TAI')
        raise AssertionError('leap second in TAI')
    except ObserverError as exc:
        assert 'UTC の時刻にしか' in str(exc)
    t0 = CTX.ts.utc(2008, 8, 1)
    f = Forces(CTX, (float(t0.whole), float(t0.tt_fraction)), 'earth')
    f.ensure(-70 * DAY_S, 70 * DAY_S)
    s = np.linspace(-69, 69, 400) * DAY_S + 1234.5
    _, g = f.tables.earth(s)
    gast = CTX.ts.tt_jd(float(t0.whole), float(t0.tt_fraction) + s / DAY_S).gast * (2 * math.pi / 24)
    d = (g - gast + math.pi) % (2 * math.pi) - math.pi
    assert np.abs(d).max() < 1e-8 and np.all(np.diff(g) > 0), np.abs(d).max()


def _truth(t0, x0):
    """The orbit through the GCRS state x0 at the Time t0 (propagate.py with the default forces)."""
    jd0 = (float(t0.whole), float(t0.tt_fraction))
    traj = Trajectory(Forces(CTX, jd0, 'earth'), x0)

    def at(t):
        s = ((np.asarray(t.whole) - jd0[0]) + (np.asarray(t.tt_fraction) - jd0[1])) * DAY_S
        traj.extend(float(np.min(s)) - 10, float(np.max(s)) + 10)
        return traj.state(s)
    return at


def _tdm(at, hours, kinds=('ANGLE', 'RANGE', 'DOPPLER'), angle_type='AZEL', range_scale=1.0, noise=False,
         stations=STATIONS, sc='ISS', step=20.0):
    """A TDM of the stations tracking the orbit ``at`` from 2008-08-01T00:00 UTC for ``hours``: AZEL
    or RADEC (EME2000) angles, two-way range (half the light path, or the whole with range_scale 2)
    and instantaneous Doppler (range rate), at the reception times with the light times."""
    rng = np.random.default_rng(5)
    segs = []
    for name, (lat, lon, h) in stations.items():
        stn = wgs84.latlon(lat, lon, elevation_m=h)
        tr = _times(2008, 8, 1, np.arange(0, hours * 3600, step))
        R = stn.at(tr).position.km.T
        tb = tr
        for _ in range(4):                                  # the time of the reflection
            p, v = at(tb)
            tb = CTX.ts.tt_jd(tr.whole, tr.tt_fraction - np.linalg.norm(p - R, axis=1) / C_KM_S / DAY_S)
        p, v = at(tb)
        down = np.linalg.norm(p - R, axis=1)
        tt = tb
        for _ in range(4):                                  # the time of the transmission
            tt = CTX.ts.tt_jd(tb.whole, tb.tt_fraction - np.linalg.norm(p - stn.at(tt).position.km.T, axis=1)
                              / C_KM_S / DAY_S)
        up = np.linalg.norm(p - stn.at(tt).position.km.T, axis=1)
        d = p - R
        di = np.einsum('nij,nj->ni', np.moveaxis(itrs.rotation_at(tr), -1, 0), d)
        la, lo = math.radians(lat), math.radians(lon)
        east = np.array([-math.sin(lo), math.cos(lo), 0])
        north = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
        up_ = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
        el = np.degrees(np.arcsin(di @ up_ / np.linalg.norm(di, axis=1)))
        a1, a2 = np.degrees(np.arctan2(di @ east, di @ north)) % 360, el
        if angle_type == 'RADEC':
            dj = (ICRS_to_J2000 @ d.T).T
            a1 = np.degrees(np.arctan2(dj[:, 1], dj[:, 0])) % 360
            a2 = np.degrees(np.arcsin(dj[:, 2] / np.linalg.norm(dj, axis=1)))
        vis = el > 5
        if not np.any(vis):
            continue
        rate = np.sum(d * (v - stn.at(tr).velocity.km_per_s.T), axis=1) / down
        rng_km = 0.5 * (down + up) * range_scale
        sig = {'AZEL': 0.005, 'RADEC': 1 / 3600}[angle_type]
        nz = (lambda s: rng.normal(0, s)) if noise else (lambda s: 0.0)
        lines = [f'META_START\nTIME_SYSTEM = UTC\nPARTICIPANT_1 = {name}\nPARTICIPANT_2 = {sc}\nMODE = SEQUENTIAL\n'
                 f'PATH = 1,2,1\nANGLE_TYPE = {angle_type}\nRANGE_UNITS = km\n'
                 + ('REFERENCE_FRAME = EME2000\n' if angle_type == 'RADEC' else '') + 'META_STOP\nDATA_START']
        for k, s in zip(np.flatnonzero(vis), np.array(tr.utc_strftime('%Y-%m-%dT%H:%M:%S'))[vis]):
            if 'ANGLE' in kinds:
                lines += [f'ANGLE_1 = {s} {a1[k] + nz(sig):.9f}', f'ANGLE_2 = {s} {a2[k] + nz(sig):.9f}']
            if 'RANGE' in kinds:
                lines.append(f'RANGE = {s} {rng_km[k] + nz(0.005):.6f}')
            if 'DOPPLER' in kinds:
                lines.append(f'DOPPLER_INSTANTANEOUS = {s} {rate[k] + nz(1e-5):.9f}')
        segs.append('\n'.join(lines + ['DATA_STOP']))
    return 'CCSDS_TDM_VERS = 2.0\nCREATION_DATE = 2008-08-02T00:00:00\nORIGINATOR = TEST\n' + '\n'.join(segs) + '\n'


_ORBIT = {}


def _orbit():
    if not _ORBIT:
        t0 = CTX.ts.utc(2008, 8, 1)
        st = TLE.vf.at(t0)
        _ORBIT['at'] = _truth(t0, np.concatenate([st.position.km, st.velocity.km_per_s]))
    return _ORBIT['at']


def _od_error(obs, hours=36):
    tq = _times(2008, 8, 1, np.linspace(0, hours * 3600, 300))
    return np.linalg.norm(_gcrs(obs, tq) - _orbit()(tq)[0], axis=1).max()


def test_tdm_orbit_determination():
    # The orbit determined from a TDM is the orbit the measurements were made from: exactly from
    # angles, range and Doppler without noise; within tens of metres with noise (and with ranges
    # of the whole round trip, which are recognised and halved); from angles alone (RADEC).
    at = _orbit()
    tdm = _tdm(at, 9)
    obs = build_observer(CTX, {'type': 'ccsds', 'text': tdm, 'stations': STATIONS_TEXT})
    od = obs.info['od']
    assert obs.label == 'TDM' and obs.name == 'ISS' and od['n_used'] > 300 and od['n_rejected'] == 0
    assert _od_error(obs) < 0.002, _od_error(obs)                          # 2 m over 36 h
    noisy = build_observer(CTX, {'type': 'ccsds', 'text': _tdm(at, 9, noise=True, range_scale=2.0),
                                 'stations': [{'name': k, 'lat': v[0], 'lon': v[1], 'elevation_m': v[2]}
                                              for k, v in STATIONS.items()]})
    assert any('往復の値として扱いました' in n for n in noisy.notes), noisy.notes
    assert _od_error(noisy) < 0.15, _od_error(noisy)
    rms = noisy.info['od']['types']        # the noise put in: 18", 5 m (2.5 m once halved), 10 mm/s
    assert 0.7 < rms['azel']['rms'] / 18 < 1.3 and 0.7 < rms['range']['rms'] / 2.5 < 1.3, rms
    assert 0.7 < rms['doppler']['rms'] / 10 < 1.3 and rms['range']['factor'] == 0.5, rms
    radec = build_observer(CTX, {'type': 'ccsds', 'text': _tdm(at, 9, kinds=('ANGLE',), angle_type='RADEC'),
                                 'stations': STATIONS_TEXT})
    assert _od_error(radec) < 0.05, _od_error(radec)


def test_tdm_with_orbit_message_and_errors():
    # An OPM of the same spacecraft given with the TDM is the first guess; without the positions
    # of the stations, or with an unknown spacecraft, the message says what to give.
    at = _orbit()
    two = {k: STATIONS[k] for k in ('USUDA', 'OKINAWA')}
    tdm = _tdm(at, 6, kinds=('RANGE', 'DOPPLER'), stations=two)
    t0 = _times(2008, 8, 1, [0.0])
    opm = _opm(t0, _eme2000(t0)[0]).replace(f'OBJECT_NAME = {NAME}', 'OBJECT_NAME = ISS')
    obs = build_observer(CTX, {'type': 'ccsds', 'text': [tdm, opm], 'stations': STATIONS_TEXT})
    assert obs.info['apriori'] == 'OPM' and _od_error(obs, 24) < 0.01, _od_error(obs, 24)
    msg = _error({'text': tdm})
    assert '観測局の位置' in msg and 'USUDA' in msg and 'ISS' in msg, msg
    assert 'stations' in _error({'text': tdm}, 'en')
    usuda = 'USUDA 36.1325 138.3628 1456'
    assert 'どれが衛星か' in _error({'text': tdm, 'stations': usuda})
    assert '観測局 OKINAWA の位置' in _error({'text': tdm, 'stations': usuda, 'object': 'ISS'})
    assert '「名前 緯度 経度 標高(m)」' in _error({'text': tdm, 'stations': 'USUDA 36.1'})
    assert '範囲外' in _error({'text': tdm, 'stations': 'USUDA 136.1 138.3 0\nOKINAWA 26.5 127.9 0'})
    assert 'TDM に' in _error({'text': tdm.split('DATA_START')[0] + 'DATA_START\nDATA_STOP\n',
                               'stations': STATIONS_TEXT})


def test_errors_in_every_language():
    # Wrong input gets a message that says what is wrong, in the language of the request.
    t0 = _times(2008, 8, 1, [0.0])
    opm = _opm(t0, _eme2000(t0)[0])
    oem = _oem(_times(2008, 8, 1, np.arange(0, 600, 60.0)))
    lines = oem.splitlines()
    cases = [
        ({'text': ''}, 'CCSDS のメッセージ（OEM・OPM・OMM・OCM・TDM）を入力してください', 'Enter a CCSDS message'),
        ({'text': 'hello'}, 'CCSDS_xxx_VERS', 'CCSDS_xxx_VERS'),
        ({'text': opm.replace('TIME_SYSTEM = UTC', 'TIME_SYSTEM = MARS_LOCAL')}, '時刻系 MARS_LOCAL', 'time system'),
        ({'text': opm.replace('REF_FRAME = EME2000', 'REF_FRAME = FOO')}, '座標系 FOO', 'reference frame FOO'),
        ({'text': opm.replace('CENTER_NAME = EARTH', 'CENTER_NAME = VULCAN')}, '中心天体 VULCAN', 'centre VULCAN'),
        ({'text': re.sub(r'\nX = \S+', '\nX = abc', opm, count=1)}, '「abc」を数値として読めません', '"abc"'),
        ({'text': re.sub(r'EPOCH = .*\n', '', opm)}, 'OPM に EPOCH がありません', 'The OPM has no EPOCH'),
        ({'text': '\n'.join(lines[:-2] + [lines[-1], lines[-2]])}, '増える順', 'increasing order'),
        ({'text': '<!DOCTYPE x [<!ENTITY a "b">]><opm/>'}, 'DOCTYPE', 'DOCTYPE'),
        ({'text': '<opm><body/>'}, 'XML を解釈できません', 'Cannot interpret the XML'),
        ({'text': OMM_KVN.replace('= SGP4\n', '= DSST\n')}, 'DSST', 'DSST'),
        ({'text': OMM_KVN.replace('REF_FRAME = TEME', 'REF_FRAME = EME2000')}, 'REF_FRAME は TEME', 'TEME'),
        ({'text': [opm, opm.replace(NAME, 'HTV-1').replace('1998-067A', '2009-048A')]}, '2 個の天体', '2 objects'),
        ({'text': opm, 'object': 'HTV-1'}, '「HTV-1」は入力にありません', '"HTV-1" is not in the input'),
        ({'text': re.sub(r'\nX = \S+', '\nX = 100.0', opm, count=1)}, '近地点は地球内部', 'inside the Earth'),
    ]
    for spec, ja, en in cases:
        msg_ja, msg_en = _error(spec), _error(spec, 'en')
        assert ja in msg_ja, (spec.get('text', '')[:60], msg_ja)
        assert en in msg_en, (spec.get('text', '')[:60], msg_en)
        for lang in i18n.LANGUAGES[1:]:
            msg = msg_en if lang == 'en' else _error(spec, lang)
            assert msg != msg_ja and not KANA.search(msg), (lang, msg)


def test_tles_and_blocks_in_kvn():
    # TLEs may be given with the CCSDS messages; a line of a message before a TLE is not its name.
    t = _times(2008, 8, 1, np.arange(0, 600, 60.0))
    msgs = ccsds.parse(_oem(t) + f'{L1}\n{L2}\n' + 'COMMENT a named TLE follows\n' + f'{NAME}\n{L1}\n{L2}\n')
    oem = next(m for m in msgs if m.kind == 'OEM')
    assert len(oem.segments[0]['states']) == 10
    assert [m.segments[0]['name'] for m in msgs if m.kind == 'TLE'] == ['', NAME]
    # OPM and OMM have no blocks: META_START / META_STOP lines that some writers add do not matter
    t0 = _times(2008, 8, 1, [0.0])
    opm = _opm(t0, _eme2000(t0)[0])
    blocks = opm.replace('OBJECT_NAME', 'META_START\nOBJECT_NAME').replace('COMMENT State Vector', 'META_STOP')
    tq = _times(2008, 8, 1, [600.0])
    assert np.abs(_gcrs(build_observer(CTX, {'type': 'ccsds', 'text': blocks}), tq)
                  - _gcrs(build_observer(CTX, {'type': 'ccsds', 'text': opm}), tq)).max() < 1e-9
    # an OMM and a TLE of the same object: the OMM is used with a note
    obs = build_observer(CTX, {'type': 'ccsds', 'text': OMM_KVN + f'{NAME}\n{L1}\n{L2}\n'})
    assert obs.info['message'] == 'OMM' and any('TLE は使わず' in n for n in obs.notes), obs.notes


def test_cli_and_api():
    # cli.py --ccsds (with --ccsds-object, --station, --sweep for an OMM) and /api/ccsds/inspect.
    with tempfile.TemporaryDirectory() as tmp:
        def write(name, text):
            path = os.path.join(tmp, name)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            return path
        omm = write('iss.omm', OMM_KVN)
        period = ['--start', '2008-09-20', '--end', '2008-09-27']
        code, d = _cli_json(['--ccsds', omm, '--dry-run'] + period)
        assert code == 0 and d['ok'] and d['observer']['model'] == 'ccsds' and d['observer']['message'] == 'OMM'
        assert d['request']['observer']['type'] == 'ccsds' and 'MEAN_MOTION' in d['request']['observer']['text']
        code, d = _cli_json(['--ccsds', omm, '--sweep', '90', '--phenomena', 'moon', '--start', '2008-07-25',
                             '--end', '2008-08-05'])
        assert code == 0 and d['groups'][0]['n_phases'] == 4, d.get('error')
        code, tle = _cli_json(['--tle', write('iss.txt', f'{NAME}\n{L1}\n{L2}\n'), '--sweep', '90', '--phenomena',
                               'moon', '--start', '2008-07-25', '--end', '2008-08-05'])
        for a, b in zip(d['groups'][0]['rows'], tle['groups'][0]['rows']):      # the OMM is the TLE
            a, b = a['best'], b['best']
            assert (a['c1'], a['max'], a['c4']) == (b['c1'], b['max'], b['c4'])
            assert abs(a['magnitude'] - b['magnitude']) < 1e-6
        oem = write('iss.oem', _oem(_times(2008, 8, 1, np.arange(0, 3601, 60.0))))
        code, d = _cli_json(['--ccsds', oem, '--sweep', '90'] + period)      # known once the file is read
        assert code == 1 and '--sweep' in d['error'] and 'OMM' in d['error']
        code, d = _cli_json(['--city', '東京', '--station', 'USUDA=36.1,138.4,1456'] + period)
        assert code == 2 and '--ccsds' in d['error']
        code, d = _cli_json(['--ccsds', oem, '--station', 'USUDA=36.1'] + period)
        assert code == 2 and 'USUDA=36.1325,138.3628,1456' in d['error']
        code, d = _cli_json(['--ccsds', os.path.join(tmp, 'none.oem')] + period)
        assert code == 1 and not d['ok']
        code, d = _cli_json(['--ccsds', oem, '--ccsds', omm, '--ccsds-object', '1998-067A', '--dry-run',
                             '--start', '2008-08-01', '--end', '2008-08-02'])
        assert code == 0 and d['observer']['message'] == 'OEM' and any('OMM は使わず' in w for w in d['warnings'])
        tdm = write('iss.tdm', _tdm(_orbit(), 9, kinds=('ANGLE', 'RANGE')))
        stations = write('stations.txt', STATIONS_TEXT)
        code, d = _cli_json(['--ccsds', tdm, '--stations', stations, '--dry-run', '--start', '2008-08-01',
                             '--end', '2008-08-02'])
        assert code == 0 and d['observer']['od']['n_used'] > 200, d.get('error')
        code, d = _cli_json(['--ccsds', tdm, '--dry-run', '--start', '2008-08-01', '--end', '2008-08-02',
                             '--lang', 'en'])
        assert code == 1 and 'stations' in d['error']
    out = server.ccsds_inspect(server.CCSDSRequest(text=OMM_KVN))
    assert out['messages'][0]['message'] == 'OMM' and out['observer']['message'] == 'OMM' and not out['tdm']
    out = server.ccsds_inspect(server.CCSDSRequest(text=_tdm(_orbit(), 9, kinds=('RANGE',)), stations='USUDA 36 138 0'))
    assert out['tdm'] and 'ISS' in out['participants'] and 'USUDA' not in out['missing_stations']
    assert 'observer' not in out
    # what cannot be computed is told in the answer, what cannot be read is a 400, in the language
    # of the page
    token = i18n.set_lang('fr')
    try:
        out = server.ccsds_inspect(server.CCSDSRequest(text='CCSDS_OPM_VERS = 3.0\nEPOCH = 2008-08-01T00:00:00\n'))
        assert out['error'] == 'Le message OPM ne contient pas X', out
        server.ccsds_inspect(server.CCSDSRequest(text='CCSDS_OPM_VERS = 3.0\nEPOCH 2008\n'))
        raise AssertionError('no error')
    except server.HTTPException as exc:
        assert exc.status_code == 400 and exc.detail.startswith('Ligne 2 du message OPM incompréhensible'), exc.detail
    finally:
        i18n.reset_lang(token)


JAPANESE = re.compile(r'[ぁ-ゖァ-ヺ一-鿿]')     # kana and kanji


def test_messages_translated():
    # Every message of the CCSDS modules has its six translations (as tests/test_cli.py checks
    # those of cli.py).
    root = os.path.join(os.path.dirname(__file__), '..', 'eclipsecalc')
    missing = []
    for name in ('ccsds.py', 'od.py', 'propagate.py', 'observers.py'):
        tree = ast.parse(open(os.path.join(root, name), encoding='utf-8').read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, 'id', getattr(n.func, 'attr', '')) in ('tr', '_err') \
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                text = n.args[0].value
                if JAPANESE.search(text) and text not in i18n.MESSAGES:
                    missing.append(f'{name}: {text}')
    assert not missing, missing


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

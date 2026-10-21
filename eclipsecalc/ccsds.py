"""CCSDS navigation data messages as observers.

Reads the Orbit Data Messages of CCSDS 502.0-B-3 (OPM, OMM, OEM, OCM; also the version 1 and 2
messages) and the Tracking Data Message of CCSDS 503.0-B-2 (TDM), in KVN or XML (single messages
or NDM combined instantiations, with or without the ndm namespace), and the OMM in the JSON and CSV
forms of CelesTrak / Space-Track.  Several messages may be given together (e.g. a TDM and an OPM, or
the OMMs of a constellation); TLEs may be mixed in.

  * OEM, and OCM trajectory blocks with enough states: interpolated as given (INTERPOLATION /
    INTERPOLATION_DEGREE, else 7th-degree Hermite on positions and velocities).
  * OPM, and OCM with a single state (or INTERPOLATION = PROPAGATE): propagated numerically
    (propagate.py) with the spacecraft parameters (drag, solar radiation pressure) and impulsive
    maneuvers of the message.
  * OMM: SGP4/SDP4 (MEAN_ELEMENT_THEORY = SGP4 / SGP, TEME, UTC) like a TLE.
  * TDM: an orbit determined from the tracking data (od.py), with the positions of the ground
    stations given separately (a TDM does not carry them) and optionally an orbit message or TLE of
    the same spacecraft as the first guess.

Every state is converted to geocentric GCRS: EME2000 / J2000 (frame bias), GCRF / ICRF, TEME, TOD,
MOD, ITRF* / EFG / TDR / GRC (Earth rotation, without polar motion), J2000_ECLIPTIC; centres other
than the Earth (the Moon, the Sun, the solar system or Earth-Moon barycentre, the planets) by the
DE440 ephemeris of the context.  Times: UTC, TAI, TT, GPS, TDB, TCB, TCG, UT1 (and the GNSS times).
"""
import csv
import io
import json
import math
import re
import xml.etree.ElementTree as ET

import numpy as np
from skyfield.framelib import ICRS_to_J2000, ecliptic_J2000_frame, itrs, tirs
from skyfield.sgp4lib import TEME

from .constants import AU_KM, DAY_S, EARTH_OMEGA, GM_EARTH, WGS84_A
from .i18n import tr
from .observers import ObserverError

MAX_TEXT = 64_000_000                     # characters of one input
MAX_STATES = 2_000_000                    # states of all the ephemerides
KINDS = ('OPM', 'OMM', 'OEM', 'OCM', 'TDM')

_UTC_FROM_TT = 2441317.5 + 42.184 / 86400.0      # 1972-01-01 (as timeutil): UTC before is UT1
L_G = 6.969290134e-10
L_B = 1.550519768e-8
T0_TCB = 2443144.5003725
TDB0_S = -6.55e-5


class CCSDSError(ObserverError):
    pass


def _err(text, /, **kw):
    return CCSDSError(tr(text, **kw))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class Message:
    """One message: kind (OPM / OMM / OEM / OCM / TDM / TLE), version, format (KVN / XML / JSON /
    CSV / TLE), header (dict) and segments.

      OPM, OMM: [{'kv': keywords of metadata and data, 'maneuvers': [dict], 'line': n}]
      OEM:      [{'meta': dict, 'states': [(line, tokens)]}]
      OCM:      [{'meta': dict, 'traj': [{'kw': dict, 'lines': [(line, tokens)]}], 'phys': dict,
                  'man': [{'kw': dict, 'lines': [...]}], 'pert': dict, 'od': dict}]
      TDM:      [{'meta': dict, 'records': [(line, keyword, epoch, value)]}]
      TLE:      [{'line1', 'line2', 'name'}]
    """

    def __init__(self, kind, version, fmt, line=None):
        self.kind, self.version, self.fmt, self.line = kind, version, fmt, line
        self.header = {}
        self.segments = []

    def label(self):
        v = f' {self.version}' if self.version else ''
        return f'{self.kind}{v}' + (f' ({self.fmt})' if self.fmt in ('XML', 'JSON', 'CSV') else '')


def text_value(v):
    """A text value as the standard compares it: underscores are blanks, case and runs of blanks
    do not matter."""
    return ' '.join(str(v or '').replace('_', ' ').split()).upper()


_VERS = re.compile(r'^CCSDS_(OPM|OMM|OEM|OCM|TDM)_VERS\s*=\s*(\S*)', re.I)
_KV = re.compile(r'^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')
_UNITS = re.compile(r'^(.*?\S)\s+\[[^\]]*\]$')
_TLE1 = re.compile(r'^1 [ 0-9A-Z]{5}[A-Z ] ')
_TLE2 = re.compile(r'^2 [ 0-9A-Z]{5} ')
_BLOCKS = {'META', 'COVARIANCE', 'DATA', 'TRAJ', 'PHYS', 'COV', 'MAN', 'PERT', 'OD', 'USER'}
_EDGE = re.compile(r'^[A-Z][A-Z0-9_]*_(START|STOP)$')
_NUMBER = re.compile(r'^[-+]?(\d+\.?\d*|\.\d+)([EeDd][-+]?\d+)?$')
_EPOCH = re.compile(r'^\d{4}-(\d{2}-\d{2}|\d{3})T')


def _value(v):
    m = _UNITS.match(v)
    return m.group(1) if m else v


class _Kvn:
    """Builds a Message from the lines of one KVN message."""

    def __init__(self, kind, version, line):
        self.msg = Message(kind, version, 'KVN', line)
        self.msg.header['CCSDS_%s_VERS' % kind] = version
        self.block = 'HEADER'
        self.seg = None
        self.sub = None

    def _error(self, no, text):
        raise _err('{kind} の {line} 行目を解釈できません: {text}', kind=self.msg.kind, line=no, text=text[:80])

    def feed(self, no, s):
        kind = self.msg.kind
        up = s.upper()
        if up.startswith('COMMENT') and (len(s) == 7 or s[7] in ' \t'):
            return
        if _EDGE.match(up):
            if kind in ('OPM', 'OMM'):            # they have no blocks (some writers add META_START etc.)
                return
            name, edge = up.rsplit('_', 1)
            if name in _BLOCKS:
                return self._edge(no, s, name, edge)
        m = _KV.match(s)
        if kind in ('OPM', 'OMM'):
            if not m:
                self._error(no, s)
            key, val = m.group(1).upper(), _value(m.group(2))
            if not self.msg.segments:
                self.msg.segments.append({'kv': {}, 'maneuvers': [], 'line': no})
            seg = self.msg.segments[0]
            if key.startswith('MAN_') and kind == 'OPM':
                if key == 'MAN_EPOCH_IGNITION' or not seg['maneuvers']:
                    seg['maneuvers'].append({'line': no})
                seg['maneuvers'][-1][key] = val
            elif key in ('CREATION_DATE', 'ORIGINATOR', 'MESSAGE_ID', 'CLASSIFICATION'):
                self.msg.header[key] = val
            else:
                seg['kv'].setdefault(key, val)
            return
        if self.block == 'HEADER':
            if not m:
                self._error(no, s)
            self.msg.header[m.group(1).upper()] = _value(m.group(2))
            return
        if self.block == 'META':
            if not m:
                self._error(no, s)
            self.seg['meta'][m.group(1).upper()] = _value(m.group(2))
            return
        if self.block in ('COVARIANCE', 'COV'):
            return
        if kind == 'OEM' and self.block == 'OEMDATA':
            self.seg['states'].append((no, s.split()))
            return
        if kind == 'TDM' and self.block == 'DATA':
            if not m:
                self._error(no, s)
            parts = m.group(2).split()
            if len(parts) < 2:
                self._error(no, s)
            self.seg['records'].append((no, m.group(1).upper(), parts[0], parts[1]))
            return
        if kind == 'OCM' and self.block in ('TRAJ', 'MAN'):
            if m and not re.match(r'^[-+]?\d', s):
                self.sub['kw'][m.group(1).upper()] = _value(m.group(2))
            else:
                self.sub['lines'].append((no, s.split()))
            return
        if kind == 'OCM' and self.block in ('PHYS', 'PERT', 'OD', 'USER'):
            if m:
                self.sub[m.group(1).upper()] = _value(m.group(2))
            return
        self._error(no, s)

    def _edge(self, no, s, name, edge):
        kind = self.msg.kind
        if edge == 'START':
            if name == 'META':
                if kind == 'OCM' and self.msg.segments:
                    self._error(no, s)
                self.seg = {'meta': {}, 'line': no}
                if kind == 'OEM':
                    self.seg['states'] = []
                elif kind == 'TDM':
                    self.seg['records'] = []
                elif kind == 'OCM':
                    self.seg.update(traj=[], phys=None, man=[], pert=None, od=None)
                self.msg.segments.append(self.seg)
                self.block = 'META'
            elif self.seg is None:
                self._error(no, s)
            elif name in ('TRAJ', 'MAN'):
                self.sub = {'kw': {}, 'lines': [], 'line': no}
                self.seg['traj' if name == 'TRAJ' else 'man'].append(self.sub)
                self.block = name
            elif name in ('PHYS', 'PERT', 'OD', 'USER'):
                self.sub = {}
                if name != 'USER':
                    self.seg[name.lower()] = self.sub
                self.block = name
            else:
                self.block = name
            return
        # *_STOP
        if name == 'META':
            self.block = 'OEMDATA' if kind == 'OEM' else 'BODY'
        elif name in ('COVARIANCE',):
            self.block = 'OEMDATA'
        else:
            self.block = 'BODY'

    def finish(self):
        msg = self.msg
        if not msg.segments:
            raise _err('{kind} にデータがありません', kind=msg.kind)
        return msg


def _name_line(s):
    """Whether the line before a TLE can be its name (not a line of a CCSDS message)."""
    t = s.strip()
    up = t.upper()
    if not t or '=' in t or _EDGE.match(up) or up.startswith('COMMENT') or _VERS.match(t):
        return False
    tokens = t.split()
    return len(tokens) < 3 or not all(_NUMBER.match(x) or _EPOCH.match(x) for x in tokens)


def _split_tles(lines):
    """TLE line pairs (with a preceding name line) taken out of the lines -> (TLE messages, rest)."""
    out, rest = [], []
    k = 0
    while k < len(lines):
        no, s = lines[k]
        t = s.strip()
        if _TLE1.match(t) and k + 1 < len(lines) and _TLE2.match(lines[k + 1][1].strip()):
            msg = Message('TLE', '', 'TLE', no)
            name = ''
            if rest and _name_line(rest[-1][1]):
                name = rest.pop()[1].strip()
                if name.startswith('0 '):
                    name = name[2:].strip()
            msg.segments.append({'line1': t, 'line2': lines[k + 1][1].strip(), 'name': name})
            out.append(msg)
            k += 2
            continue
        rest.append((no, s))
        k += 1
    return out, rest


def _parse_kvn(lines):
    tles, lines = _split_tles(lines)
    msgs, cur = [], None
    for no, raw in lines:
        s = raw.strip()
        if not s:
            continue
        m = _VERS.match(s)
        if m:
            cur = _Kvn(m.group(1).upper(), m.group(2), no)
            msgs.append(cur)
            continue
        if cur is None:
            raise _err('{line} 行目: CCSDS のメッセージは CCSDS_xxx_VERS の行（例: CCSDS_OEM_VERS = 3.0）で始めてください', line=no)
        cur.feed(no, s)
    return [b.finish() for b in msgs] + tles


# --- XML --------------------------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _flat(el):
    """Keyword children of an element -> dict (COMMENT and structure tags left out)."""
    out = {}
    for c in el:
        t = _local(c.tag)
        if t and t.upper() == t and t != 'COMMENT' and len(c) == 0:
            out[t] = (c.text or '').strip()
    return out


def _parse_xml(text):
    if re.search(r'<!(DOCTYPE|ENTITY)', text, re.I):
        raise _err('XML の DOCTYPE・ENTITY 宣言は使えません')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise _err('XML を解釈できません: {exc}', exc=exc)
    top = _local(root.tag).lower()
    elems = [c for c in root if _local(c.tag).lower() in ('opm', 'omm', 'oem', 'ocm', 'tdm')] if top == 'ndm' else [root]
    if not elems:
        raise _err('XML に CCSDS のメッセージ（opm, omm, oem, ocm, tdm）がありません')
    msgs = []
    for k, el in enumerate(elems):
        kind = _local(el.tag).upper()
        if kind not in KINDS:
            raise _err('XML のルート要素 <{tag}> は CCSDS のメッセージではありません', tag=_local(el.tag))
        msg = Message(kind, el.attrib.get('version', ''), 'XML', k + 1)
        for c in el:
            if _local(c.tag) == 'header':
                msg.header.update(_flat(c))
        body = next((c for c in el if _local(c.tag) == 'body'), None)
        if body is None:
            raise _err('{kind} の XML に <body> がありません', kind=kind)
        for seg_el in (c for c in body if _local(c.tag) == 'segment'):
            meta = next((c for c in seg_el if _local(c.tag) == 'metadata'), None)
            data = next((c for c in seg_el if _local(c.tag) == 'data'), None)
            meta = _flat(meta) if meta is not None else {}
            if kind in ('OPM', 'OMM'):
                kv, mans = dict(meta), []
                for blk in (data if data is not None else []):
                    t = _local(blk.tag)
                    if t == 'maneuverParameters':
                        mans.append(_flat(blk))
                    elif t in ('covarianceMatrix', 'userDefinedParameters'):
                        continue
                    elif len(blk):
                        kv.update(_flat(blk))
                    elif t.upper() == t and t != 'COMMENT':
                        kv[t] = (blk.text or '').strip()
                msg.segments.append({'kv': kv, 'maneuvers': mans, 'line': None})
            elif kind == 'OEM':
                states = []
                for sv in (data if data is not None else []):
                    if _local(sv.tag) != 'stateVector':
                        continue
                    f = _flat(sv)
                    states.append((None, [f.get(k_, '') for k_ in ('EPOCH', 'X', 'Y', 'Z', 'X_DOT', 'Y_DOT', 'Z_DOT')]
                                   + [f[k_] for k_ in ('X_DDOT', 'Y_DDOT', 'Z_DDOT') if k_ in f]))
                msg.segments.append({'meta': meta, 'states': states, 'line': None})
            elif kind == 'OCM':
                seg = {'meta': meta, 'traj': [], 'phys': None, 'man': [], 'pert': None, 'od': None, 'line': None}
                for blk in (data if data is not None else []):
                    t = _local(blk.tag)
                    if t in ('traj', 'man'):
                        line_tag = 'trajLine' if t == 'traj' else 'manLine'
                        seg[t].append({'kw': _flat(blk), 'line': None,
                                       'lines': [(None, (c.text or '').split()) for c in blk if _local(c.tag) == line_tag]})
                    elif t in ('phys', 'pert', 'od'):
                        seg[t] = _flat(blk)
                msg.segments.append(seg)
            else:
                recs = []
                for ob in (data if data is not None else []):
                    if _local(ob.tag) != 'observation':
                        continue
                    f = _flat(ob)
                    ep = f.pop('EPOCH', '')
                    for key, val in f.items():
                        recs.append((None, key, ep, val))
                msg.segments.append({'meta': meta, 'records': recs, 'line': None})
        if not msg.segments:
            raise _err('{kind} の XML に <segment> がありません', kind=kind)
        msgs.append(msg)
    return msgs


# --- OMM as JSON / CSV (CelesTrak, Space-Track) -------------------------------------------------------
def _omm_from_dict(d, fmt, k):
    d = {str(key).upper(): ('' if v is None else str(v)) for key, v in d.items()}
    if 'EPOCH' not in d or not ({'MEAN_MOTION', 'SEMI_MAJOR_AXIS'} & set(d)):
        raise _err('{fmt} の {n} 件目は OMM（EPOCH と MEAN_MOTION を持つ平均軌道要素）ではありません', fmt=fmt, n=k + 1)
    msg = Message('OMM', d.get('CCSDS_OMM_VERS', ''), fmt, k + 1)
    for key in ('CREATION_DATE', 'ORIGINATOR', 'MESSAGE_ID'):
        if d.get(key):
            msg.header[key] = d[key]
    msg.segments.append({'kv': d, 'maneuvers': [], 'line': None})
    return msg


def _parse_json(text):
    try:
        data = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError as exc:
        raise _err('JSON を解釈できません: {exc}', exc=exc)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data or not all(isinstance(d, dict) for d in data):
        raise _err('JSON は OMM のオブジェクト（またはその配列）にしてください')
    return [_omm_from_dict(d, 'JSON', k) for k, d in enumerate(data)]


def _parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise _err('CSV に OMM の行がありません')
    return [_omm_from_dict(r, 'CSV', k) for k, r in enumerate(rows)]


def parse(text):
    """All the messages of the input text (or of a list of texts, e.g. files) -> [Message]."""
    if isinstance(text, (list, tuple)):
        if not text:
            raise _err('CCSDS のメッセージ（OEM・OPM・OMM・OCM・TDM）を入力してください')
        if sum(len(x) for x in text if isinstance(x, str)) > MAX_TEXT:
            raise _err('入力が大きすぎます（{mb:.0f} MB まで）', mb=MAX_TEXT / 1e6)
        return [m for x in text for m in parse(x)]
    if not isinstance(text, str) or not text.strip():
        raise _err('CCSDS のメッセージ（OEM・OPM・OMM・OCM・TDM）を入力してください')
    if len(text) > MAX_TEXT:
        raise _err('入力が大きすぎます（{mb:.0f} MB まで）', mb=MAX_TEXT / 1e6)
    text = text.lstrip('﻿')
    head = text.lstrip()
    if head.startswith('<'):
        return _parse_xml(head)
    if head.startswith(('[', '{')):
        return _parse_json(head)
    first = head.split('\n', 1)[0]
    if ',' in first and ('MEAN_MOTION' in first.upper() or 'NORAD_CAT_ID' in first.upper()) and '=' not in first:
        return _parse_csv(head)
    lines = list(enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n'), start=1))
    msgs = _parse_kvn(lines)
    if not msgs:
        raise _err('CCSDS のメッセージ（CCSDS_OEM_VERS などで始まる KVN、XML、OMM の JSON・CSV）も TLE も見つかりません')
    return msgs


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------
_TIME = re.compile(r'^([+-]?\d{4})-(?:(\d{1,2})-(\d{1,2})|(\d{1,3}))T(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?Z?$', re.I)
_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
TIME_SYSTEMS = ('UTC', 'TAI', 'TT', 'TDT', 'GPS', 'GST', 'QZSST', 'BDT', 'TDB', 'ET', 'TCB', 'TCG', 'UT1')


def _jdn(y, mo, d):
    """Julian day number (integer at noon) of Gregorian dates (arrays)."""
    a = (14 - mo) // 12
    yy = y + 4800 - a
    mm = mo + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def _calendar(strings):
    """Time strings of CCSDS (YYYY-MM-DDThh:mm:ss[.d][Z] or YYYY-DDDThh:mm:ss[.d][Z]) -> integer
    arrays year, month, day, hour, minute and seconds (float)."""
    n = len(strings)
    y = np.empty(n, int)
    mo = np.empty(n, int)
    d = np.empty(n, int)
    h = np.empty(n, int)
    mi = np.empty(n, int)
    sec = np.empty(n, float)
    for k, s in enumerate(strings):
        m = _TIME.match(str(s).strip())
        ok = bool(m)
        if ok:
            yy = int(m.group(1))
            if m.group(4):
                doy = int(m.group(4))
                leap = yy % 4 == 0 and (yy % 100 != 0 or yy % 400 == 0)
                ok = 1 <= doy <= 365 + leap
                mm, dd = 1, doy
            else:
                mm, dd = int(m.group(2)), int(m.group(3))
                leap = yy % 4 == 0 and (yy % 100 != 0 or yy % 400 == 0)
                ok = 1 <= mm <= 12 and 1 <= dd <= _DAYS[mm - 1] + (mm == 2 and leap)
            hh, mn, ss = int(m.group(5)), int(m.group(6)), float(m.group(7) or 0)
            ok = ok and hh < 24 and mn < 60 and ss < 61
        if not ok:
            raise _err('時刻「{text}」を解釈できません（CCSDS の形式 YYYY-MM-DDThh:mm:ss または YYYY-DDDThh:mm:ss）', text=str(s)[:40])
        y[k], mo[k], d[k], h[k], mi[k], sec[k] = yy, mm, dd, hh, mn, ss
    return y, mo, d, h, mi, sec


def time_system(value):
    ts = text_value(value or 'UTC').replace(' ', '')
    if ts not in TIME_SYSTEMS:
        raise _err('時刻系 {name} には対応していません（UTC, TAI, TT, GPS, TDB, TCB, TCG, UT1 など）', name=value)
    return ts


def epochs(ctx, strings, system='UTC'):
    """Skyfield Time (vector) of CCSDS time strings in a time system."""
    system = time_system(system)
    ts = ctx.ts
    y, mo, d, h, mi, sec = _calendar(strings)
    if np.any(sec >= 60) and system != 'UTC':
        raise _err('うるう秒（60 秒）は UTC の時刻にしか書けません')
    if system == 'UTC':
        t = ts.utc(y, mo, d, h, mi, sec)
        bad = (sec >= 60) & (np.asarray(t.utc[5]) < 60)
        if np.any(bad):
            k = int(np.argmax(bad))
            raise _err('時刻「{text}」の 60 秒はうるう秒ではありません', text=str(strings[k])[:40])
        early = np.asarray(t.tt) < _UTC_FROM_TT
        if np.any(early):                    # before 1972: UT (as the rest of the calculator)
            u = ts.ut1(y, mo, d, h, mi, sec)
            t = ts.tt_jd(np.where(early, u.tt, t.tt))
        return t
    if system in ('TAI', 'GPS', 'GST', 'QZSST', 'BDT'):
        off = {'TAI': 0.0, 'GPS': 19.0, 'GST': 19.0, 'QZSST': 19.0, 'BDT': 33.0}[system]
        return ts.tai(y, mo, d, h, mi, sec + off)
    if system == 'UT1':
        return ts.ut1(y, mo, d, h, mi, sec)
    whole = _jdn(y, mo, d) - 0.5
    frac = (h * 3600.0 + mi * 60.0 + sec) / DAY_S
    if system in ('TT', 'TDT'):
        return ts.tt_jd(whole, frac)
    if system == 'TCG':
        return ts.tt_jd(whole, frac - L_G * ((whole - T0_TCB) + frac))
    if system in ('TDB', 'ET'):
        return ts.tdb_jd(whole, frac)
    return ts.tdb_jd(whole, frac - L_B * ((whole - T0_TCB) + frac) + TDB0_S / DAY_S)      # TCB


def epoch(ctx, string, system='UTC'):
    t = epochs(ctx, [string], system)
    return ctx.ts.tt_jd(t.whole[0], t.tt_fraction[0])


def is_relative(token):
    return bool(re.match(r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$', str(token).strip()))


# ---------------------------------------------------------------------------
# Frames and centres
# ---------------------------------------------------------------------------
CENTERS = {
    'EARTH': 'earth', 'MOON': 'moon', 'SUN': 'sun', 'SOLAR SYSTEM BARYCENTER': 'ssb', 'SSB': 'ssb',
    'EARTH BARYCENTER': 'emb', 'EARTH-MOON BARYCENTER': 'emb', 'EARTH MOON BARYCENTER': 'emb', 'EMB': 'emb',
    'MERCURY': 'mercury', 'MERCURY BARYCENTER': 'mercury', 'VENUS': 'venus', 'VENUS BARYCENTER': 'venus',
    'MARS': 'mars', 'MARS BARYCENTER': 'mars', 'JUPITER': 'jupiter', 'JUPITER BARYCENTER': 'jupiter',
    'SATURN': 'saturn', 'SATURN BARYCENTER': 'saturn', 'URANUS': 'uranus', 'URANUS BARYCENTER': 'uranus',
    'NEPTUNE': 'neptune', 'NEPTUNE BARYCENTER': 'neptune', 'PLUTO': 'pluto', 'PLUTO BARYCENTER': 'pluto',
}
INERTIAL = {'EME2000': 'j2000', 'J2000': 'j2000', 'J2000A': 'j2000', 'MOE EARTH': 'j2000',
            'GCRF': 'icrs', 'ICRF': 'icrs', 'ICRS': 'icrs', 'GCRS': 'icrs',
            'J2000 ECLIPTIC': 'ecliptic', 'ECLIPJ2000': 'ecliptic',
            'TEME': 'teme', 'TOD': 'tod', 'TOD EARTH': 'tod', 'TETE': 'tod', 'MOD': 'mod', 'MOD EARTH': 'mod'}
EARTH_FIXED = {'ITRF': 'itrs', 'ITRS': 'itrs', 'FIXED EARTH': 'itrs', 'ECEF': 'itrs', 'ECF': 'itrs', 'ECR': 'itrs',
               'EFG': 'tirs', 'PEF': 'tirs', 'TIRS': 'tirs', 'TDR': 'tirs', 'GTOD': 'tirs', 'GRC': 'tirs'}


def center_key(name):
    c = CENTERS.get(text_value(name or 'EARTH'))
    if c is None:
        raise _err('中心天体 {name} には対応していません（EARTH, MOON, SUN, SOLAR SYSTEM BARYCENTER, EARTH BARYCENTER, 惑星）',
                   name=name)
    return c


def frame_key(name):
    v = text_value(name or '')
    f = INERTIAL.get(v) or EARTH_FIXED.get(v)
    if f is None and re.fullmatch(r'(ITRF|DTRF)[ -]?\d{2,4}', v):
        f = 'itrs'
    if f is None and re.fullmatch(r'(ICRF|GCRF)\d', v):
        f = 'icrs'
    if f is None:
        raise _err('座標系 {name} には対応していません（EME2000, GCRF, ICRF, TEME, TOD, MOD, ITRF, J2000_ECLIPTIC など）',
                   name=name)
    return f


def frame_rotation(ctx, frame, t):
    """Rotation matrices R (3, 3, n) from GCRS to the frame at the times t (r_frame = R r_gcrs);
    None for the GCRS axes."""
    if frame == 'icrs':
        return None
    n = len(t.tt) if np.ndim(t.tt) else 1
    if frame == 'j2000':
        return np.repeat(ICRS_to_J2000[:, :, None], n, axis=2)
    if frame == 'ecliptic':
        R = ecliptic_J2000_frame.rotation_at(t)
        return R if np.ndim(R) == 3 else np.repeat(R[:, :, None], n, axis=2)
    if frame == 'teme':
        return TEME.rotation_at(t)
    if frame == 'tod':
        return t.M
    if frame == 'mod':
        return np.einsum('ij...,jk->ik...', t.P, ICRS_to_J2000)
    if frame == 'itrs':
        return itrs.rotation_at(t)
    return tirs.rotation_at(t)


def to_gcrs(ctx, t, r, v, frame, center='earth', frame_epoch=None):
    """States (n, 3) in ``frame`` relative to ``center`` at the times t -> geocentric GCRS positions
    and velocities (km, km/s).  ``frame_epoch``: the fixed epoch of a TOD / MOD / TEME frame."""
    r = np.asarray(r, float).reshape(-1, 3)
    v = None if v is None else np.asarray(v, float).reshape(-1, 3)
    if frame in ('itrs', 'tirs') and center != 'earth':
        raise _err('地球固定の座標系は中心が EARTH のときだけ使えます')
    if frame in ('teme', 'tod', 'mod') and center != 'earth':
        raise _err('{frame} は中心が EARTH のときだけ使えます', frame=frame.upper())
    tf = t if frame_epoch is None else frame_epoch
    R = frame_rotation(ctx, frame, tf)
    if R is not None:
        if R.ndim == 2:
            R = R[:, :, None]
        if R.shape[2] == 1 and r.shape[0] > 1:
            R = np.repeat(R, r.shape[0], axis=2)
        Rt = np.transpose(R, (2, 1, 0))            # (n, 3, 3): frame -> GCRS
        if v is not None and frame in ('itrs', 'tirs'):
            v = v + EARTH_OMEGA * np.stack([-r[:, 1], r[:, 0], np.zeros(len(r))], axis=1)
        r = np.einsum('nij,nj->ni', Rt, r)
        if v is not None:
            v = np.einsum('nij,nj->ni', Rt, v)
    if center != 'earth':
        from .propagate import EPH_TARGETS
        st = (ctx.eph[EPH_TARGETS[center]] - ctx.earth).at(t)
        r = r + np.atleast_2d(st.position.km.T)
        if v is not None:
            v = v + np.atleast_2d(st.velocity.km_per_s.T)
    return r, v


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
def _num(kv, key, where, default=None):
    v = kv.get(key)
    if v is None or str(v).strip() == '':
        if default is None:
            raise _err('{where} に {key} がありません', where=where, key=key)
        return default
    try:
        x = float(str(v).strip())
    except ValueError:
        x = math.nan
    if not math.isfinite(x):
        raise _err('{where} の {key} の値「{value}」を数値として読めません', where=where, key=key, value=str(v)[:30])
    return x


def _numbers(tokens, n, where, line):
    try:
        out = [float(x) for x in tokens[:n]]
    except ValueError:
        out = [math.nan]
    if len(tokens) < n or not all(map(math.isfinite, out)):
        if line:
            raise _err('{where} の {line} 行目に数値が {n} 個必要です', where=where, line=line, n=n)
        raise _err('{where} の状態に数値が {n} 個必要です', where=where, n=n)
    return out


def _intl(s):
    """An international designator in the COSPAR form YYYY-NNNP (from the TLE form YYNNNP too)."""
    s = str(s or '').strip().upper()
    m = re.fullmatch(r'(\d{2})(\d{3})([A-Z]{1,3})', s)
    if m:
        yy = int(m.group(1))
        return f'{1900 + yy if yy >= 57 else 2000 + yy}-{m.group(2)}{m.group(3)}'
    return s


def identity(msg, seg=0):
    """{'name', 'id', 'norad'} of the object of a message (segment)."""
    if msg.kind == 'TLE':
        s = msg.segments[0]
        l1 = s['line1']
        return {'name': s['name'], 'id': _intl(l1[9:17]), 'norad': l1[2:7].strip().lstrip('0') or '0'}
    if msg.kind in ('OPM', 'OMM'):
        kv = msg.segments[0]['kv']
        return {'name': kv.get('OBJECT_NAME', ''), 'id': kv.get('OBJECT_ID', ''),
                'norad': str(kv.get('NORAD_CAT_ID', '')).strip().lstrip('0')}
    meta = msg.segments[seg]['meta']
    return {'name': meta.get('OBJECT_NAME', ''),
            'id': meta.get('OBJECT_ID', meta.get('INTERNATIONAL_DESIGNATOR', '')),
            'norad': str(meta.get('OBJECT_DESIGNATOR', '')).strip()}


def _known(v):
    return v and text_value(v) not in ('UNKNOWN', 'N/A')


def _key(ident):
    for k in ('id', 'norad', 'name'):
        if _known(ident.get(k)):
            return k, text_value(ident[k])
    return 'name', ''


def _matches(ident, query):
    q = text_value(query)
    return bool(q) and any(_known(ident.get(k)) and text_value(ident[k]) == q for k in ('name', 'id', 'norad'))


def _label(ident):
    name = ident.get('name') if _known(ident.get('name')) else ''
    extra = [x for x in (ident.get('id'), ident.get('norad') and f"NORAD {ident['norad']}")
             if _known(x) and x != name]
    return (name + (f' ({", ".join(extra)})' if extra and name else ', '.join(extra))).strip() or tr('名前のない天体')


ORBIT_PRIORITY = ('OEM', 'OCM', 'OPM', 'OMM', 'TLE')


def objects(msgs):
    """The objects of the orbit messages: [{'key', 'ident', 'label', 'entries': [(message, segment)]}]
    in the order of the input (an OEM may hold segments of several objects)."""
    out = {}
    for msg in msgs:
        if msg.kind == 'TDM':
            continue
        segs = range(len(msg.segments)) if msg.kind == 'OEM' else [0]
        for k in segs:
            ident = identity(msg, k)
            key = _key(ident)
            o = out.get(key)
            if o is None:
                # the same object written with another identifier (e.g. a TLE and an OMM)
                o = next((x for x in out.values()
                          if any(_known(ident.get(f)) and text_value(ident[f]) == text_value(x['ident'].get(f))
                                 for f in ('id', 'norad'))), None)
            if o is None:
                o = out[key] = {'key': key, 'ident': ident, 'label': _label(ident), 'entries': []}
            o['entries'].append((msg, k))
    return list(out.values())


def participants(msgs):
    names = []
    for msg in msgs:
        if msg.kind != 'TDM':
            continue
        for seg in msg.segments:
            for k, v in seg['meta'].items():
                if re.fullmatch(r'PARTICIPANT_\d', k) and v.strip() and v.strip() not in names:
                    names.append(v.strip())
    return names


# ---------------------------------------------------------------------------
# Observers
# ---------------------------------------------------------------------------
def message_info(msg):
    h = msg.header
    return {'message': msg.kind, 'version': msg.version, 'format': msg.fmt,
            'originator': h.get('ORIGINATOR', ''), 'creation_date': h.get('CREATION_DATE', ''),
            'message_id': h.get('MESSAGE_ID', '')}


def _seconds(t, j0):
    return ((np.asarray(t.whole, float) - j0[0]) + (np.asarray(t.tt_fraction, float) - j0[1])) * DAY_S


def _interp_method(kw, has_vel, where):
    """(method, nodes) of INTERPOLATION / INTERPOLATION_DEGREE ('propagate' for the OCM's PROPAGATE)."""
    m = text_value(kw.get('INTERPOLATION', ''))
    deg = kw.get('INTERPOLATION_DEGREE')
    try:
        d = int(float(deg)) if deg not in (None, '') else None
    except ValueError:
        raise _err('{where} の INTERPOLATION_DEGREE の値「{value}」を整数として読めません', where=where, value=deg)
    if not m:
        return ('hermite', 4) if has_vel else ('lagrange', 8)
    if m == 'PROPAGATE':
        return 'propagate', 0
    if m == 'HERMITE':
        return ('hermite', max(2, ((d or 7) + 1) // 2)) if has_vel else ('lagrange', max(2, (d or 7) + 1))
    if m == 'LAGRANGE':
        return 'lagrange', max(2, (d or 7) + 1)
    if m == 'LINEAR':
        return 'linear', 2
    raise _err('{where} の補間方法 {method} には対応していません（HERMITE, LAGRANGE, LINEAR）', where=where,
               method=kw.get('INTERPOLATION'))


def interpolation_error_km(seg, samples=40):
    """A rough estimate of the interpolation error of a table: each sampled node interpolated from the
    others (twice the spacing there), scaled down by the order of the method."""
    from .observers import interpolate_states
    x = seg['s']
    n = x.size
    k = 2 if seg['method'] == 'linear' else seg['npts']
    if n < k + 2:
        return None
    errs = []
    for j in np.unique(np.linspace(1, n - 2, min(samples, n - 2)).astype(int)):
        keep = np.ones(n, bool)
        keep[j] = False
        sub = dict(seg, s=x[keep], pos=seg['pos'][keep], vel=None if seg['vel'] is None else seg['vel'][keep])
        p, _ = interpolate_states(sub, x[j:j + 1])
        errs.append(float(np.linalg.norm(p[0] - seg['pos'][j])))
    order = 2 if seg['method'] == 'linear' else 2 * k if seg['method'] == 'hermite' and seg['vel'] is not None else k
    return max(errs) / 2 ** order


def _table_segment(ctx, t, pos, vel, frame, center, frame_epoch, j0, method, npts, useable, where):
    if np.any(np.diff(np.asarray(t.tt)) <= 0):
        raise _err('{where} の状態の時刻は増える順に並べてください（同じ時刻の重複もできません）', where=where)
    r, v = to_gcrs(ctx, t, pos, vel, frame, center, frame_epoch)
    s = _seconds(t, j0)
    lo, hi = float(s[0]), float(s[-1])
    if useable[0] is not None:
        lo = max(lo, float(_seconds(useable[0], j0)))
    if useable[1] is not None:
        hi = min(hi, float(_seconds(useable[1], j0)))
    if hi <= lo:
        raise _err('{where} の USEABLE_START_TIME〜USEABLE_STOP_TIME にデータがありません', where=where)
    return {'s': s, 'pos': r, 'vel': v, 'method': method, 'npts': npts, 'lo': lo, 'hi': hi}


def _span_info(ctx, segs, j0):
    from .timeutil import iso_from_jd
    jd0 = j0[0] + j0[1]
    a = min(g['lo'] for g in segs) / DAY_S + jd0
    b = max(g['hi'] for g in segs) / DAY_S + jd0
    steps = np.concatenate([np.diff(g['s']) for g in segs if g['s'].size > 1])
    return {'start': iso_from_jd(ctx, a), 'end': iso_from_jd(ctx, b), 'n_states': int(sum(g['s'].size for g in segs)),
            'n_segments': len(segs), 'step_s': float(np.median(steps)) if steps.size else None}


def _check_tables(segs, where, notes):
    n = len(segs)
    errs = [e for e in (interpolation_error_km(g, max(4, 160 // n)) for g in segs) if e is not None]
    if errs and max(errs) > 0.1:
        notes.append(tr('{where} の状態の間隔が粗いため、補間の誤差が最大 {km:.1f} km ほどになるおそれがあります',
                        where=where, km=max(errs)))
    return max(errs) if errs else None


def _build_oem(ctx, entries, name, notes):
    from .observers import EphemerisObserver
    segs, info_segs, j0, total = [], [], None, 0
    for msg, k in entries:
        seg = msg.segments[k]
        meta = seg['meta']
        rows = seg['states']
        total += len(rows)
        if total > MAX_STATES:
            raise _err('状態ベクトルが多すぎます（{n} 個まで）', n=MAX_STATES)
        if len(rows) < 2:
            raise _err('OEM の各セグメントには状態ベクトルが 2 つ以上必要です')
        system = meta.get('TIME_SYSTEM', 'UTC')
        t = epochs(ctx, [tok[0] if tok else '' for _, tok in rows], system)
        nums = np.array([_numbers(tok[1:], 6, 'OEM', line) for line, tok in rows])
        if j0 is None:
            j0 = (float(t.whole[0]), float(t.tt_fraction[0]))
        frame, center = frame_key(meta.get('REF_FRAME')), center_key(meta.get('CENTER_NAME', 'EARTH'))
        fe = (epoch(ctx, meta['REF_FRAME_EPOCH'], system)
              if meta.get('REF_FRAME_EPOCH') and frame in ('tod', 'mod', 'teme') else None)
        method, npts = _interp_method(meta, True, 'OEM')
        if method == 'propagate':
            method, npts = 'hermite', 4
        useable = tuple(epoch(ctx, meta[key], system) if meta.get(key) else None
                        for key in ('USEABLE_START_TIME', 'USEABLE_STOP_TIME'))
        segs.append(_table_segment(ctx, t, nums[:, :3], nums[:, 3:6], frame, center, fe, j0, method, npts, useable,
                                   'OEM'))
        info_segs.append({'ref_frame': meta.get('REF_FRAME', ''), 'center': meta.get('CENTER_NAME', ''),
                          'time_system': system, 'interpolation': meta.get('INTERPOLATION', ''),
                          'interpolation_degree': meta.get('INTERPOLATION_DEGREE', ''), 'n_states': len(rows)})
    msg = entries[0][0]
    info = message_info(msg)
    meta = msg.segments[entries[0][1]]['meta']
    info.update(object_name=meta.get('OBJECT_NAME', ''), object_id=meta.get('OBJECT_ID', ''),
                ref_frame=meta.get('REF_FRAME', ''), center=meta.get('CENTER_NAME', ''),
                time_system=meta.get('TIME_SYSTEM', ''), segments=info_segs, **_span_info(ctx, segs, j0))
    info['interpolation_error_km'] = _check_tables(segs, 'OEM', notes)
    return EphemerisObserver(ctx, name, segs, j0, info, notes)


def _impulse(ctx, t, frame_name, dv, where):
    """A function state -> delta-v (GCRS, km/s) of a maneuver given in an orbit-relative frame (RTN,
    RSW, TNW, ...) or an inertial frame."""
    from .propagate import local_frame
    dv = np.asarray(dv, float)
    f = text_value(frame_name or 'RTN').replace(' ', '_')
    if local_frame(np.array([7000.0, 0, 0]), np.array([0, 7.5, 0]), f) is not None:
        return lambda x: local_frame(x[:3], x[3:], f) @ dv
    key = frame_key(frame_name)
    if key in ('itrs', 'tirs'):
        raise _err('{where} の軌道制御の座標系 {frame} には対応していません', where=where, frame=frame_name)
    R = frame_rotation(ctx, key, t)
    g = dv if R is None else (R[:, :, 0] if R.ndim == 3 else R).T @ dv
    return lambda x: g


def _spacecraft(mass, area_drag, cd, area_srp, cr):
    drag = cd * area_drag / mass if mass and area_drag and cd else None
    srp = cr * area_srp / mass if mass and area_srp and cr else None
    return drag, srp


def _rotate(ctx, t, x, frame, fe):
    """A state's axes turned to ICRS, keeping its centre."""
    R = frame_rotation(ctx, frame, t if fe is None else fe)
    r, v = np.asarray(x[:3], float), np.asarray(x[3:], float)
    if R is not None:
        R = R[:, :, 0] if R.ndim == 3 else R
        r, v = R.T @ r, R.T @ v
    return r[None], v[None]


def _forces(center, drag, srp):
    from .propagate import GM_BODIES
    out = ['EGM96 8x8', 'Sun', 'Moon'] if center == 'earth' else [n.capitalize() for n in GM_BODIES]
    if drag:
        out.append('drag')
    if srp:
        out.append('SRP')
    return out


def _force_notes(center, anchors, drag, label, notes):
    from .propagate import elements as osc_elements
    if center != 'earth':
        notes.append(tr('{label} の軌道は中心天体と太陽・惑星・月の重力だけで伝搬しています（天体の形による重力の偏りは考えていません）',
                        label=label))
        return
    el = osc_elements(anchors[0][1][:3], anchors[0][1][3:])
    if not drag and el['r_min_km'] - WGS84_A < 800:
        notes.append(tr('{label} に大気抵抗の計算に必要な値（質量・面積・抗力係数）がないため、大気抵抗は計算に入れていません',
                        label=label))
    elif drag:
        notes.append(tr('大気抵抗は平均的な太陽活動の大気密度（指数関数モデル）で計算しています。'
                        '低軌道では太陽活動によって数日で数 km の差が出ることがあります'))


def _build_opm(ctx, entries, name, notes):
    from .observers import PropagatedObserver
    anchors, maneuvers = [], []
    center, drag, srp = None, None, None
    finite = tr('有限時間の軌道制御は、その中央の時刻の瞬時の速度変化として計算しました')
    for msg, _ in entries:
        kv = msg.segments[0]['kv']
        system = kv.get('TIME_SYSTEM', 'UTC')
        if 'EPOCH' not in kv:
            raise _err('{where} に {key} がありません', where='OPM', key='EPOCH')
        t = epoch(ctx, kv['EPOCH'], system)
        x = np.array([_num(kv, k, 'OPM') for k in ('X', 'Y', 'Z', 'X_DOT', 'Y_DOT', 'Z_DOT')])
        frame, c = frame_key(kv.get('REF_FRAME')), center_key(kv.get('CENTER_NAME', 'EARTH'))
        if center not in (None, c):
            raise _err('同じ天体の OPM の中心天体（CENTER_NAME）がそろっていません')
        center = c
        fe = (epoch(ctx, kv['REF_FRAME_EPOCH'], system)
              if kv.get('REF_FRAME_EPOCH') and frame in ('tod', 'mod', 'teme') else None)
        r, v = to_gcrs(ctx, t, x[:3], x[3:], frame, 'earth', fe) if c == 'earth' else _rotate(ctx, t, x, frame, fe)
        anchors.append((t, np.concatenate([r[0], v[0]])))
        d, s = _spacecraft(_num(kv, 'MASS', 'OPM', 0.0), _num(kv, 'DRAG_AREA', 'OPM', 0.0),
                           _num(kv, 'DRAG_COEFF', 'OPM', 0.0), _num(kv, 'SOLAR_RAD_AREA', 'OPM', 0.0),
                           _num(kv, 'SOLAR_RAD_COEFF', 'OPM', 0.0))
        drag, srp = drag or d, srp or s
        for man in msg.segments[0]['maneuvers']:
            if 'MAN_EPOCH_IGNITION' not in man:
                continue
            dur = _num(man, 'MAN_DURATION', 'OPM', 0.0)
            ti = epoch(ctx, man['MAN_EPOCH_IGNITION'], system)
            tm = ctx.ts.tt_jd(ti.whole, ti.tt_fraction + 0.5 * dur / DAY_S)
            dv = [_num(man, f'MAN_DV_{k}', 'OPM') for k in (1, 2, 3)]
            maneuvers.append((tm, _impulse(ctx, tm, man.get('MAN_REF_FRAME', 'RTN'), dv, 'OPM')))
            if dur > 0 and finite not in notes:
                notes.append(finite)
    msg = entries[0][0]
    kv = msg.segments[0]['kv']
    info = message_info(msg)
    info.update(object_name=kv.get('OBJECT_NAME', ''), object_id=kv.get('OBJECT_ID', ''),
                ref_frame=kv.get('REF_FRAME', ''), center=kv.get('CENTER_NAME', ''),
                time_system=kv.get('TIME_SYSTEM', ''), epoch_text=kv.get('EPOCH', ''), maneuvers=len(maneuvers),
                drag=drag, srp=srp, forces=_forces(center, drag, srp))
    _force_notes(center, anchors, drag, 'OPM', notes)
    return PropagatedObserver(ctx, anchors, center, drag, srp, maneuvers, name, info, notes, 'OPM')


_TRAJ_SIZES = {'CARTP': 3, 'CARTPV': 6, 'CARTPVA': 9, 'KEPLERIAN': 6, 'KEPLERIANMEAN': 6}


def _build_ocm(ctx, msg, name, notes):
    from .observers import EphemerisObserver, PropagatedObserver
    from .propagate import GM_BODIES, state_from_elements
    seg = msg.segments[0]
    meta = seg['meta']
    system = meta.get('TIME_SYSTEM', 'UTC')
    t0 = epoch(ctx, meta['EPOCH_TZERO'], system) if meta.get('EPOCH_TZERO') else None
    pert = seg.get('pert') or {}
    phys = seg.get('phys') or {}
    tables, anchors, centers, j0 = [], [], set(), None
    for traj in seg['traj']:
        kw = traj['kw']
        lines = [(no, tok) for no, tok in traj['lines'] if tok]
        if not lines:
            continue
        ttype = text_value(kw.get('TRAJ_TYPE', 'CARTPV')).replace(' ', '')
        n_el = _TRAJ_SIZES.get(ttype)
        if n_el is None:
            raise _err('OCM の TRAJ_TYPE {type} には対応していません（CARTP, CARTPV, CARTPVA, KEPLERIAN, KEPLERIANMEAN）',
                       type=kw.get('TRAJ_TYPE'))
        if ttype.startswith('KEPLERIAN') and text_value(kw.get('ORB_AVERAGING', 'OSCULATING')) != 'OSCULATING':
            raise _err('OCM の平均要素（ORB_AVERAGING = {avg}）には対応していません（接触要素 OSCULATING のみ）',
                       avg=kw.get('ORB_AVERAGING'))
        center = center_key(kw.get('CENTER_NAME', 'EARTH'))
        centers.add(center)
        frame = frame_key(kw.get('TRAJ_REF_FRAME', 'ICRF3'))
        fe = None
        if kw.get('TRAJ_FRAME_EPOCH') and frame in ('tod', 'mod', 'teme'):
            v = kw['TRAJ_FRAME_EPOCH']
            if is_relative(v) and t0 is None:
                raise _err('{where} の相対時刻には EPOCH_TZERO が必要です', where='OCM')
            fe = ctx.ts.tt_jd(t0.whole, t0.tt_fraction + float(v) / DAY_S) if is_relative(v) else epoch(ctx, v, system)
        if is_relative(lines[0][1][0]):
            if t0 is None:
                raise _err('{where} の相対時刻には EPOCH_TZERO が必要です', where='OCM')
            secs = np.array([_numbers(tok[:1], 1, 'OCM', no)[0] for no, tok in lines])
            t = ctx.ts.tt_jd(np.full(secs.size, t0.whole), t0.tt_fraction + secs / DAY_S)
        else:
            t = epochs(ctx, [tok[0] for _, tok in lines], system)
        nums = np.array([_numbers(tok[1:], n_el, 'OCM', no) for no, tok in lines])
        if ttype.startswith('KEPLERIAN'):
            gm = _num(pert, 'GM', 'OCM', GM_BODIES.get(center, GM_EARTH))
            nums = np.array([state_from_elements(*row[:6], gm=gm, mean=(ttype == 'KEPLERIANMEAN')) for row in nums])
        pos, vel = nums[:, :3], (nums[:, 3:6] if nums.shape[1] >= 6 else None)
        method, npts = _interp_method(kw, vel is not None, 'OCM')
        if j0 is None:
            j0 = (float(t.whole[0]), float(t.tt_fraction[0]))
        if len(lines) >= 2 and method != 'propagate':
            useable = tuple(epoch(ctx, kw[key], system) if kw.get(key) else None
                            for key in ('USEABLE_START_TIME', 'USEABLE_STOP_TIME'))
            tables.append(_table_segment(ctx, t, pos, vel, frame, center, fe, j0, method, npts, useable, 'OCM'))
            continue
        if vel is None:
            raise _err('OCM の位置だけの状態（CARTP）からは軌道を伝搬できません（速度が必要です）')
        for k in range(len(lines)):
            tk = ctx.ts.tt_jd(t.whole[k], t.tt_fraction[k])
            if center == 'earth':
                r, v = to_gcrs(ctx, tk, pos[k], vel[k], frame, 'earth', fe)
            else:
                r, v = _rotate(ctx, tk, np.concatenate([pos[k], vel[k]]), frame, fe)
            anchors.append((tk, np.concatenate([r[0], v[0]])))
    info = message_info(msg)
    info.update(object_name=meta.get('OBJECT_NAME', ''), object_id=meta.get('INTERNATIONAL_DESIGNATOR', ''),
                time_system=system, n_traj=len(seg['traj']),
                traj_types=sorted({text_value(tj['kw'].get('TRAJ_TYPE', 'CARTPV')) for tj in seg['traj']}))
    if tables:
        if anchors:
            notes.append(tr('OCM の状態が 1 つだけの軌道ブロックは使わず、状態の並びを補間しました'))
        info.update(**_span_info(ctx, tables, j0))
        info['interpolation_error_km'] = _check_tables(tables, 'OCM', notes)
        return EphemerisObserver(ctx, name, tables, j0, info, notes)
    if not anchors:
        raise _err('OCM に軌道の状態（TRAJ ブロック）がありません')
    if len(centers) > 1:
        raise _err('OCM の軌道ブロックの中心天体（CENTER_NAME）がそろっていません')
    center = centers.pop()
    mass = (_num(phys, 'WET_MASS', 'OCM', 0.0) or _num(phys, 'DRY_MASS', 'OCM', 0.0)
            or _num(phys, 'INITIAL_WET_MASS', 'OCM', 0.0))
    drag, srp = _spacecraft(mass, _num(phys, 'DRAG_CONST_AREA', 'OCM', 0.0), _num(phys, 'DRAG_COEFF_NOM', 'OCM', 0.0),
                            _num(phys, 'SRP_CONST_AREA', 'OCM', 0.0), _num(phys, 'SOLAR_RAD_COEFF', 'OCM', 0.0))
    maneuvers = _ocm_maneuvers(ctx, seg, system, t0, notes)
    _force_notes(center, anchors, drag, 'OCM', notes)
    info.update(maneuvers=len(maneuvers), drag=drag, srp=srp, forces=_forces(center, drag, srp))
    return PropagatedObserver(ctx, anchors, center, drag, srp, maneuvers, name, info, notes, 'OCM')


def _ocm_maneuvers(ctx, seg, system, t0, notes):
    out = []
    for man in seg['man']:
        kw = man['kw']
        comp = [c.strip().upper().replace(' ', '') for c in kw.get('MAN_COMPOSITION', '').split(',') if c.strip()]
        if not comp or comp[0] not in ('TIME_ABSOLUTE', 'TIME_RELATIVE'):
            continue
        if not all(k in comp for k in ('DV_X', 'DV_Y', 'DV_Z')):
            text = tr('OCM の推力・加速度・分離で表した軌道制御（THR_*・ACC_*・DEPLOY_*）は計算に入れていません')
            if text not in notes:
                notes.append(text)
            continue
        for no, tok in man['lines']:
            if len(tok) < len(comp):
                raise _err('{where} の {line} 行目に数値が {n} 個必要です', where='OCM', line=no, n=len(comp))
            if comp[0] == 'TIME_RELATIVE':
                if t0 is None:
                    raise _err('{where} の相対時刻には EPOCH_TZERO が必要です', where='OCM')
                ti = ctx.ts.tt_jd(t0.whole, t0.tt_fraction + float(tok[0]) / DAY_S)
            else:
                ti = epoch(ctx, tok[0], system)
            vals = dict(zip(comp[1:], tok[1:]))
            try:
                dura = float(vals.get('MAN_DURA', 0.0))
                dv = [float(vals[k]) for k in ('DV_X', 'DV_Y', 'DV_Z')]
            except ValueError:
                raise _err('{where} の {line} 行目に数値が {n} 個必要です', where='OCM', line=no, n=len(comp))
            tm = ctx.ts.tt_jd(ti.whole, ti.tt_fraction + 0.5 * dura / DAY_S)
            out.append((tm, _impulse(ctx, tm, kw.get('MAN_REF_FRAME', 'RTN'), dv, 'OCM')))
    return out


def _make_omm_class():
    from .observers import SGP4Observer

    class OMMObserver(SGP4Observer):
        """An Earth satellite of a CCSDS OMM (SGP4 mean elements, like a TLE)."""

        def age_warnings(self, age):
            if age > 30:
                return [tr('OMM の元期から {age:.0f} 日離れています。SGP4 の位置誤差は数百 km 以上になり得るため、'
                           '結果は目安として扱ってください。', age=age)]
            if age > 7:
                return [tr('OMM の元期から {age:.0f} 日離れています（位置誤差は数 km〜数十 km 程度）。', age=age)]
            return []

        def describe(self):
            d = {'kind': 'space', 'model': 'ccsds', 'name': self.name}
            d.update(self.info)
            d.update(self.elements())
            return d
    return OMMObserver


OMMObserver = _make_omm_class()


def omm_observer(ctx, msg, name, m_deg=None):
    """The SGP4 observer of an OMM (MEAN_ELEMENT_THEORY SGP4; ``m_deg`` replaces the mean anomaly)."""
    from sgp4.api import WGS72, Satrec
    kv = msg.segments[0]['kv']
    where = 'OMM'
    theory = set(re.split(r'[/,\s]+', text_value(kv.get('MEAN_ELEMENT_THEORY', 'SGP4'))))     # e.g. SGP/SGP4
    if not theory <= {'SGP4', 'SGP', 'SDP4', ''}:
        raise _err('OMM の MEAN_ELEMENT_THEORY = {theory} には対応していません（SGP4 の平均要素のみ。SGP4-XP・DSST などは使えません）',
                   theory=kv.get('MEAN_ELEMENT_THEORY'))
    for key, want in (('CENTER_NAME', 'EARTH'), ('REF_FRAME', 'TEME'), ('TIME_SYSTEM', 'UTC')):
        if kv.get(key) and text_value(kv[key]) != want:
            raise _err('SGP4 の OMM の {key} は {want} にしてください（{value}）', key=key, want=want, value=kv[key])
    if 'EPOCH' not in kv:
        raise _err('{where} に {key} がありません', where=where, key='EPOCH')
    y, mo, d, h, mi, sec = _calendar([kv['EPOCH']])
    epoch_days = float(_jdn(y, mo, d)[0] - 0.5 + (h[0] * 3600 + mi[0] * 60 + sec[0]) / DAY_S - 2433281.5)
    if str(kv.get('MEAN_MOTION', '')).strip():
        n_rev = _num(kv, 'MEAN_MOTION', where)
    else:
        a = _num(kv, 'SEMI_MAJOR_AXIS', where)
        n_rev = math.sqrt(398600.8 / a ** 3) * DAY_S / (2 * math.pi)       # the GM of SGP4 (WGS72)
    e = _num(kv, 'ECCENTRICITY', where)
    if not (0 <= e < 1) or not n_rev > 0:
        raise _err('OMM の軌道要素の値が範囲外です（離心率 0 以上 1 未満、平均運動は正）')
    m = _num(kv, 'MEAN_ANOMALY', where) if m_deg is None else float(m_deg) % 360.0
    norad = str(kv.get('NORAD_CAT_ID', '')).strip()
    try:
        satnum = int(float(norad)) if norad else 0
    except ValueError:
        satnum = 0
    sat = Satrec()
    sat.sgp4init(WGS72, 'i', satnum if 0 <= satnum <= 339999 else 0, epoch_days, _num(kv, 'BSTAR', where, 0.0),
                 _num(kv, 'MEAN_MOTION_DOT', where, 0.0) / (1440.0 ** 2 / (2 * math.pi)),
                 _num(kv, 'MEAN_MOTION_DDOT', where, 0.0) / (1440.0 ** 3 / (2 * math.pi)), e,
                 math.radians(_num(kv, 'ARG_OF_PERICENTER', where)), math.radians(_num(kv, 'INCLINATION', where)),
                 math.radians(m), n_rev * 2 * math.pi / 1440.0, math.radians(_num(kv, 'RA_OF_ASC_NODE', where)))
    obs = OMMObserver.__new__(OMMObserver)
    obs.name = name or kv.get('OBJECT_NAME') or (f'NORAD {satnum}' if satnum else 'OMM')
    obs._init_sgp4(ctx, sat, obs.name, tr('この OMM の軌道の近地点は地球内部にあります（再突入済みの可能性）'))
    info = message_info(msg)
    info.update(object_name=kv.get('OBJECT_NAME', ''), object_id=kv.get('OBJECT_ID', ''),
                mean_element_theory=kv.get('MEAN_ELEMENT_THEORY', 'SGP4'), epoch_text=kv['EPOCH'],
                norad=satnum if satnum else (norad or None))
    obs.info = info
    obs.notes = []
    return obs


def orbit_observer(ctx, obj, name, m_deg=None):
    """The observer of an object of the orbit messages: its OEM, else OCM, OPM, OMM or TLE."""
    from .observers import TLEObserver, tle_with_mean_anomaly
    entries = obj['entries']
    kinds = [m.kind for m, _ in entries]
    best = next(k for k in ORBIT_PRIORITY if k in kinds)
    chosen = [(m, s) for m, s in entries if m.kind == best]
    notes = []
    others = [k for k in ORBIT_PRIORITY if k in kinds and k != best]
    if others:
        notes.append(tr('同じ天体の {kinds} は使わず、{used} で計算しました', kinds=', '.join(others), used=best))
    ident = obj['ident']
    name = name or (ident['name'].strip() if _known(ident.get('name')) else obj['label'])
    if m_deg is not None and best not in ('OMM', 'TLE'):
        raise _err('平均近点角を変えた一括計算は OMM（平均軌道要素）と TLE でだけ使えます')
    if best == 'OEM':
        return _build_oem(ctx, chosen, name, notes)
    if best == 'OCM':
        if len(chosen) > 1:
            notes.append(tr('同じ天体の OCM が {n} 件あるため、最初の 1 件で計算しました', n=len(chosen)))
        return _build_ocm(ctx, chosen[0][0], name, notes)
    if best == 'OPM':
        return _build_opm(ctx, chosen, name, notes)
    if best == 'OMM':
        obs = [omm_observer(ctx, m, name, m_deg) for m, _ in chosen]
    else:
        obs = []
        for m, _ in chosen:
            s = m.segments[0]
            l2 = s['line2'] if m_deg is None else tle_with_mean_anomaly(s['line2'], m_deg)
            obs.append(TLEObserver(ctx, s['line1'], l2, name or s['name']))
    pick = max(obs, key=lambda o: o.epoch_jd)
    if len(obs) > 1:
        notes.append(tr('同じ天体の {kind} が {n} 件あるため、元期が最も新しいものを使いました', kind=best, n=len(obs)))
    pick.notes = list(getattr(pick, 'notes', None) or []) + notes
    return pick


def build_observer(ctx, spec):
    """The observer of a request {'type': 'ccsds', 'text', 'object', 'stations', 'name', 'm_deg'}."""
    from .od import parse_stations, tdm_observer
    msgs = parse(spec.get('text'))
    name = (spec.get('name') or '').strip()
    query = (spec.get('object') or '').strip()
    stations = parse_stations(spec.get('stations'))
    tdms = [m for m in msgs if m.kind == 'TDM']
    objs = objects(msgs)
    if tdms:
        if spec.get('m_deg') is not None:
            raise _err('平均近点角を変えた一括計算は OMM（平均軌道要素）と TLE でだけ使えます')
        return tdm_observer(ctx, msgs, tdms, objs, stations, query, name)
    sel = [o for o in objs if _matches(o['ident'], query)] if query else objs
    if query and not sel:
        raise _err('「{object}」は入力にありません（入力にある天体: {list}）', object=query,
                   list=', '.join(o['label'] for o in objs))
    if len(sel) > 1:
        raise _err('入力に {n} 個の天体があります。対象の天体を選んでください（{list}）', n=len(sel),
                   list=', '.join(o['label'] for o in sel[:12]) + (' …' if len(sel) > 12 else ''))
    return orbit_observer(ctx, sel[0], name, spec.get('m_deg'))


def mean_elements_only(spec):
    """Whether a CCSDS request describes its satellite by mean elements (OMM or TLE, for the phase
    sweep)."""
    try:
        msgs = parse(spec.get('text'))
    except ObserverError:
        return False
    kinds = {m.kind for m in msgs}
    return bool(kinds & {'OMM', 'TLE'}) and not kinds & {'OEM', 'OCM', 'OPM', 'TDM'}


# ---------------------------------------------------------------------------
# Summary for the page and the dry run
# ---------------------------------------------------------------------------
def _span_text(strings):
    good = [s for s in strings if s]
    return [good[0], good[-1]] if good else None


def summary(msg):
    """What a message holds, without converting it."""
    d = message_info(msg)
    if msg.kind == 'TLE':
        s = msg.segments[0]
        d.update(object=_label(identity(msg)), line1=s['line1'], line2=s['line2'])
        return d
    if msg.kind in ('OPM', 'OMM'):
        kv = msg.segments[0]['kv']
        d.update(object=_label(identity(msg)), epoch=kv.get('EPOCH', ''), ref_frame=kv.get('REF_FRAME', ''),
                 center=kv.get('CENTER_NAME', ''), time_system=kv.get('TIME_SYSTEM', ''))
        if msg.kind == 'OPM':
            d['maneuvers'] = len(msg.segments[0]['maneuvers'])
        else:
            d['mean_element_theory'] = kv.get('MEAN_ELEMENT_THEORY', '')
        return d
    if msg.kind == 'OEM':
        d['segments'] = [{'object': _label(identity(msg, k)), 'ref_frame': g['meta'].get('REF_FRAME', ''),
                          'center': g['meta'].get('CENTER_NAME', ''), 'time_system': g['meta'].get('TIME_SYSTEM', ''),
                          'n_states': len(g['states']), 'span': _span_text([tok[0] for _, tok in g['states'] if tok])}
                         for k, g in enumerate(msg.segments)]
        d['object'] = ', '.join(dict.fromkeys(s['object'] for s in d['segments']))
        return d
    if msg.kind == 'OCM':
        g = msg.segments[0]
        d.update(object=_label(identity(msg)), time_system=g['meta'].get('TIME_SYSTEM', ''),
                 epoch_tzero=g['meta'].get('EPOCH_TZERO', ''), maneuvers=len(g['man']),
                 traj=[{'traj_type': t['kw'].get('TRAJ_TYPE', 'CARTPV'), 'ref_frame': t['kw'].get('TRAJ_REF_FRAME', ''),
                        'center': t['kw'].get('CENTER_NAME', ''), 'n_states': len(t['lines']),
                        'span': _span_text([tok[0] for _, tok in t['lines'] if tok])} for t in g['traj']])
        return d
    types = {}
    for g in msg.segments:
        for _, key, _, _ in g['records']:
            types[key] = types.get(key, 0) + 1
    d.update(participants=participants([msg]), data_types=types,
             segments=[{'participants': [v for k, v in sorted(g['meta'].items()) if k.startswith('PARTICIPANT_')],
                        'path': g['meta'].get('PATH', g['meta'].get('PATH_1', '')),
                        'angle_type': g['meta'].get('ANGLE_TYPE', ''), 'range_units': g['meta'].get('RANGE_UNITS', ''),
                        'time_system': g['meta'].get('TIME_SYSTEM', ''), 'n_records': len(g['records']),
                        'span': _span_text(sorted(r[2] for r in g['records']))} for g in msg.segments])
    return d


def inspect(ctx, text, query='', stations=None):
    """What the input holds and how it would be used: the messages, the objects to choose from, the
    TDM participants whose positions are needed, and the observer when it can be made without an
    orbit determination."""
    from .od import parse_stations
    msgs = parse(text)
    st = parse_stations(stations)
    objs = objects(msgs)
    parts = participants(msgs)
    out = {'messages': [summary(m) for m in msgs],
           'objects': [{'label': o['label'], 'name': o['ident'].get('name', ''), 'id': o['ident'].get('id', ''),
                        'norad': o['ident'].get('norad', ''), 'kinds': sorted({m.kind for m, _ in o['entries']})}
                       for o in objs],
           'participants': parts, 'missing_stations': [p for p in parts if text_value(p) not in st],
           'tdm': any(m.kind == 'TDM' for m in msgs)}
    if not out['tdm']:
        try:
            obs = build_observer(ctx, {'text': text, 'object': query})
            d = obs.describe()
            if d.get('epoch_jd_tt'):
                from .timeutil import iso_from_jd
                d['epoch'] = iso_from_jd(ctx, d['epoch_jd_tt'])
            out['observer'] = d
            out['notes'] = list(getattr(obs, 'notes', None) or [])
        except ObserverError as exc:
            out['error'] = str(exc)
    return out

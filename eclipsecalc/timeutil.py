"""Time conversion helpers."""
import datetime as _dt
import re

import numpy as np

from .i18n import tr

# 1972-01-01T00:00:00 UTC as a TT Julian date.  Before it there is no UTC with leap seconds (Skyfield
# would use TT - 42.184 s, up to a minute off); those times are UT1 (UT = TT - Delta T), as in the
# eclipse catalogues.
_UTC_FROM_TT = 2441317.5 + 42.184 / 86400.0


def parse_utc(ctx, text):
    """Parse 'YYYY-MM-DD' or an ISO date-time (UTC; UT before 1972) into a TT Julian date."""
    text = text.strip().replace(' ', 'T')
    if text.endswith('Z'):
        text = text[:-1]
    if 'T' in text:
        d, t = text.split('T', 1)
        hh, mm, *rest = (t.split(':') + ['0', '0'])[:3]
        sec = float(rest[0]) if rest else 0.0
    else:
        d, hh, mm, sec = text, 0, 0, 0.0
    y, mo, da = (int(v) for v in d.split('-')) if not d.startswith('-') else _neg_date(d)
    return _tt(ctx, y, mo, da, int(hh), int(mm), sec)


def _tt(ctx, y, mo, da, hh, mm, sec):
    t = ctx.ts.utc(y, mo, da, hh, mm, sec)
    if t.tt < _UTC_FROM_TT:
        t = ctx.ts.ut1(y, mo, da, hh, mm, sec)
    return float(t.tt)


def _neg_date(d):
    parts = d[1:].split('-')
    return -int(parts[0]), int(parts[1]), int(parts[2])


_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
           'october', 'november', 'december')
_MON = r'(?:\d{1,2}|[a-z]{3,9}\.?)'
_OBS_TIME = re.compile(
    r'^(?:(?P<y>\d{4})[-/.](?P<mo>' + _MON + r')[-/.](?P<d>\d{1,2})'         # 2017-08-21, 2017/08/21, 2017-Aug-21
    r'|(?P<d2>\d{1,2})[- ](?P<mo2>[a-z]{3,9}\.?)[- ](?P<y2>\d{4}))'           # 21-Aug-2017, 21-AUG-2017
    r'(?:(?P<sep>t|\s+|_)(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{1,2}(?:\.\d*)?))?)?'
    r'(?:\s*(?P<utc>z|utc?|[+-]00:?00)|(?:_|\s+)(?P<tai>tai))?$', re.I)


def parse_obs_time(ctx, text):
    """The UTC time of an observation (e.g. the exposure time of an image) -> TT Julian date.

    Accepts (case-insensitive, always UTC; UT before 1972 as parse_utc) ISO 8601
    '2017-08-21T16:53:59.204Z' (also with a space for the T and without the Z),
    '2017/08/21 16:53:59.204' and the SolarSoft / quick-look style '21-Aug-2017 16:53:59.204 UT'.
    A time in a leap second (23:59:60.5) is accepted where there is one.  The JSOC style of SDO
    '2017.08.21_16:53:59.204_TAI' is TAI (37 s ahead of UTC in 2017): it is read as such with its
    suffix and refused without it.  ValueError (with a message naming the input) if the text is
    not such a time."""
    s = str(text).strip()
    m = _OBS_TIME.match(s)
    try:
        if not m:
            raise ValueError
        g = m.groupdict()
        if not g['tai'] and not g['utc'] and (g['sep'] == '_' or re.match(r'\d{4}\.', s)):
            raise ValueError                             # JSOC-style time without its time scale
        y, mo, d = (g['y'], g['mo'], g['d']) if g['y'] else (g['y2'], g['mo2'], g['d2'])
        if mo.isdigit():
            mo = int(mo)
        else:                                            # Aug, AUG, Aug., August, Sept
            name = mo.lower().rstrip('.')
            mo = next(k + 1 for k, full in enumerate(_MONTHS) if full.startswith(name) and len(name) >= 3)
        y, d = int(y), int(d)
        _dt.date(y, mo, d)                               # a day that exists
        hh, mi = int(g['h'] or 0), int(g['mi'] or 0)
        sec = float(g['s'] or 0.0)
        if hh > 23 or mi > 59 or not (sec < 60 or (sec < 61 and not g['tai'] and _leap_minute(ctx.ts, y, mo, d, hh, mi))):
            raise ValueError
    except (ValueError, StopIteration):
        raise ValueError(tr('時刻「{text}」を解釈できません（UTC。例 2017-08-21T16:53:59.204Z、'
                            '2017/08/21 16:53:59.204、21-Aug-2017 16:53:59.204）', text=s)) from None
    if g['tai']:
        return float(ctx.ts.tai(y, mo, d, hh, mi, sec).tt)
    return _tt(ctx, y, mo, d, hh, mi, sec)


def _leap_minute(ts, y, mo, d, h, mi):
    """Whether the UTC minute ends with a leap second (has a second 60)."""
    return float(ts.utc(int(y), int(mo), int(d), int(h), int(mi), 60.0).utc[5]) >= 60.0


def iso_utc(t):
    """Skyfield Time (scalar or array) -> ISO-8601 UTC string(s) with ms (UT1 before 1972).

    A time in a leap second is written as such (2016-12-31T23:59:60.500Z)."""
    scalar = np.ndim(t.tt) == 0
    cal = np.where(np.asarray(t.tt) < _UTC_FROM_TT, t.ut1_calendar(), t.utc)
    year, month, day, hour, minute, second = (np.atleast_1d(v) for v in cal)
    out = []
    for y, mo, d, h, mi, s in zip(year, month, day, hour, minute, second):
        ms = int(round(float(s) * 1000.0))
        base = _dt.datetime(int(y), int(mo), int(d), int(h), int(mi))
        # The second is 60.x during a leap second, which datetime cannot hold; a minute with a leap
        # second has 61 s (59.9996 rounds to 60.000 in it, 60.9996 to the next minute).
        if ms >= 60_000 and (s >= 60.0 or _leap_minute(t.ts, y, mo, d, h, mi)):
            if ms < 61_000:
                out.append(base.strftime('%Y-%m-%dT%H:%M:') + f'60.{ms - 60_000:03d}Z')
                continue
            ms -= 1000
        dt = base + _dt.timedelta(milliseconds=ms)
        out.append(dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z')
    return out[0] if scalar else out


def iso_from_jd(ctx, jd):
    return iso_utc(ctx.ts.tt_jd(jd))


def delta_t_seconds(ctx, jd):
    return float(ctx.ts.tt_jd(jd).delta_t)

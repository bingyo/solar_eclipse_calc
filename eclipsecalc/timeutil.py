"""Time conversion helpers."""
import datetime as _dt

import numpy as np

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
    t = ctx.ts.utc(y, mo, da, int(hh), int(mm), sec)
    if t.tt < _UTC_FROM_TT:
        t = ctx.ts.ut1(y, mo, da, int(hh), int(mm), sec)
    return float(t.tt)


def _neg_date(d):
    parts = d[1:].split('-')
    return -int(parts[0]), int(parts[1]), int(parts[2])


def iso_utc(t):
    """Skyfield Time (scalar or array) -> ISO-8601 UTC string(s) with ms (UT1 before 1972)."""
    scalar = np.ndim(t.tt) == 0
    cal = np.where(np.asarray(t.tt) < _UTC_FROM_TT, t.ut1_calendar(), t.utc)
    year, month, day, hour, minute, second = (np.atleast_1d(v) for v in cal)
    out = []
    for y, mo, d, h, mi, s in zip(year, month, day, hour, minute, second):
        ms = int(round(float(s) * 1000.0))
        base = _dt.datetime(int(y), int(mo), int(d), int(h), int(mi))
        # second may be 60.x during a leap second; datetime cannot hold it.
        dt = base + _dt.timedelta(milliseconds=ms)
        out.append(dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z')
    return out[0] if scalar else out


def iso_from_jd(ctx, jd):
    return iso_utc(ctx.ts.tt_jd(jd))


def delta_t_seconds(ctx, jd):
    return float(ctx.ts.tt_jd(jd).delta_t)

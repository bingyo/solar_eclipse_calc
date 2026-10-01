"""Geocentric conjunctions (new moons / inferior conjunctions) used as
pre-filters for the event searches."""
import math

import numpy as np

from .constants import AU_KM
from .geometry import angle_between, norm

EPS_J2000 = math.radians(23.4392911)
_COS_E, _SIN_E = math.cos(EPS_J2000), math.sin(EPS_J2000)


def _relative_longitude(ctx, body, jd):
    """Geometric geocentric ecliptic longitude of body minus that of the Sun."""
    t = ctx.ts.tt_jd(jd)
    e = ctx.earth.at(t).position.au
    s = ctx.sun.at(t).position.au - e
    b = ctx.bodies[body].at(t).position.au - e
    ls = np.arctan2(s[1] * _COS_E + s[2] * _SIN_E, s[0])
    lb = np.arctan2(b[1] * _COS_E + b[2] * _SIN_E, b[0])
    d = (lb - ls + np.pi) % (2 * np.pi) - np.pi
    return d, norm(b), norm(s)


def find_conjunctions(ctx, body, jd_a, jd_b):
    """TT Julian dates of geocentric conjunctions in ecliptic longitude.

    For Mercury/Venus only inferior conjunctions are returned.
    """
    step = 0.25 if body == 'moon' else 0.5
    jd = np.arange(jd_a - step, jd_b + 2 * step, step)
    jd = jd[(jd > ctx.jd_min) & (jd < ctx.jd_max)]
    if jd.size < 2:
        return np.zeros(0)
    d, rb, rs = _relative_longitude(ctx, body, jd)
    sc = (np.sign(d[:-1]) != np.sign(d[1:])) & (np.abs(d[:-1]) < 1.0) & (np.abs(d[1:]) < 1.0)
    if body != 'moon':
        sc &= rb[:-1] < rs[:-1]
    idx = np.nonzero(sc)[0]
    a, b = jd[idx], jd[idx + 1]
    da = d[idx]
    for _ in range(28):
        m = 0.5 * (a + b)
        dm, _, _ = _relative_longitude(ctx, body, m)
        same = np.sign(dm) == np.sign(da)
        a = np.where(same, m, a)
        da = np.where(same, dm, da)
        b = np.where(same, b, m)
    return 0.5 * (a + b)


def geocentric_min_separation(ctx, body, tc, half_span_days, n=25):
    """Minimum geometric geocentric Sun-body separation (rad) near each tc
    and the relative angular rate (rad/day) at that time."""
    if len(tc) == 0:
        return np.zeros(0), np.zeros(0)
    off = np.linspace(-half_span_days, half_span_days, n)
    jd = (tc[:, None] + off[None, :]).ravel()
    t = ctx.ts.tt_jd(jd)
    e = ctx.earth.at(t).position.km
    s = ctx.sun.at(t).position.km - e
    b = ctx.bodies[body].at(t).position.km - e
    sep = angle_between(s, b).reshape(len(tc), n)
    k = np.argmin(sep, axis=1)
    smin = sep[np.arange(len(tc)), k]
    # relative rate from the full sampled curve (robust, >= true minimum rate)
    rate = np.abs(np.diff(sep, axis=1)).max(axis=1) / (off[1] - off[0])
    return smin, rate


BODY_MIN_DISTANCE_KM = {
    'moon': 350_000.0,
    'mercury': 0.53 * AU_KM,
    'venus': 0.25 * AU_KM,
}

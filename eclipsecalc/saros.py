"""Saros series number of a solar eclipse from its lunation number.

Eclipses one inex (358 lunations) apart belong to consecutive saros
series, and members of one series are 223 lunations apart.  Hence the
series number is a linear function of the lunation number modulo 223:
    s = s_ref + 38 * (k - k_ref)  (mod 223)    since 38 * 358 = 1 (mod 223).
The representative is chosen in a 223-wide window centred on the series
active in the given epoch (series numbers drift by ~1 per 29 years).
"""
MEEUS_K0_JD = 2451550.09766      # mean new moon of 2000 Jan 6 (Meeus k = 0)
SYNODIC_MONTH = 29.530588861
K_REF, S_REF = 218, 145          # 2017 Aug 21 total eclipse = Saros 145


def lunation_number(jd_tt):
    return round((jd_tt - MEEUS_K0_JD) / SYNODIC_MONTH)


def saros_number(jd_tt):
    k = lunation_number(jd_tt)
    year = 2000.0 + (jd_tt - 2451545.0) / 365.25
    center = 136.0 + (year - 2000.0) / 29.0
    lo = int(round(center)) - 111
    s = S_REF + 38 * (k - K_REF)
    return int(lo + (s - lo) % 223)

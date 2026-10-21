"""Physical constants and default model parameters.

All lengths are in kilometres unless stated otherwise.
"""
import math

AU_KM = 149_597_870.700
C_KM_S = 299_792.458
DAY_S = 86_400.0
RAD2ARCSEC = math.degrees(1.0) * 3600.0

# WGS84 reference ellipsoid
WGS84_A = 6378.137
WGS84_F = 1.0 / 298.257223563
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)

GM_EARTH = 398_600.4418          # km^3 / s^2
J2_EARTH = 1.08262668e-3
EARTH_OMEGA = 7.2921150e-5       # rad / s

# --- Solar radius -----------------------------------------------------------
# IAU 2015 Resolution B3 nominal solar radius (the photosphere at tau = 1, 959.23" at 1 AU).
SUN_RADIUS_IAU2015 = 695_700.0
# Classical value used by NASA/Espenak eclipse predictions: 959.63" at 1 AU.
SUN_RADIUS_NASA = 959.63 / RAD2ARCSEC * AU_KM
# The visible limb (inflection point of the limb darkening, ~0.33 Mm outside tau = 1:
# Haberreiter, Schmutz & Kosovichev 2008): 959.96" at 1 AU (696,231 km), the best fit to the
# contacts of the 2006 Mercury transit in the Hinode SOT/FG G-band movies (O-C within 1.3 s),
# as measured elsewhere (SDO/HMI 2012 Venus transit 959.90", eclipse light curves 959.99"
# (Lamy et al. 2015), SOHO/MDI 960.12", PICARD/SODISM 959.86").  The default, since contacts
# are observed at the visible limb.
SUN_RADIUS_OBSERVED = 959.96 / RAD2ARCSEC * AU_KM
# North pole of the Sun's rotation axis (IAU WGCCRE 2015, ICRS), for the P angle and the
# solar-north-up (helioprojective) coordinates of the images of solar telescopes.
SUN_POLE_RA_DEG = 286.13
SUN_POLE_DEC_DEG = 63.87

# --- Lunar radius -----------------------------------------------------------
# NASA (Espenak & Meeus) uses two values of k (lunar radius in Earth
# equatorial radii): a larger one for penumbral (external) contacts that
# reaches the mean height of limb mountains, and a smaller one for umbral
# (internal) contacts that approximates the mean depth of limb valleys.
MOON_K_EXTERNAL = 0.2725076
MOON_K_INTERNAL = 0.272281
MOON_RADIUS_MEAN = 1737.4        # IAU mean radius
GM_MOON = 4902.800               # km^3 / s^2 (DE440)
# The Moon's sphere of influence about the Earth (a (m / M)^(2/5)): closer, the motion of a
# spacecraft is governed by the Moon (a lunar orbiter).
MOON_SOI_KM = 66_000.0

# --- Planetary radii (IAU WGCCRE 2015) --------------------------------------
MERCURY_RADIUS = 2439.4
VENUS_RADIUS = 6051.8
# Semi-diameters at 1 AU used in NASA/Espenak transit predictions
# (Mercury 3.36", Venus 8.41" - the latter includes the cloud deck).
MERCURY_RADIUS_NASA = 3.36 / RAD2ARCSEC * AU_KM
VENUS_RADIUS_NASA = 8.41 / RAD2ARCSEC * AU_KM
# Venus in front of the Sun is larger than its solid surface: cloud tops and haze in visible
# light, the upper atmosphere in soft X-rays (Hinode XRT) (Reale et al. 2015, Nat. Commun. 6,
# 7563).  The visible radius is the default (contacts ~7 s from those of the solid surface).
# Mercury has no atmosphere: its radius is the same at every wavelength (confirmed to 0.1 % by
# the 2006 SOT movies).
VENUS_RADIUS_VISIBLE = 6131.0
VENUS_RADIUS_XRAY = 6202.0

BODY_LABELS_JA = {
    'moon': '月',
    'mercury': '水星',
    'venus': '金星',
}

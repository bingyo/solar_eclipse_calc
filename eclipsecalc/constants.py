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
# IAU 2015 Resolution B3 nominal solar radius.
SUN_RADIUS_IAU2015 = 695_700.0
# Classical value used by NASA/Espenak eclipse predictions: 959.63" at 1 AU.
SUN_RADIUS_NASA = 959.63 / RAD2ARCSEC * AU_KM

# --- Lunar radius -----------------------------------------------------------
# NASA (Espenak & Meeus) uses two values of k (lunar radius in Earth
# equatorial radii): a larger one for penumbral (external) contacts that
# reaches the mean height of limb mountains, and a smaller one for umbral
# (internal) contacts that approximates the mean depth of limb valleys.
MOON_K_EXTERNAL = 0.2725076
MOON_K_INTERNAL = 0.272281
MOON_RADIUS_MEAN = 1737.4        # IAU mean radius

# --- Planetary radii (IAU WGCCRE 2015) --------------------------------------
MERCURY_RADIUS = 2439.4
VENUS_RADIUS = 6051.8
# Semi-diameters at 1 AU used in NASA/Espenak transit predictions
# (Mercury 3.36", Venus 8.41" - the latter includes the cloud deck).
MERCURY_RADIUS_NASA = 3.36 / RAD2ARCSEC * AU_KM
VENUS_RADIUS_NASA = 8.41 / RAD2ARCSEC * AU_KM

BODY_LABELS_JA = {
    'moon': '月',
    'mercury': '水星',
    'venus': '金星',
}

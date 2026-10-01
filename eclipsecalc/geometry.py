"""Small vectorised geometry helpers (disk overlap, ellipsoid, frames)."""
import numpy as np

from .constants import WGS84_A, WGS84_B, WGS84_E2


def norm(v, axis=0):
    return np.sqrt(np.sum(v * v, axis=axis))


def unit(v, axis=0):
    return v / norm(v, axis=axis)


def angle_between(a, b):
    """Angle between vectors stored along axis 0 (robust for small angles)."""
    c = np.cross(a, b, axis=0)
    return np.arctan2(norm(c), np.sum(a * b, axis=0))


def circle_overlap_area(r1, r2, d):
    """Area of intersection of two circles with radii r1, r2 and centre distance d."""
    r1, r2, d = np.broadcast_arrays(np.asarray(r1, float), np.asarray(r2, float),
                                    np.asarray(d, float))
    area = np.zeros(r1.shape)
    rmin = np.minimum(r1, r2)
    inside = d <= np.abs(r1 - r2)
    area[inside] = np.pi * rmin[inside] ** 2
    part = (~inside) & (d < r1 + r2)
    if np.any(part):
        a, b, c = r1[part], r2[part], d[part]
        ca = np.clip((c * c + a * a - b * b) / (2 * c * a), -1, 1)
        cb = np.clip((c * c + b * b - a * a) / (2 * c * b), -1, 1)
        k = (-c + a + b) * (c + a - b) * (c - a + b) * (c + a + b)
        area[part] = (a * a * np.arccos(ca) + b * b * np.arccos(cb)
                      - 0.5 * np.sqrt(np.maximum(k, 0.0)))
    return area


def obscuration(rho_sun, rho_body, sep):
    """Fraction of the solar disk area covered."""
    return circle_overlap_area(rho_sun, rho_body, sep) / (np.pi * np.asarray(rho_sun) ** 2)


def magnitude(rho_sun, rho_body, sep):
    """Eclipse magnitude: fraction of the solar diameter covered."""
    return np.maximum((rho_sun + rho_body - sep) / (2.0 * rho_sun), 0.0)


def itrs_to_geodetic(xyz):
    """ITRS (km, axis 0) -> geodetic latitude, longitude (deg), height (km)."""
    x, y, z = xyz
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - WGS84_E2))
    for _ in range(6):
        s = np.sin(lat)
        n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * s * s)
        h = p / np.maximum(np.cos(lat), 1e-12) - n
        lat = np.arctan2(z, p * (1.0 - WGS84_E2 * n / (n + h)))
    s = np.sin(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * s * s)
    h = p / np.maximum(np.cos(lat), 1e-12) - n
    return np.degrees(lat), np.degrees(lon), h


def geodetic_to_itrs(lat_deg, lon_deg, h_km=0.0):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    s = np.sin(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * s * s)
    x = (n + h_km) * np.cos(lat) * np.cos(lon)
    y = (n + h_km) * np.cos(lat) * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h_km) * s
    return np.array([x, y, z])


def geodetic_normal(lat_deg, lon_deg):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    return np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


ELLIPSOID_SCALE = np.array([1.0, 1.0, WGS84_A / WGS84_B])


def line_ellipsoid_intersection(origin, direction, height_km=0.0):
    """Nearest intersection (smallest parameter) of lines with the ellipsoid.

    ``origin`` and ``direction`` have shape (3, N).  Returns (point, param, hit)
    where ``hit`` is a boolean mask.  Uses the affine map that turns the
    ellipsoid into a sphere, which preserves straight lines exactly.
    """
    a = WGS84_A + height_km
    scale = np.array([1.0, 1.0, a / (WGS84_B + height_km)])[:, None]
    o = origin * scale
    d = direction * scale
    A = np.sum(d * d, axis=0)
    B = 2.0 * np.sum(o * d, axis=0)
    C = np.sum(o * o, axis=0) - a * a
    disc = B * B - 4 * A * C
    hit = disc >= 0
    sq = np.sqrt(np.where(hit, disc, 0.0))
    t = (-B - sq) / (2 * A)
    pt = origin + direction * t
    return pt, t, hit


def wrap180(deg):
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0

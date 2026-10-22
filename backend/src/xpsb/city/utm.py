"""UTM <-> longitude/latitude on GRS80/WGS84 (Krüger series, error well below 1 mm
within a zone). Official German 3D data comes in ETRS89 / UTM (EPSG:25832, 25833);
ETRS89 and WGS84 differ by well under a metre, below what X-Plane resolves here.
"""

from __future__ import annotations

import math

_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_E0 = 500_000.0

_N = _F / (2 - _F)
_A_HAT = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440,
    61 * _N**3 / 240 - 103 * _N**4 / 140,
    49561 * _N**4 / 161280,
)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)


def zone_of(lon: float) -> int:
    return int((lon + 180) // 6) + 1


def _lon0(zone: int) -> float:
    return math.radians(zone * 6 - 183)


def to_lonlat(e: float, n: float, zone: int) -> tuple[float, float]:
    """Easting/northing (northern hemisphere) -> (lon, lat) in degrees."""
    xi = n / (_K0 * _A_HAT)
    eta = (e - _E0) / (_K0 * _A_HAT)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, start=1))
    lon = _lon0(zone) + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lon), math.degrees(lat)


def from_lonlat(lon: float, lat: float, zone: int) -> tuple[float, float]:
    """(lon, lat) in degrees -> easting/northing in the given zone."""
    phi = math.radians(lat)
    lam = math.radians(lon) - _lon0(zone)
    e = math.sqrt(_F * (2 - _F))
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_p, eta_p
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    return _E0 + _K0 * _A_HAT * eta, _K0 * _A_HAT * xi

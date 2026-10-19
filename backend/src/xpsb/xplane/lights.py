"""The X-Plane 12 lights our objects use, as defined in X-Plane's lights.txt
(Resources/bitmaps/world/lites/lights.txt; the copy shipped with Laminar's
XPlane2Blender exporter was used as reference).

Directions are in OBJ axes (+X east, +Y up, +Z south); intensities in candela.
Spill lights ("_sp") really light up the surfaces around them, billboards ("_bb")
are the visible glow of the lamp itself.
"""

from __future__ import annotations

import math

from xpsb.build.mesh import GeometryError, Light

# LIGHT_PARAM_DEF spot_params_sp_pm 9  R G B A INTENSITY DX DY DZ WIDTH
#   WIDTH = cosine of half the beam angle
# LIGHT_PARAM_DEF spot_params_bb_pm 8  R G B INTENSITY DX DY DZ WIDTH
#   WIDTH = a constant that makes the billboard more omnidirectional
SPILL = "spot_params_sp_pm"
GLOW = "spot_params_bb_pm"
# Named lights (fixed parameters): a sodium street lamp shining down.
STREET_LAMP = "amb_street_light2"

WARM = (1.0, 0.78, 0.52)  # sodium/halogen facade lighting
WHITE = (1.0, 0.98, 0.92)  # LED / metal halide floodlights


def local_to_obj(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Local (x east, y north, z up) to OBJ axes."""
    return x, z, -y


def _unit(d: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(c * c for c in d))
    if length < 1e-9:
        raise GeometryError("light direction must not be zero")
    return (d[0] / length, d[1] / length, d[2] / length)


def spot(
    x: float,
    y: float,
    z: float,
    direction: tuple[float, float, float],
    color: tuple[float, float, float] = WARM,
    intensity_cd: float = 6000.0,
    beam_deg: float = 60.0,
    glow: bool = True,
) -> list[Light]:
    """A spot light (local position and direction): the light it throws and, if
    `glow`, the bright point of the lamp itself."""
    if not 1.0 <= beam_deg <= 179.0:
        raise GeometryError("light: beam must be between 1 and 179 degrees")
    if not 10.0 <= intensity_cd <= 2_000_000.0:
        raise GeometryError("light: intensity must be between 10 and 2000000 cd")
    dx, dy, dz = _unit(local_to_obj(*direction))
    r, g, b = color
    width = math.cos(math.radians(beam_deg / 2))
    out = [Light(SPILL, x, y, z, (r, g, b, 1.0, intensity_cd, dx, dy, dz, width))]
    if glow:
        # The glow of the lamp: about a tenth of the spill, seen from the front mostly.
        out.append(Light(GLOW, x, y, z, (r, g, b, intensity_cd / 10, dx, dy, dz, 0.3)))
    return out


def street_lamp(x: float, y: float, z: float) -> Light:
    return Light(STREET_LAMP, x, y, z, (), named=True)


def obj_line(light: Light) -> str:
    """The OBJ8 line of a light (position converted to OBJ axes)."""
    x, y, z = local_to_obj(light.x, light.y, light.z)
    pos = f"{x:.3f} {y:.3f} {z:.3f}"
    if light.named:
        return f"LIGHT_NAMED {light.name} {pos}"
    return f"LIGHT_PARAM {light.name} {pos} " + " ".join(f"{p:.4g}" for p in light.params)

"""Object categories and surface types the app can build (the catalog agreed in the plan)."""

from __future__ import annotations

from enum import StrEnum


class ObjectCategory(StrEnum):
    BUILDINGS = "buildings"
    GRANDSTANDS = "grandstands"
    SCAFFOLDING = "scaffolding"
    CANOPIES = "canopies"
    BARRIERS = "barriers"
    MASTS = "masts"


class SurfaceType(StrEnum):
    GRASS = "grass"
    ASPHALT = "asphalt"
    CONCRETE = "concrete"
    GRAVEL = "gravel"
    DIRT = "dirt"  # speedway cinder, sand, compacted earth
    TARTAN = "tartan"
    MARKINGS = "markings"


class SurfaceMode(StrEnum):
    MATERIAL = "material"
    IMAGERY = "imagery"

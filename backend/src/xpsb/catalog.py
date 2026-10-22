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
    MONUMENTS = "monuments"
    BRIDGES = "bridges"


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
    NONE = "none"  # no surfaces at all: the ground of other sceneries (orthos) stays


# Names for unnamed objects in messages built on the server (progress, approvals).
CATEGORY_LABELS = {
    "de": {
        "buildings": "Gebäude",
        "grandstands": "Tribüne",
        "scaffolding": "Gerüst",
        "canopies": "Überdachung",
        "barriers": "Zaun/Mauer",
        "masts": "Mast",
        "monuments": "Denkmal",
        "bridges": "Brücke",
    },
    "en": {
        "buildings": "Building",
        "grandstands": "Grandstand",
        "scaffolding": "Scaffolding",
        "canopies": "Canopy",
        "barriers": "Fence/wall",
        "masts": "Mast",
        "monuments": "Monument",
        "bridges": "Bridge",
    },
}


def category_label(category: str, language: str) -> str:
    return CATEGORY_LABELS.get(language, CATEGORY_LABELS["en"]).get(category, category)

"""Read existing manifest AOIs without changing the shared area registry."""

import json
from pathlib import Path

from analog_pipelines.geology.models import Area, validate_areas


def polygon(west: float, south: float, east: float, north: float) -> dict:
    if not west < east or not south < north:
        raise ValueError("bbox must have positive width and height")
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def existing_areas(root: Path) -> list[Area]:
    areas = []
    for path in sorted((root / "pipelines/earth/manifests").glob("*.json")):
        data = json.loads(path.read_text())
        for candidate in data.get("candidates", []):
            bbox = candidate["bbox"]
            areas.append(
                Area(
                    area_id=candidate["location_id"],
                    display_name=candidate["display_name"],
                    body="Earth",
                    crs="EPSG:4326",
                    geometry=polygon(**bbox),
                    origin="existing",
                    geometry_source=str(path.relative_to(root)) + "#candidates/bbox",
                    limitations=[
                        "Local existing terrain AOI; not representative of the whole named region."
                    ],
                )
            )
    for path in sorted((root / "pipelines/planetary/manifests").glob("*.json")):
        data = json.loads(path.read_text())
        for reference in data.get("references", []):
            if reference["reference_id"] != "moon-shackleton-rim-site04-roi2":
                continue
            # The manifest names the authoritative source map centre and window size.
            # Fail rather than silently reusing these coordinates for a changed reference.
            if "X=-2757.5 m, Y=-6342.5 m" not in reference["notes"]:
                raise ValueError("authoritative lunar ROI centre changed; review geometry import")
            size = reference["window_size_m"] / 2
            areas.append(
                Area(
                    area_id=reference["reference_id"],
                    display_name=reference["display_name"],
                    body=reference["body"],
                    crs=reference["crs"],
                    geometry=polygon(
                        -2757.5 - size, -6342.5 - size, -2757.5 + size, -6342.5 + size
                    ),
                    origin="existing",
                    geometry_source=str(path.relative_to(root)) + "#references/notes",
                    limitations=[
                        "MOON_ME DE421 terrain ROI projected footprint; geology map uses Moon 2000/LOLA; frame alignment below map scale is not established."
                    ],
                )
            )
    return validate_areas(areas)


def load_areas(path: Path) -> list[Area]:
    data = json.loads(path.read_text())
    areas = data["areas"] if isinstance(data, dict) else data
    return validate_areas([Area.model_validate(area) for area in areas])

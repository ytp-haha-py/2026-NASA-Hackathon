"""Source-specific geology map importers and availability audit.

Provider exceptions are recorded independently by the runner. GLiM derived values
with unverified rights stay in the external cache, never the portable manifest.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urlencode

from analog_pipelines.geology.io import Cache, artifact, write_json
from analog_pipelines.geology.models import Analysis, Area, Dataset, Lithology
from analog_pipelines.geology.spatial import VectorIndex, area_shape, transform_geometry
from analog_pipelines.geology.taxonomy import glim_mapping, process_from_unit

GLIM = "https://services8.arcgis.com/4KhTMTZ1x0f76DSg/arcgis/rest/services/GLiM_Niveau_I/FeatureServer/1"
MOON_ZIP = "https://asc-astropedia.s3.us-west-2.amazonaws.com/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2.zip"
MARS_ZIP = "https://pubs.usgs.gov/sim/3464/sim3464_database.zip"


def base_analysis(area: Area, dataset: Dataset, **kwargs) -> Analysis:
    return Analysis(
        area_id=area.area_id,
        geometry_fingerprint=area.geometry_fingerprint,
        body=area.body,
        dataset_id=dataset.dataset_id,
        accessed_at=datetime.now(UTC),
        processing_method="geology-1.0.0",
        source_crs=dataset.source_crs,
        native_resolution_m=dataset.native_resolution_m,
        map_scale=dataset.map_scale,
        analysis_crs=None,
        valid_fraction=None,
        artifacts=[],
        quality_flags=[],
        limitations=list(dataset.limitations),
        **kwargs,
    )


def glim(area: Area, dataset: Dataset, cache: Cache) -> Analysis:
    from shapely.geometry import mapping, shape

    geometry = transform_geometry(area_shape(area), area.crs, "EPSG:4326")
    west, south, east, north = geometry.bounds
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "3857",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "orderByFields": "OBJECTID ASC",
        "resultRecordCount": "2000",
    }
    features, artifacts = [], []
    # Paginated bounded query; fail closed if the safety ceiling is exceeded.
    for page in range(50):
        params["resultOffset"] = str(page * 2000)
        url = GLIM + "/query?" + urlencode(params)
        data, receipt = cache.json(url)
        artifacts.append(receipt)
        if "error" in data:
            raise ValueError("GLiM server returned a query error")
        features.extend(data.get("features", []))
        if not data.get("exceededTransferLimit"):
            break
    else:
        raise ValueError("GLiM query exceeded bounded pagination budget")
    import fiona

    # ESRIJSON preserves multipart rings and holes; do not reinterpret each ring as a shell.
    converted = []
    for start in range(0, len(features), 2000):
        payload = {
            "geometryType": "esriGeometryPolygon",
            "spatialReference": {"wkid": 3857},
            "fields": data["fields"],
            "features": features[start : start + 2000],
        }
        with (
            fiona.io.MemoryFile(json.dumps(payload).encode(), ext=".json") as memory,
            memory.open(driver="ESRIJSON") as source,
        ):
            for feature in source:
                converted.append(
                    {
                        "type": "Feature",
                        "properties": dict(feature.properties),
                        "geometry": mapping(shape(feature.geometry)),
                    }
                )
    local_path = cache.root / f"glim-{area.geometry_fingerprint.split(':')[1]}.geojson"
    # Explicit non-WGS84 CRS is retained in the external, local-only intermediate.
    write_json(
        local_path,
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
            "features": converted,
        },
    )
    index = VectorIndex(local_path)
    rows, coverage, metric_crs = index.overlay(area)
    units, processes = [], []
    for fid, props, fraction in rows:
        code = str(props.get("xx") or props.get("Litho") or "")
        rocks, category = glim_mapping(code)
        unit = Lithology(
            evidence_id=f"glim:{props.get('OBJECTID', fid)}",
            source_feature_id=str(props.get("OBJECTID", fid)),
            unit_id=code,
            unit_name=str(props.get("xx_Description") or code),
            official_description=str(props.get("xx_Description") or "not_verified"),
            original_attributes=props,
            unified_rocks=rocks,
            overlap_fraction=fraction,
            interpretation="Broad source lithology only; no inferred specific mineral or rock species.",
        )
        units.append(unit)
        if category:
            processes.append(process_from_unit(unit, category, unit.official_description))
    local_result = {
        "area_id": area.area_id,
        "geometry_fingerprint": area.geometry_fingerprint,
        "valid_fraction": coverage,
        "analysis_crs": metric_crs,
        "lithology": [u.model_dump() for u in units],
        "geological_processes": [p.model_dump() for p in processes],
        "redistribution": "not_verified; do not commit this local result",
    }
    write_json(cache.root / f"glim-local-{area.area_id}.json", local_result)
    record = base_analysis(
        area,
        dataset,
        status="unverified",
        reason="Spatial overlay completed locally; mirror licenseInfo is empty. Extracted values withheld from portable output.",
    )
    record.artifacts = artifacts
    record.quality_flags = [
        "redistribution_not_verified",
        "third_party_mirror",
        "local_overlay_completed",
    ]
    return record


def moon_index(cache: Cache) -> tuple[VectorIndex, dict, object]:
    archive = cache.fetch(MOON_ZIP, "moon.zip")
    root = cache.unzip(archive, "moon")
    # The distributed FileGDB uses compressed tables not supported by every GDAL build.
    # The same complete package supplies a GeoUnits shapefile with its own spatial index.
    path = next(root.rglob("GeoUnits.shp"))
    table = next(root.rglob("Unified_Geologic_Map_of_the_Moon_DOMU_descriptions.csv"))
    with table.open(encoding="utf-8-sig", newline="") as stream:
        units = {row["Unit"]: row for row in csv.DictReader(stream)}
    return VectorIndex(path), units, artifact(archive, MOON_ZIP, "full_gis_database")


def moon(area: Area, dataset: Dataset, prepared: tuple) -> Analysis:
    index, descriptions, receipt = prepared
    rows, coverage, crs = index.overlay(area)
    record = base_analysis(area, dataset, status="success" if rows else "no_coverage")
    record.analysis_crs, record.valid_fraction = crs, coverage
    record.source_crs = index.crs
    record.artifacts = [receipt]
    record.quality_flags = [
        f"excluded_invalid_source_geometries:{index.invalid_count}",
        "map_scale_5000000",
        "frame_alignment_below_map_scale_unverified",
    ]
    for fid, props, fraction in rows:
        code = props["FIRST_Unit"]
        description = descriptions.get(code, {})
        unit = Lithology(
            evidence_id=f"moon-map:{fid}",
            source_feature_id=fid,
            unit_id=code,
            unit_name=description.get("Name", props["FIRST_Un_2"]),
            official_description=description.get("Description", "not_verified"),
            original_attributes=props,
            unified_rocks=[],
            overlap_fraction=fraction,
            interpretation=description.get("Interpretation", "not_verified"),
        )
        record.lithology.append(unit)
        # Exact reviewed source family. No inference of breccia/melt mineral abundance.
        family = props["FIRST_Un_2"]
        if family in {"Crater Unit", "Crater, Secondary", "Crater, Catena"}:
            record.geological_processes.append(
                process_from_unit(
                    unit,
                    "impact",
                    unit.official_description + " Interpretation: " + unit.interpretation,
                )
            )
    return record


def mars_index(cache: Cache) -> tuple[VectorIndex, dict, list]:
    archive = cache.fetch(MARS_ZIP, "mars.zip")
    root = cache.unzip(archive, "mars")
    metadata_url = "https://pubs.usgs.gov/sim/3464/sim3464_metadata.xml"
    metadata_path = cache.fetch(metadata_url, "mars-metadata.xml")
    xml = ET.parse(metadata_path)
    descriptions = {}
    for attr in xml.findall(".//attr"):
        if attr.findtext("attrlabl") == "name":
            for entry in attr.findall(".//edom"):
                descriptions[entry.findtext("edomv")] = (entry.findtext("edomvd") or "").strip()
    # The distributed XML enumerates names but leaves their definitions empty.
    # Read the official map sheet instead of inventing unit descriptions.
    map_url = "https://pubs.usgs.gov/sim/3464/sim3464.pdf"
    map_path = cache.fetch(map_url, "mars-map.pdf")
    text_path = cache.root / "mars-map.txt"
    if not text_path.exists():
        subprocess.run(["pdftotext", "-raw", str(map_path), str(text_path)], check=True, timeout=60)
    text = text_path.read_text()
    aliases = {
        "Jezero fan 1 unit": "Jezero fan unit 1",
        "Jezero fan 2 unit": "Jezero fan unit 2",
        "Nili Planum 1 unit": "Nili Planum unit 1",
        "Nili Planum 2 unit": "Nili Planum unit 2",
    }
    for name in descriptions:
        title = aliases.get(name, name)
        found = re.search(
            re.escape(title) + r"—(.+?Type\s+localit(?:y|ies)[^:]*:.+?° E\.(?:, respectively\.)?)",
            text,
            re.DOTALL,
        )
        if found:
            descriptions[name] = title + "—" + " ".join(found.group(1).split())
    return (
        VectorIndex(next(root.rglob("Nili.gdb")), "GeoUnits_v1"),
        descriptions,
        [
            artifact(archive, MARS_ZIP, "full_gis_database"),
            artifact(metadata_path, metadata_url, "official_unit_metadata"),
            artifact(map_path, map_url, "official_unit_descriptions_map_sheet"),
        ],
    )


def mars(area: Area, dataset: Dataset, prepared: tuple) -> Analysis:
    index, descriptions, receipts = prepared
    rows, coverage, crs = index.overlay(area)
    record = base_analysis(area, dataset, status="success" if rows else "no_coverage")
    record.analysis_crs, record.valid_fraction = crs, coverage
    record.source_crs = index.crs
    record.artifacts = receipts
    record.quality_flags = [
        f"excluded_invalid_source_geometries:{index.invalid_count}",
        "map_scale_75000",
    ]
    for fid, props, fraction in rows:
        name = props["name"]
        unit = Lithology(
            evidence_id=f"mars-map:{fid}",
            source_feature_id=fid,
            unit_id=props["symbol"],
            unit_name=name,
            official_description=descriptions.get(name) or "not_verified",
            original_attributes=props,
            unified_rocks=[],
            overlap_fraction=fraction,
            interpretation="Official geomorphological map unit; no inferred quantitative mineral abundance.",
        )
        record.lithology.append(unit)
        if name in {"Jezero fan 1 unit", "Jezero fan 2 unit"}:
            unit.unified_rocks = ["sedimentary_deposits", "fluvial_deposits", "lacustrine_deposits"]
        elif name == "Nili Planum fan unit":
            unit.unified_rocks = ["sedimentary_deposits", "fluvial_deposits"]
        elif name == "Crater ejecta unit":
            unit.unified_rocks = ["impact_ejecta"]
        if unit.official_description == "not_verified":
            record.quality_flags.append(f"unit_description_not_verified:{unit.unit_id}")
        if name in {"Jezero fan 1 unit", "Jezero fan 2 unit", "Nili Planum fan unit"}:
            record.geological_processes.append(
                process_from_unit(
                    unit,
                    "sedimentary",
                    "Official SIM3464 map-sheet interpretation identifies deltaic/lacustrine or alluvial/fluvial deposits for this fan unit. "
                    + unit.official_description,
                )
            )
        if name in {"Crater rim unit", "Crater ejecta unit", "Crater floor unit"}:
            record.geological_processes.append(
                process_from_unit(unit, "impact", unit.official_description)
            )
    return record

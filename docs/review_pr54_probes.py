"""Small offline diagnostics for PR54; synthetic software evidence only.

Supply the checkout root as argv[1] and its modules through PYTHONPATH.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from analog_api.adapters.seed import seed_manifests
from analog_api.adapters.typed_db import init_typed_db
from analog_api.domain.manifest import Manifest
from analog_pipelines import geomorpho
from analog_pipelines.coarse_features import extract_coarse_features
from analog_pipelines.coarse_grid import GridRegion, generate_region_grid
from analog_pipelines.geomorpho import read_window
from analog_pipelines.typed_contracts import migrate_v1_manifest
from pyproj import Transformer
from rasterio.transform import from_bounds
from sqlalchemy import create_engine, inspect, text

root = Path(sys.argv[1]).resolve(strict=True)
raw = json.loads((root / "pipelines/earth/manifests/copernicus_glo90_earth.json").read_text())
raw["candidates"] = raw["candidates"][:1]
c = raw["candidates"][0]
c["window_size_m"] = 600.0
c["analysis_resolution_m"] = 30.0
for obs in c["features"].values():
    obs["valid_fraction"] = 0.83
    obs["extraction_scale_m"] = 30.0
Manifest.model_validate(raw)
m = migrate_v1_manifest(raw)
o = next(x for x in m.observations if x.feature_id in c["features"])
print(
    json.dumps(
        {
            "probe": "valid_v1_migration",
            "input_window_m": 600,
            "input_resolution_m": 30,
            "output_support": o.spatial_support.model_dump(),
            "input_valid_fraction": 0.83,
            "output_valid_fraction": o.model_dump().get("valid_fraction", "FIELD_NOT_REPRESENTED"),
            "output_coordinate_frame": m.areas[0].coordinate_frame,
            "input_crs": c["crs"],
            "input_wgs84_bbox": c["bbox"],
            "output_area_bbox": m.areas[0].bbox,
            "output_area_crs": m.areas[0].crs,
        }
    )
)

region = GridRegion("same-id", "TEST ONLY", 5.8, 60.0, 6.4, 60.35)
cells, coverage = generate_region_grid(region)
other, _ = generate_region_grid(region, window_size_m=10000)
print(
    json.dumps(
        {
            "probe": "grid_identity",
            "id_5km": cells[0].candidate_id,
            "id_10km": other[0].candidate_id,
            "same_geometry": cells[0].bbox_metric == other[0].bbox_metric,
            "coverage_label": coverage.sample_label,
            "uncovered_margin_m": coverage.uncovered_margin_m,
        }
    )
)

cell = cells[0]
b = cell.bbox_wgs84
size = 160
t = from_bounds(b["west"], b["south"], b["east"], b["north"], size, size)
rows, cols = np.indices((size, size))
lon = t.c + (cols + 0.5) * t.a
lat = t.f + (rows + 0.5) * t.e
x, y = Transformer.from_crs("EPSG:4326", cell.crs, always_xy=True).transform(lon, lat)
mb = cell.bbox_metric
inside = (x >= mb["west"]) & (x <= mb["east"]) & (y >= mb["south"]) & (y <= mb["north"])
with tempfile.TemporaryDirectory(prefix="pr54-probes-") as tmp:
    path = Path(tmp) / "synthetic.tif"
    values = np.where(inside, 10.0, 80.0).astype("float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=t,
    ) as dst:
        dst.write(values, 1)
    w = read_window(path, bbox=b)
    print(
        json.dumps(
            {
                "probe": "projected_footprint",
                "inside_mean": float(values[inside].mean()),
                "bbox_mean": extract_coarse_features(w.data, w.valid_mask)["coarse_slope_mean_deg"],
                "outside_pixel_fraction": float((~inside).mean()),
            }
        )
    )
    projected_reader = getattr(geomorpho, "read_projected_window", None)
    if projected_reader is not None:
        projected = projected_reader(path, bbox_metric=mb, target_crs=cell.crs)
        print(
            json.dumps(
                {
                    "probe": "new_projected_reader",
                    "shape": projected.data.shape,
                    "resolution_m": projected.resolution_m,
                    "bbox_metric_matches": projected.bbox_metric == mb,
                    "mean": extract_coarse_features(projected.data, projected.valid_mask)[
                        "coarse_slope_mean_deg"
                    ],
                }
            )
        )

engine = create_engine("sqlite+pysqlite:///:memory:")
init_typed_db(engine)
with engine.begin() as conn:
    conn.execute(
        text(
            "INSERT INTO typed_areas (area_id, body, crs, coordinate_frame) VALUES ('user-typed-area', 'Earth', 'EPSG:4326', 'earth_wgs84')"
        )
    )
seed_manifests(engine, [], reset=False)
with engine.connect() as conn:
    before = conn.execute(text("SELECT count(*) FROM typed_areas")).scalar()
seed_manifests(engine, [], reset=True)
with engine.connect() as conn:
    after = conn.execute(text("SELECT count(*) FROM typed_areas")).scalar()
print(
    json.dumps(
        {
            "probe": "sqlite_reset",
            "typed_rows_after_nonreset": before,
            "typed_rows_after_reset": after,
            "table_exists": "typed_areas" in inspect(engine).get_table_names(),
        }
    )
)

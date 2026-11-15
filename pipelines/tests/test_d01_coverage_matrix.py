import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_d01_coverage_matrix_has_required_shape_and_bodies():
    matrix = _load("docs/data-sources/d01-coverage-matrix.json")
    assert matrix["dimension_code"] == "D01"
    assert matrix["canonical_fine_features"] == [
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    ]
    entries = matrix["entries"]
    assert isinstance(entries, list) and entries
    required = {
        "body",
        "feature_id",
        "source_id",
        "provider",
        "product",
        "version",
        "licence",
        "actual_indexed_queryable_coverage",
        "data_status",
        "time_support",
        "depth_support",
        "spatial_support",
        "crs",
        "qa",
        "uncertainty",
        "legal_score_filter_roles",
        "gaps",
        "rebuild_instructions",
    }
    bodies = set()
    for entry in entries:
        missing = required - set(entry)
        assert not missing, f"{entry.get('body')}:{entry.get('feature_id')} missing {missing}"
        bodies.add(entry["body"])
        assert entry["dimension_code"] == "D01"
        assert isinstance(entry["gaps"], list)
        roles = entry["legal_score_filter_roles"]
        assert "similarity" in roles and "hard_filter" in roles and "evidence" in roles
    assert {"Earth", "Moon", "Mars"} <= bodies


def test_d01_coverage_matrix_matches_committed_manifest_counts():
    matrix = _load("docs/data-sources/d01-coverage-matrix.json")
    earth = _load("pipelines/earth/manifests/copernicus_glo90_earth.json")
    moon = _load("pipelines/planetary/manifests/lola_site01_references.json")
    moon_manifest_paths = sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "pipelines/planetary/manifests").glob("lola*.json")
    )
    moon_all = sum(len(_load(p)["references"]) for p in moon_manifest_paths)
    mars_paths = sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "pipelines/planetary/manifests").glob("ctx_mars*.json")
    )
    mars_all = sum(len(_load(p)["references"]) for p in mars_paths)
    mars = _load("pipelines/planetary/manifests/ctx_mars_references.json")
    coarse = _load("docs/global-terrain-results/coverage.json")

    entries = {(e["body"], e["feature_id"], e.get("source_id")): e for e in matrix["entries"]}
    fine = matrix["canonical_fine_features"]
    for fid in fine:
        assert entries[("Earth", fid, "copernicus-dem-glo90")]["actual_indexed_queryable_coverage"][
            "indexed_windows"
        ] == len(earth["candidates"])
        assert (
            entries[("Moon", fid, "nasa-lola-south-pole-sites-5m")][
                "actual_indexed_queryable_coverage"
            ]["indexed_windows"]
            == moon_all
        )
        assert (
            entries[("Mars", fid, "usgs-m20-jezero-ctx-dem-20m")][
                "actual_indexed_queryable_coverage"
            ]["indexed_windows"]
            == mars_all
        )
    for fid in ["coarse_slope_mean_deg", "coarse_slope_p90_deg", "coarse_flat_fraction_lt5deg"]:
        assert (
            entries[("Earth", fid, "geomorpho250-slope")]["actual_indexed_queryable_coverage"][
                "indexed_cells"
            ]
            == coarse["counts"]["cells"]
        )

    # Representative query manifests contain the five fine features.
    for ref in mars["references"][:1] + moon["references"][:1]:
        assert set(ref["features"].keys()) == set(fine)
    assert set(earth["candidates"][0]["features"].keys()) == set(fine)


def test_d01_coverage_matrix_includes_blockers_and_representative_queries():
    matrix = _load("docs/data-sources/d01-coverage-matrix.json")
    blockers = matrix["verified_blockers_and_alternatives"]
    assert isinstance(blockers, list) and blockers
    assert any(b["status"] == "blocked_or_unverified" and b.get("alternative") for b in blockers)
    queries = matrix["representative_queries"]
    for key in ["Earth_fine", "Moon_fine", "Mars_fine"]:
        assert "reproducible_command" in queries[key]
        assert "manifest" in queries[key]

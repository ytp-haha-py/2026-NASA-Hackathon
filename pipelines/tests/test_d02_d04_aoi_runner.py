from __future__ import annotations

from analog_pipelines.d02_d04_aoi.run_aoi import bbox_around_point, build_typed_observations_stub


def test_bbox_around_point() -> None:
    bbox = bbox_around_point(77.5, 18.3, half_deg=0.05)
    assert bbox["west"] == 77.45
    assert bbox["east"] == 77.55
    assert bbox["south"] == 18.25
    assert bbox["north"] == 18.35


def test_build_typed_observations_stub_shapes() -> None:
    bbox = {"west": 1, "south": 2, "east": 3, "north": 4}
    d02 = {
        "outcome": "no_coverage",
        "source": "s2",
        "query_url": "u2",
        "query_provenance": {},
        "notes": "n2",
    }
    d03 = {"outcome": "no_coverage", "source": "s3", "checked": ["c3"], "notes": "n3"}
    d04 = {"outcome": "no_coverage", "source": "s4", "checked": ["c4"], "notes": "n4"}
    obs = build_typed_observations_stub("aoi1", bbox, d02, d03, d04)
    assert len(obs) == 3
    dims = {o["dimension_code"] for o in obs}
    assert dims == {"D02", "D03", "D04"}
    for o in obs:
        assert o["scoring_eligible"] is False
        assert "state" in o and "role" in o and "provenance" in o
        assert o["aoi"]["id"] == "aoi1"

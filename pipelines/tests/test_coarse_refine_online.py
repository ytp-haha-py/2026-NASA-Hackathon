"""Opt-in online refinement check for the real 90 m provider (Issue #37).

Reads one bounded 1.98 km Copernicus GLO-90 window plus its WorldCover context. Never
part of the default suite:

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/tests/test_coarse_refine_online.py -q -m network
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from analog_api.domain.features import FEATURE_BY_ID
from analog_pipelines.coarse_index import CoarseIndexRepository
from analog_pipelines.refine import derive_focus_window, select_hits
from analog_pipelines.refine_earth import RefineEarthProvider

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run the opt-in online refinement check",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "pipelines/coarse/real-pilot/index"
SNAPSHOT = "geomorpho250-real-pilot-20261115"


def test_real_provider_refines_one_bounded_sahara_focus():
    repository = CoarseIndexRepository(INDEX, snapshot_id=SNAPSHOT)
    selection = select_hits(repository, limit=3)
    hit = next(hit for hit in selection.hits if hit["region_id"] == "pilot-sahara")
    focus = derive_focus_window(hit)
    measurement = RefineEarthProvider(timeout=30)(focus)
    assert measurement.data_kind == "observed"
    assert set(measurement.features) == set(FEATURE_BY_ID)
    assert 0.8 <= measurement.valid_fraction <= 1.0
    assert measurement.checksum.startswith("sha256:")
    assert measurement.provider_status["copernicus-dem-glo90"]["status"] == "verified"
    assert measurement.provider_status["copernicus-dem-glo90"]["source_tiles"]
    assert measurement.method_versions["terrain"] == "terrain-metrics-1.0.0"
    assert measurement.context_observations

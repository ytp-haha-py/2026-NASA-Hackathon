"""Labelled synthetic terrain fixture for buffer/validity tests only.

This is not an observed DEM, not a planetary analog, and must never be written to a
manifest or published as coverage. It exists to exercise the local operator and
edge-validity bookkeeping with known analytic geometry.
"""

from __future__ import annotations

import numpy as np

SYNTHETIC_LABEL = "synthetic_fixture_not_an_observation"
SYNTHETIC_METHOD = "synthetic-analytic-terrain-1.0.0"


def quadratic_bowl(size: int = 46, cell_m: float = 90.0, a: float = 1.0e-4) -> np.ndarray:
    """z = 0.5*a*(x^2+y^2), the analytic test surface used by the curvature tests."""
    centre = (size - 1) / 2.0
    y, x = np.meshgrid(
        (np.arange(size) - centre) * cell_m,
        (np.arange(size) - centre) * cell_m,
        indexing="ij",
    )
    return 0.5 * a * (x * x + y * y)


def gaussian_hill(size: int = 46, cell_m: float = 90.0) -> np.ndarray:
    """A smooth hill whose curvature is concentrated near the centre.

    The normal 22x22 crop can only support an interior subset; the buffered crop
    supports the whole core. This makes the edge-bias summary difference visible
    without pretending that the synthetic values are observations.
    """
    centre = (size - 1) / 2.0
    y, x = np.meshgrid(
        (np.arange(size) - centre) * cell_m,
        (np.arange(size) - centre) * cell_m,
        indexing="ij",
    )
    sigma_m = 4.5 * cell_m
    return 120.0 * np.exp(-(x * x + y * y) / (2.0 * sigma_m * sigma_m))

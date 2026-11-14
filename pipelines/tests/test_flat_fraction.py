import math

import numpy as np
import pytest
from analog_pipelines.features import ExtractionError, flat_terrain_fraction


def plane(degrees):
    return np.tile(np.arange(22) * 90 * math.tan(math.radians(degrees)), (22, 1))


def test_flat_and_steep_known_planes():
    assert flat_terrain_fraction(plane(0), 90) == 1
    assert flat_terrain_fraction(plane(4.9), 90) == 1
    assert flat_terrain_fraction(plane(5.1), 90) == 0


def test_nodata_is_excluded_from_both_counts():
    z = plane(0)
    z[5:8, 5:8] = np.nan
    assert flat_terrain_fraction(z, 90) == 1
    with pytest.raises(ExtractionError):
        flat_terrain_fraction(np.full((22, 22), np.nan), 90)


def test_mixed_slopes_use_valid_slope_denominator():
    z = np.zeros((22, 22))
    z[:, 11:] = np.arange(11) * 90 * 0.2
    # Interior columns 1..10 are flat, 11..20 are sloped (10+10 cells per row).
    assert flat_terrain_fraction(z, 90) == pytest.approx(0.5)
    domain = np.zeros_like(z, dtype=bool)
    domain[1:21, 1:11] = True
    assert flat_terrain_fraction(z, 90, domain_mask=domain) == 1

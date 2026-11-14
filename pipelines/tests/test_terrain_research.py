"""Analytic validation only, not observed landform classifications."""

import numpy as np
import pytest
from analog_pipelines.terrain_research import landform_hellinger, surface_curvedness


def test_curvedness_distinguishes_saddle_and_plane_without_sign_cancellation():
    y, x = np.meshgrid(np.arange(-11, 12) * 90, np.arange(-11, 12) * 90, indexing="ij")
    a = 0.0001
    plane = surface_curvedness(0.1 * x + 0.2 * y, 90)
    assert np.nanmax(plane) < 1e-14
    bowl = surface_curvedness(0.5 * a * (x * x + y * y), 90)
    saddle = surface_curvedness(0.5 * a * (x * x - y * y), 90)
    assert bowl[11, 11] == pytest.approx(a)
    assert saddle[11, 11] == pytest.approx(a)
    np.testing.assert_allclose(
        bowl, surface_curvedness(-0.5 * a * (x * x + y * y), 90), equal_nan=True
    )
    assert np.isnan(bowl[0]).all()


def test_curvedness_never_fills_missing_neighborhoods():
    z = np.zeros((22, 22))
    z[10, 10] = np.nan
    result = surface_curvedness(z, 90)
    assert np.isnan(result[9:12, 9:12]).all()
    assert result[5, 5] == 0


def test_composition_distance_is_one_bounded_vector_comparison():
    a = np.zeros(10)
    a[0] = 1
    b = np.zeros(10)
    b[9] = 1
    assert landform_hellinger(a, a) == 0
    assert landform_hellinger(a, b) == pytest.approx(1)
    assert landform_hellinger(a, b) == landform_hellinger(b, a)
    with pytest.raises(ValueError):
        landform_hellinger(np.zeros(10), a)
    with pytest.raises(ValueError):
        landform_hellinger(np.ones(9) / 9, a)
    with pytest.raises(ValueError):
        landform_hellinger([np.nan] * 10, a)

import math

from batch_ingest import candidates
from pyproj import Geod


def test_finite_plan_keeps_originals_and_disjoint_windows():
    windows = candidates()
    assert len(windows) == 113
    assert len({w.location_id for w in windows}) == 113
    assert windows[0].location_id == "earth-atacama-desert"
    geod = Geod(ellps="WGS84")
    for start in range(5, len(windows), 9):
        region = windows[start : start + 9]
        for i, a in enumerate(region):
            assert -80 <= a.lat <= 84
            for b in region[i + 1 :]:
                # 3 km grid spacing exceeds the 1980 m square's diagonal.
                distance = geod.inv(a.lon, a.lat, b.lon, b.lat)[2]
                assert distance > 1980 * math.sqrt(2)

import io
import struct
import tarfile

import pytest
from roi_centres import roi_centres


def archive(x, y, with_shape=True):
    header = bytearray(97)
    struct.pack_into("<I", header, 4, 1)
    struct.pack_into("<HH", header, 8, 97, 25)
    for i, name in enumerate((b"X", b"Y")):
        header[32 + 32 * i : 33 + 32 * i] = name
        header[32 + 32 * i + 11] = ord("N")
        header[32 + 32 * i + 16] = 12
    header[96] = 13
    dbf = bytes(header) + b" " + x.encode().rjust(12) + y.encode().rjust(12)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        files = {"Site_ROIs.dbf": dbf}
        if with_shape:
            geom = struct.pack("<I4d", 5, 100.0, 200.0, 300.0, 400.0)
            files["Site_ROIs.shp"] = bytes(100) + struct.pack(">II", 1, len(geom) // 2) + geom
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def test_official_finite_dbf_coordinates_take_precedence():
    assert roi_centres(archive("-12.5", "-22.5")) == [(1, -12.5, -22.5)]


def test_overflow_uses_corresponding_official_polygon_bounds():
    assert roi_centres(archive("********", "********")) == [(1, 200.0, 300.0)]
    with pytest.raises(ValueError, match="neither valid"):
        roi_centres(archive("********", "********", with_shape=False))

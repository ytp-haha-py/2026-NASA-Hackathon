"""Read NASA's small ROI DBF directly without adding a GIS dependency."""

from __future__ import annotations

import io
import math
import struct
import tarfile


def roi_centres(archive: bytes) -> list[tuple[int, float, float]]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        dbfs = [m for m in tar.getmembers() if m.name.endswith(".dbf") and m.isfile()]
        if len(dbfs) != 1:
            raise ValueError("Expected exactly one regular ROI DBF")
        data = tar.extractfile(dbfs[0]).read()
        shapes = [m for m in tar.getmembers() if m.name.endswith(".shp") and m.isfile()]
        shape_data = tar.extractfile(shapes[0]).read() if len(shapes) == 1 else b""
    # ESRI shapefile records: 8-byte big-endian record header, little-endian geometry.
    bounds = {}
    offset = 100
    while offset + 8 <= len(shape_data):
        number, words = struct.unpack_from(">II", shape_data, offset)
        content = shape_data[offset + 8 : offset + 8 + 2 * words]
        if len(content) >= 36 and struct.unpack_from("<I", content)[0] in {5, 15, 25}:
            bounds[number] = struct.unpack_from("<4d", content, 4)
        offset += 8 + 2 * words
    count = struct.unpack_from("<I", data, 4)[0]
    header, length = struct.unpack_from("<HH", data, 8)
    fields = []
    offset = 32
    while data[offset] != 13:
        descriptor = data[offset : offset + 32]
        fields.append((descriptor[:11].split(b"\0")[0].decode(), descriptor[16]))
        offset += 32
    result = []
    for row in range(count):
        record = data[header + row * length : header + (row + 1) * length]
        if record[:1] == b"*":
            continue
        values = {}
        offset = 1
        for name, width in fields:
            values[name.upper()] = record[offset : offset + width].decode().strip()
            offset += width
        try:
            x, y = float(values["X"]), float(values["Y"])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("nonfinite DBF coordinates")
        except (KeyError, ValueError):
            # Some official DBFs overflow numeric fields as ********. Use the
            # corresponding official polygon bbox, never invent coordinates.
            if row + 1 not in bounds:
                raise ValueError(
                    f"ROI {row + 1} has neither valid X/Y nor polygon bounds"
                ) from None
            west, south, east, north = bounds[row + 1]
            x, y = (west + east) / 2, (south + north) / 2
        result.append((row + 1, x, y))
    return result

"""Offline tests for the planetary/LOLA importer.

No test in this directory may touch the public network. The committed real manifest
is validated against the JSON Schema and the API ``Manifest`` model. Importer helper
and extraction tests use a local, clearly named TEST-ONLY synthetic lunar-like plane
created in pytest's temporary directory; it is never written into the real manifest.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The shared extractor's approximate centre transform requires this override for a
# lunar projected CRS. The importer module also sets it; setting it here keeps the
# offline tests explicit.
os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")

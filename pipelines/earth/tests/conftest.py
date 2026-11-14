"""Offline tests for the Earth importer.

No test in this directory may touch the network. The committed manifest is tested
against the JSON Schema and the API Pydantic model, and helper/crop behaviour is
tested with local synthetic rasters only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

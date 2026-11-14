"""Offline tests for the ESA WorldCover context importer.

No test in this directory may touch the network. The committed manifest is tested
against the JSON Schema and the API Pydantic model; class-proportion logic is tested
with local synthetic arrays only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#!/usr/bin/env python
"""Write the bounded D02 mineral spectral evidence report (Issue #42).

Reads only the pinned geology snapshot, the pinned typed-v2 export and the
committed coverage audit. No network access, no credentials and no abundance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analog_pipelines.geology.mineral_evidence import DEFAULT_ROOT, write_mineral_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    report = write_mineral_evidence(args.root)
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

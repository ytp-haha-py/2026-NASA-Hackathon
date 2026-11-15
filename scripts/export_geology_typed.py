#!/usr/bin/env python
"""Export the Geology pilot record as an evidence-only typed-v2 bundle."""

from __future__ import annotations

import argparse
import json

from analog_pipelines.geology.typed_export import write_geology_typed_export


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--record",
        default="pipelines/geology/records/geology-e920e7e3ae79e176.json",
    )
    parser.add_argument("--output", default="pipelines/geology/typed")
    args = parser.parse_args()
    manifest = write_geology_typed_export(args.record, args.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

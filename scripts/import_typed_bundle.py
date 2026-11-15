#!/usr/bin/env python
"""Import a portable typed-v2 bundle into an additive SQLite database."""

from __future__ import annotations

import argparse
import json

from analog_api.adapters.seed import make_engine
from analog_api.adapters.typed_bundle import import_typed_bundle
from analog_api.adapters.typed_db import init_typed_db
from sqlalchemy.orm import Session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--database", default="var/analog.db")
    args = parser.parse_args()
    url = args.database if "://" in args.database else f"sqlite+pysqlite:///{args.database}"
    engine = make_engine(url)
    init_typed_db(engine)
    with Session(engine) as session:
        result = import_typed_bundle(session, args.bundle)
        session.commit()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

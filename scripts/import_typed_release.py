#!/usr/bin/env python
"""Import a portable acquisition release into an additive typed-v2 SQLite database."""

from __future__ import annotations

import argparse
import json

from analog_api.adapters.release_import import import_release
from analog_api.adapters.seed import make_engine
from analog_api.adapters.typed_db import init_typed_db


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--database", default="var/analog.db")
    args = parser.parse_args()
    url = args.database if "://" in args.database else f"sqlite+pysqlite:///{args.database}"
    engine = make_engine(url)
    init_typed_db(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        result = import_release(session, args.release)
        session.commit()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

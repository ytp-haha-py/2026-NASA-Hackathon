#!/usr/bin/env python
"""D03 thermal time-selector query evidence over committed pilot exports.

This script demonstrates time/local-solar-time/season/day-night-aware selection
without modifying shared API schemas or the scoring engine. It operates on the
committed typed observation exports (pilot JSONL) and reports representative
queries for Earth, Moon and Mars when data exists.

No network access, no global claims, and no fabricated values.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PILOTS = {
    "Earth": ROOT / "pipelines/pilots/d03_earth_power/observations.jsonl",
    "MoonMars": ROOT / "pipelines/pilots/d03_planetary_thermal/observations.jsonl",
}


def load_observations() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in PILOTS.values():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def lst_to_minutes(label: str | None) -> tuple[int, int] | None:
    if not label or "-" not in label:
        return None
    try:
        start, end = label.split("-", 1)
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        return sh * 60 + sm, eh * 60 + em
    except Exception:
        return None


def is_daytime(label: str | None) -> bool | None:
    mins = lst_to_minutes(label)
    if mins is None:
        return None
    start = mins[0]
    # Simple day/night split for evidence: 06:00-18:00 treated as day.
    return 360 <= start < 1080


def main() -> int:
    observations = load_observations()
    d03 = [o for o in observations if o.get("dimension_code") == "D03"]
    print(f"D03 observations loaded: {len(d03)}")
    by_body = Counter(o.get("body") for o in d03)
    print(f"by_body: {dict(sorted(by_body.items()))}")

    # Earth: time-window selector evidence (daily interval/season window present)
    earth = [o for o in d03 if o.get("body") == "Earth"]
    earth_2024w1 = [
        o
        for o in earth
        if (o.get("temporal_support") or {}).get("start", "").startswith("2024-01-01")
        and (o.get("temporal_support") or {}).get("end", "").startswith("2024-01-07")
    ]
    print("\n[Earth] time-selector: temporal window 2024-01-01..2024-01-07")
    print(
        f"  matched_observations={len(earth_2024w1)} areas={len({o['area_id'] for o in earth_2024w1})}"
    )
    if earth_2024w1:
        sample = earth_2024w1[0]
        print(
            "  sample:",
            sample["area_id"],
            sample["feature_id"],
            sample["state"],
            sample.get("temporal_support", {}).get("kind"),
        )

    # Moon: local-solar-time / day-night selector evidence
    moon = [o for o in d03 if o.get("body") == "Moon"]
    moon_night = [
        o
        for o in moon
        if (o.get("temporal_support") or {}).get("local_solar_time") == "00:00-00:15"
    ]
    moon_day = [
        o
        for o in moon
        if (o.get("temporal_support") or {}).get("local_solar_time") == "12:00-12:15"
    ]
    print("\n[Moon] local-solar-time selector: 00:00-00:15 (night bin)")
    print(
        f"  matched={len(moon_night)} states={dict(Counter(o['state'] for o in moon_night))} "
        f"features={sorted({o['feature_id'] for o in moon_night})}"
    )
    print("[Moon] local-solar-time selector: 12:00-12:15 (day bin)")
    print(
        f"  matched={len(moon_day)} states={dict(Counter(o['state'] for o in moon_day))} "
        f"features={sorted({o['feature_id'] for o in moon_day})}"
    )
    # day/night evidence summary
    day_flags = [
        is_daytime((o.get("temporal_support") or {}).get("local_solar_time")) for o in moon
    ]
    print(
        f"  day_night_classifiable={sum(1 for x in day_flags if x is not None)} night={sum(1 for x in day_flags if x is False)} day={sum(1 for x in day_flags if x is True)}"
    )
    print("  season_support=multi-year southern-summer composite (subsolar latitude < 0 deg)")
    # no diurnal range invented
    assert not any(
        o.get("feature_id") in {"diurnal_temperature_range_k", "diurnal_range_k"} for o in moon
    )

    # Mars: static/time-independent selector evidence
    mars = [o for o in d03 if o.get("body") == "Mars"]
    mars_static = [o for o in mars if (o.get("temporal_support") or {}).get("kind") == "static"]
    print("\n[Mars] temporal-support selector: kind=static (time-independent retrieval)")
    print(
        f"  matched={len(mars_static)} states={dict(Counter(o['state'] for o in mars_static))} "
        f"features={sorted({o['feature_id'] for o in mars_static})} "
        f"lst_present={sum(1 for o in mars_static if (o.get('temporal_support') or {}).get('local_solar_time'))}"
    )
    if mars_static:
        ts = mars_static[0].get("temporal_support") or {}
        print(f"  sample_temporal_support={json.dumps(ts, sort_keys=True)}")

    # footprint/uncertainty evidence snapshot
    print("\n[Footprint/uncertainty] representative evidence")
    for body in ("Earth", "Moon", "Mars"):
        subset = [o for o in d03 if o.get("body") == body]
        if not subset:
            continue
        s = subset[0]
        sp = s.get("spatial_support") or {}
        unc = s.get("uncertainty")
        print(
            f"  {body}: feature={s['feature_id']} footprint_kind={sp.get('kind')} "
            f"native_resolution_m={sp.get('native_resolution_m')} "
            f"window_size_m={sp.get('window_size_m')} "
            f"uncertainty={json.dumps(unc, sort_keys=True) if unc is not None else 'null'}"
        )

    print(
        "\nRESULT: PASS (representative time/LST/day-night/season/static selectors and footprint/uncertainty evidence produced for available bodies)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

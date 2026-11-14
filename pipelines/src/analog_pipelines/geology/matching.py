"""Evidence-based category matching and strictly compatible mineral comparisons.

No terrain distance, missing-value imputation, or cross-instrument abundance conversion.
"""

from analog_pipelines.geology.models import Analysis, Mineral
from analog_pipelines.geology.taxonomy import CANDIDATES, GLIM_ROCKS

VALID_STATES = {"detected", "not_detected_under_valid_observation"}


def compare_minerals(left: Mineral, right: Mineral) -> dict:
    result = {
        "mineral": left.mineral,
        "compared": False,
        "reason": None,
        "left_state": left.state,
        "right_state": right.state,
        "left_valid_fraction": left.valid_fraction,
        "right_valid_fraction": right.valid_fraction,
        "evidence_ids": [left.evidence_id, right.evidence_id],
        "absolute_difference": None,
        "unit": None,
    }
    if left.mineral != right.mineral:
        result["reason"] = "different_minerals"
    elif left.state not in VALID_STATES or right.state not in VALID_STATES:
        result["reason"] = "missing_or_inapplicable_observation"
    elif left.quality != "verified" or right.quality != "verified":
        result["reason"] = "context_only_or_unverified"
    elif not left.semantics_id or (left.semantics_id, left.method, left.measurement, left.unit) != (
        right.semantics_id,
        right.method,
        right.measurement,
        right.unit,
    ):
        result["reason"] = "incompatible_observation_semantics"
    else:
        result.update(
            compared=True, reason="compatible_semantics", agrees=left.state == right.state
        )
        if left.value is not None and right.value is not None:
            result.update(absolute_difference=abs(left.value - right.value), unit=left.unit)
    return result


def match_categories(
    records: list[Analysis], requested: list[str], rocks: list[str] | None = None
) -> dict:
    rocks = rocks or []
    if (
        not (requested or rocks)
        or len(requested) != len(set(requested))
        or len(rocks) != len(set(rocks))
    ):
        raise ValueError("request unique, nonempty categories or rocks")
    if not set(requested) <= CANDIDATES.keys():
        raise ValueError("unknown geology category")
    vocabulary = {v for entries in [*CANDIDATES.values(), *GLIM_ROCKS.values()] for v in entries}
    if not set(rocks) <= vocabulary:
        raise ValueError("unknown rock/deposit request")
    areas = {}
    identities = {}
    for record in records:
        if record.area_id in identities and identities[record.area_id] != (
            record.geometry_fingerprint,
            record.body,
        ):
            raise ValueError("same Area ID has incompatible geometry or body")
        identities[record.area_id] = (record.geometry_fingerprint, record.body)
        key = (record.area_id, record.geometry_fingerprint, record.body)
        areas.setdefault(key, []).append(record)
    results = []
    for (area_id, fingerprint, body), providers in areas.items():
        if body != "Earth":
            continue
        evidence, rock_evidence = [], []
        for record in providers:
            if record.status not in {"success", "partial"}:
                continue
            for process in record.geological_processes:
                if process.category in requested:
                    evidence.append(
                        {
                            "category": process.category,
                            "evidence_ids": process.evidence_ids,
                            "supporting_statement": process.supporting_statement,
                            "dataset_id": record.dataset_id,
                            "valid_fraction": record.valid_fraction,
                            "limitations": record.limitations,
                        }
                    )
            for unit in record.lithology:
                for rock in sorted(set(unit.unified_rocks) & set(rocks)):
                    rock_evidence.append(
                        {
                            "rock": rock,
                            "evidence_id": unit.evidence_id,
                            "dataset_id": record.dataset_id,
                            "unit_id": unit.unit_id,
                            "source_description": unit.official_description,
                            "overlap_fraction": unit.overlap_fraction,
                        }
                    )
        supported = sorted({e["category"] for e in evidence})
        supported_rocks = sorted({e["rock"] for e in rock_evidence})
        results.append(
            {
                "area_id": area_id,
                "geometry_fingerprint": fingerprint,
                "matched_categories": supported,
                "unknown_categories": sorted(set(requested) - set(supported)),
                "matched_rocks": supported_rocks,
                "unknown_rocks": sorted(set(rocks) - set(supported_rocks)),
                "supported_request_count": len(supported) + len(supported_rocks),
                "evidence": evidence,
                "rock_evidence": rock_evidence,
                "provider_status": {r.dataset_id: r.status for r in providers},
            }
        )
    results.sort(key=lambda r: (-r["supported_request_count"], r["area_id"]))
    return {
        "scope": "Earth AOIs in this supplied subset only; no global/exhaustive claim",
        "requested_categories": requested,
        "requested_rocks": rocks,
        "indexed_earth_count": len(results),
        "results": results,
        "limitations": [
            "Counts describe evidence-supported requests, not physical similarity percentages.",
            "Absent category evidence means unknown, never proven geological absence.",
            "Mineral presence alone does not activate a geological process category.",
        ],
    }

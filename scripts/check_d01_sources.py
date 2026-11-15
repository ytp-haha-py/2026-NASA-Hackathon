#!/usr/bin/env python3
"""Verify the D01 Moon/Mars terrain source catalog against live HTTP metadata.

This checker is deliberately stdlib-only and metadata-only. Raster probes use
``Range: bytes=0-1023`` and never download a DEM. It exits non-zero if a source
that was verified in ``docs/data-sources/d01-terrain.json`` regresses, if a
required catalog field is missing, or if the catalog itself is invalid.

Usage:
    python3 scripts/check_d01_sources.py
    python3 scripts/check_d01_sources.py --catalog docs/data-sources/d01-terrain.json
    python3 scripts/check_d01_sources.py --timeout 30

The catalog is the source of truth. Every ``http_evidence`` entry is replayed
with its recorded method and expectations. ``blocked_or_unverified`` and
``rejected_candidates`` entries are schema-checked but not probed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "docs" / "data-sources" / "d01-terrain.json"
USER_AGENT = "d01-source-audit/1.0 (metadata-only checker; +https://github.com/)"
MAX_BODY_BYTES = 262_144
MAX_RANGE_BYTES = 4_096

SOURCE_FIELDS = (
    "source_id",
    "body",
    "provider",
    "product",
    "endpoint",
    "access_method",
    "license",
    "license_status",
    "license_url",
    "attribution",
    "native_resolution_m",
    "crs",
    "coverage",
    "metrics",
    "status",
    "http_evidence",
    "limitations",
    "reproducible_command",
)

SERVICE_FIELDS = (
    "service_id",
    "body",
    "provider",
    "product",
    "endpoint",
    "access_method",
    "license",
    "license_status",
    "license_url",
    "attribution",
    "native_resolution_m",
    "crs",
    "coverage",
    "metrics",
    "status",
    "http_evidence",
    "limitations",
    "reproducible_command",
)

TOP_LEVEL_FIELDS = (
    "catalog_version",
    "audit_date",
    "scope",
    "non_claims",
    "sources",
    "metadata_services",
    "blocked_or_unverified",
    "rejected_candidates",
    "gap_summary",
    "checkpoint_plan",
    "checker",
)


class CheckFailure(Exception):
    """A single probe or schema check failed."""


def load_catalog(path: Path) -> dict:
    if not path.is_file():
        raise CheckFailure(f"catalog not found: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            catalog = json.load(handle)
    except json.JSONDecodeError as exc:
        raise CheckFailure(f"catalog is not valid JSON: {exc}") from exc
    if not isinstance(catalog, dict):
        raise CheckFailure("catalog root must be a JSON object")
    return catalog


def require_fields(entry: dict, fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if field not in entry]
    if missing:
        raise CheckFailure(f"{label}: missing required field(s): {', '.join(missing)}")


def validate_schema(catalog: dict) -> tuple[int, int]:
    require_fields(catalog, TOP_LEVEL_FIELDS, "catalog")
    if not isinstance(catalog["sources"], list) or not catalog["sources"]:
        raise CheckFailure("catalog.sources must be a non-empty list")
    if not isinstance(catalog["metadata_services"], list):
        raise CheckFailure("catalog.metadata_services must be a list")

    probe_count = 0
    for source in catalog["sources"]:
        require_fields(source, SOURCE_FIELDS, f"source {source.get('source_id', '<unknown>')}")
        status = source["status"]
        if status in {"verified", "verified_metadata_only"} and not source["http_evidence"]:
            raise CheckFailure(f"{source['source_id']}: {status} source has no http_evidence")
        for number, evidence in enumerate(source["http_evidence"], 1):
            validate_evidence(evidence, f"{source['source_id']} evidence {number}")
            probe_count += 1

    for service in catalog["metadata_services"]:
        require_fields(service, SERVICE_FIELDS, f"service {service.get('service_id', '<unknown>')}")
        for number, evidence in enumerate(service["http_evidence"], 1):
            validate_evidence(evidence, f"{service['service_id']} evidence {number}")
            probe_count += 1

    for entry in catalog["blocked_or_unverified"]:
        require_fields(
            entry,
            ("source_id", "body", "product", "endpoint", "status", "reason", "alternative"),
            f"blocked {entry.get('source_id', '<unknown>')}",
        )
    for entry in catalog["rejected_candidates"]:
        require_fields(
            entry,
            ("source_id", "body", "product", "endpoint", "status", "reason"),
            f"rejected {entry.get('source_id', '<unknown>')}",
        )
    for entry in catalog["checkpoint_plan"]:
        require_fields(
            entry,
            (
                "checkpoint_id",
                "objective",
                "sources",
                "max_windows",
                "network_cap_mb",
                "cpu_cap_min",
                "gate",
                "stop_rule",
                "output",
                "resumable_key",
            ),
            f"checkpoint {entry.get('checkpoint_id', '<unknown>')}",
        )
    return len(catalog["sources"]), probe_count


def validate_evidence(evidence: dict, label: str) -> None:
    require_fields(evidence, ("url", "request", "observed", "expect"), label)
    expect = evidence["expect"]
    if not isinstance(expect, dict):
        raise CheckFailure(f"{label}: expect must be an object")
    require_fields(
        expect, ("status", "content_type_prefix_any", "body_contains", "min_total_bytes"), label
    )
    if not isinstance(expect["status"], list) or not expect["status"]:
        raise CheckFailure(f"{label}: expect.status must be a non-empty list")
    if (
        not isinstance(expect["content_type_prefix_any"], list)
        or not expect["content_type_prefix_any"]
    ):
        raise CheckFailure(f"{label}: expect.content_type_prefix_any must be a non-empty list")


def fetch(url: str, request: str, timeout: float) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    }
    is_range = request.lower().startswith("get range")
    if is_range:
        headers["Range"] = "bytes=0-1023"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read(MAX_RANGE_BYTES if is_range else MAX_BODY_BYTES)
            return {
                "status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": response.headers.get("Content-Length"),
                "content_range": response.headers.get("Content-Range"),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "content_length": exc.headers.get("Content-Length"),
            "content_range": exc.headers.get("Content-Range"),
            "body": exc.read(MAX_RANGE_BYTES),
            "error": f"HTTP {exc.code}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "status": None,
            "content_type": "",
            "content_length": None,
            "content_range": None,
            "body": b"",
            "error": f"{type(exc).__name__}: {exc}",
        }


def total_bytes(result: dict) -> int | None:
    content_range = result.get("content_range") or ""
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    content_length = result.get("content_length")
    if content_length and content_length.isdigit():
        return int(content_length)
    body = result.get("body") or b""
    return len(body) or None


def check_probe(evidence: dict, timeout: float) -> tuple[bool, str, dict]:
    expect = evidence["expect"]
    result = fetch(evidence["url"], evidence["request"], timeout)
    if result["error"]:
        return False, result["error"], result
    if result["status"] not in expect["status"]:
        return False, f"status {result['status']} not in {expect['status']}", result

    content_type = result["content_type"].lower()
    prefixes = [prefix.lower() for prefix in expect["content_type_prefix_any"]]
    if not any(content_type.startswith(prefix) for prefix in prefixes):
        return False, f"content-type {result['content_type']!r} does not match {prefixes}", result

    minimum = expect.get("min_total_bytes")
    observed_total = total_bytes(result)
    if minimum is not None and (observed_total is None or observed_total < minimum):
        return False, f"total bytes {observed_total} < floor {minimum}", result

    token = expect.get("body_contains")
    if token is not None:
        text = result["body"].decode("utf-8", "replace")
        if token not in text:
            return False, f"body does not contain {token!r}", result

    expected_matched = expect.get("json_number_matched")
    if expected_matched is not None:
        try:
            payload = json.loads(result["body"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, f"body is not JSON: {exc}", result
        actual = payload.get("numberMatched") if isinstance(payload, dict) else None
        if actual != expected_matched:
            return False, f"numberMatched {actual!r} != {expected_matched}", result

    return True, "ok", result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="catalog JSON path")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="per-request timeout in seconds"
    )
    args = parser.parse_args(argv)

    print("D01 Moon/Mars terrain source checker")
    print(f"catalog: {args.catalog}")
    try:
        catalog = load_catalog(args.catalog)
        source_count, probe_count = validate_schema(catalog)
    except CheckFailure as exc:
        print(f"SCHEMA FAIL: {exc}")
        return 2

    print(
        f"sources: {source_count} | metadata_services: {len(catalog['metadata_services'])} | "
        f"blocked/unverified: {len(catalog['blocked_or_unverified'])} | "
        f"rejected: {len(catalog['rejected_candidates'])} | probes: {probe_count}"
    )
    print("")

    failures: list[tuple[str, str, str]] = []
    passed = 0
    for section in ("sources", "metadata_services"):
        for entry in catalog[section]:
            entry_id = entry.get("source_id") or entry.get("service_id")
            for evidence in entry["http_evidence"]:
                ok, reason, result = check_probe(evidence, args.timeout)
                total = total_bytes(result)
                if evidence["request"].lower().startswith("get range"):
                    size_text = (
                        f"object {total:,} B, read 1,024 B"
                        if total is not None
                        else "object size unknown"
                    )
                else:
                    size_text = f"body {total:,} B" if total is not None else "body size unknown"
                if ok:
                    passed += 1
                    content_type = result["content_type"] or "-"
                    print(
                        f"PASS  {entry_id:44s} {result['status']}  {content_type:42s} {size_text}"
                    )
                else:
                    failures.append((entry_id, reason, evidence["url"]))
                    print(f"FAIL  {entry_id:44s} {reason}")
                    print(f"      {evidence['url']}")

    print("")
    print(f"summary: {passed} passed, {len(failures)} failed, {probe_count} probes")
    if failures:
        for entry_id, reason, url in failures:
            print(f"  - {entry_id}: {reason} ({url})")
        return 1
    print("result: OK (all previously verified sources still respond as recorded)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Re-runnable, stdlib-only D02 source checker.

Reads ``docs/data-sources/d02-geology.json`` and re-runs the small metadata
requests that back the audit.  It never downloads a raster, archive or granule:
GET bodies are capped per entry and archives are checked with HEAD.

Exit codes:
  0  every checked source still matches the recorded contract
  1  at least one regression (HTTP status, content type, body assertion or length)
  2  usage / catalogue error

Examples:
  python3 scripts/check_d02_sources.py
  python3 scripts/check_d02_sources.py --list
  python3 scripts/check_d02_sources.py --source pds-omega-global-mineral-maps
  python3 scripts/check_d02_sources.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "docs" / "data-sources" / "d02-geology.json"
USER_AGENT = "planetary-analog-d02-audit/1.0 (source verification; contact repo maintainers)"
DEFAULT_TIMEOUT = 40.0


def _base_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _read_body(response, limit: int) -> tuple[str, int, bool]:
    raw = response.read(limit + 1)
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
    return raw.decode("utf-8", "replace"), len(raw), truncated


def check_evidence(entry: dict, *, timeout: float) -> dict:
    url = entry["url"]
    method = entry.get("method", "GET").upper()
    expect = entry.get("expect", {})
    max_bytes = int(entry.get("max_bytes") or 0)
    if method == "GET" and max_bytes <= 0:
        return {
            "label": entry.get("label", url),
            "url": url,
            "method": method,
            "ok": False,
            "problems": ["catalogue error: GET without positive max_bytes"],
        }

    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            declared = response.headers.get("Content-Length")
            final_url = response.geturl()
            if method == "GET":
                body, read, truncated = _read_body(response, max_bytes)
            else:
                body, read, truncated = "", 0, False
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "label": entry.get("label", url),
            "url": url,
            "method": method,
            "ok": False,
            "problems": [f"request failed: {exc}"],
        }

    problems: list[str] = []
    expected_status = expect.get("status", 200)
    if status != expected_status:
        problems.append(f"status {status} != {expected_status}")

    allowed = [str(item).lower() for item in expect.get("content_type_any_of") or []]
    if allowed and _base_content_type(content_type) not in allowed:
        problems.append(f"content type {content_type!r} not in {allowed!r}")

    for needle in expect.get("body_contains") or []:
        if needle not in body:
            problems.append(f"missing body text: {needle!r}")

    lowered = body.lower()
    for needle in expect.get("body_not_contains") or []:
        if needle.lower() in lowered:
            problems.append(f"unexpected body text: {needle!r}")

    content_length = int(declared) if declared and declared.isdigit() else None
    low = expect.get("content_length_min")
    high = expect.get("content_length_max")
    if low is not None and (content_length is None or content_length < int(low)):
        problems.append(f"content length {content_length} < {low}")
    if high is not None and (content_length is None or content_length > int(high)):
        problems.append(f"content length {content_length} > {high}")

    return {
        "label": entry.get("label", url),
        "url": url,
        "method": method,
        "ok": not problems,
        "status": status,
        "content_type": content_type,
        "content_length": content_length,
        "bytes_read": read,
        "truncated": truncated,
        "final_url": final_url,
        "problems": problems,
    }


def _detail(result: dict) -> str:
    content_type = result.get("content_type") or "-"
    if result.get("method") == "HEAD":
        length = result.get("content_length")
        detail = f"{result.get('status')} {content_type} "
        detail += f"{length} B" if length is not None else "length not declared"
    else:
        detail = f"{result.get('status')} {content_type} read={result.get('bytes_read')} B"
    if result.get("truncated"):
        detail += " (truncated)"
    return detail


def load_catalog(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            catalog = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read catalogue {path}: {exc}") from exc
    if not isinstance(catalog.get("sources"), list):
        raise SystemExit(f"catalogue {path} has no sources array")
    return catalog


def iter_sources(catalog: dict, selected: str | None):
    for source in sorted(catalog["sources"], key=lambda item: item["source_id"]):
        if selected and source["source_id"] != selected:
            continue
        yield source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--source", help="check one source_id only")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--list", action="store_true", help="list source ids without network")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = parser.parse_args(argv)

    catalog = load_catalog(args.catalog)
    sources = list(iter_sources(catalog, args.source))
    if args.source and not sources:
        print(f"unknown source_id: {args.source}", file=sys.stderr)
        return 2
    if args.list:
        for source in sources:
            print(f"{source['source_id']}\t{source['body']}\t{source['status']}")
        return 0

    try:
        catalog_label = str(args.catalog.resolve().relative_to(ROOT))
    except ValueError:
        catalog_label = str(args.catalog)
    if not args.json:
        print(f"D02 source audit: {catalog_label}")
        print(
            f"catalogue {catalog.get('catalog_version')} | audit {catalog.get('audit_date')} | "
            f"{len(sources)} source(s)"
        )

    summary = {
        "catalog": catalog_label,
        "sources_checked": 0,
        "checks": 0,
        "passed": 0,
        "failed": 0,
        "sources": [],
    }
    failed_sources = 0
    for index, source in enumerate(sources, start=1):
        source_result = {
            "source_id": source["source_id"],
            "body": source["body"],
            "status": source["status"],
            "checks": [],
        }
        problems_for_source: list[str] = []
        if not args.json:
            print(
                f"\n[{index}/{len(sources)}] {source['source_id']} "
                f"({source['body']}, {source['status']})"
            )
        for entry in source.get("evidence", []):
            result = check_evidence(entry, timeout=args.timeout)
            source_result["checks"].append(result)
            summary["checks"] += 1
            if result["ok"]:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
                problems_for_source.extend(result["problems"])
            if not args.json:
                state = "PASS" if result["ok"] else "FAIL"
                print(f"  {state} {result['label']}: {result['method']} {_detail(result)}")
                for problem in result["problems"]:
                    print(f"       - {problem}")
                print(f"       {result['url']}")

        summary["sources_checked"] += 1
        summary["sources"].append(source_result)
        if problems_for_source:
            failed_sources += 1

    license_checks = []
    for entry in catalog.get("license_evidence", []):
        if args.source:
            break
        result = check_evidence(entry, timeout=args.timeout)
        license_checks.append(result)
        summary["checks"] += 1
        if result["ok"]:
            summary["passed"] += 1
        else:
            summary["failed"] += 1
            failed_sources += 1
        if not args.json:
            state = "PASS" if result["ok"] else "FAIL"
            print(f"  {state} [licence] {result['label']}: {result['method']} {_detail(result)}")
            for problem in result["problems"]:
                print(f"       - {problem}")
            print(f"       {result['url']}")
    if license_checks:
        summary["license_evidence"] = license_checks

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"\n{summary['sources_checked']} source(s), {summary['checks']} check(s): "
            f"{summary['passed']} passed, {summary['failed']} failed"
        )
        if failed_sources:
            print(f"RESULT: FAIL ({failed_sources} source(s) regressed)")
        else:
            print("RESULT: PASS")
    return 1 if failed_sources else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Rerun the D04/D06 public-source audit with small metadata/WCS requests.

The catalog lives at ``docs/data-sources/d04-d06-water-regolith.json``. This
checker sends only the requests recorded there, compares the observed HTTP
status, content type, body size, byte ranges and marker strings, and exits
non-zero on any regression.

Safety: ranged requests read at most the requested bytes plus one, so a server
that ignores ``Range`` cannot make this script download the 4.25 GB Mini-RF
image or the 14.3 MB Lunar Prospector table. WCS checks use a two-by-two cell
subset (about 600 bytes), never a full coverage.

Usage:
    python3 scripts/check_d04_d06_sources.py
    python3 scripts/check_d04_d06_sources.py --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "docs" / "data-sources" / "d04-d06-water-regolith.json"
USER_AGENT = "analog-d04-d06-audit/1.0"
DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_READ = 262_144
DEFAULT_JOBS = 3
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_S = 1.5
ISRIC_HOST = "maps.isric.org"
_ISRIC_LOCK = threading.Lock()


class CatalogError(ValueError):
    """The catalog is missing required structure."""


class FetchError(RuntimeError):
    """A request could not be completed."""


class FetchResult:
    def __init__(
        self,
        *,
        status: int | None,
        content_type: str,
        body: bytes,
        content_range: str | None,
        final_url: str,
        truncated: bool,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body
        self.content_range = content_range
        self.final_url = final_url
        self.truncated = truncated
        self.error = error


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise CatalogError(f"{path} must contain a 'sources' array")
    return data


def _range_limit(check: dict[str, Any]) -> int | None:
    headers = (check.get("request") or {}).get("headers") or {}
    value = headers.get("Range")
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", value.strip())
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end < start:
        return None
    return end - start + 1


def _cache_key(check: dict[str, Any]) -> tuple[str, str]:
    request = check.get("request") or {}
    headers = request.get("headers") or {}
    return str(request.get("url", "")), str(headers.get("Range", ""))


def fetch(
    check: dict[str, Any],
    *,
    timeout: float,
    retries: int,
    backoff: float,
    cache: dict[tuple[str, str], FetchResult],
) -> FetchResult:
    key = _cache_key(check)
    if key in cache:
        return cache[key]

    request_cfg = check.get("request") or {}
    url = request_cfg.get("url")
    if not isinstance(url, str) or not url:
        raise FetchError("catalog check has no request.url")
    headers = {"User-Agent": str(request_cfg.get("user_agent") or USER_AGENT)}
    headers.update({str(k): str(v) for k, v in (request_cfg.get("headers") or {}).items()})

    range_limit = _range_limit(check)
    configured_limit = check.get("max_read")
    if range_limit is not None:
        limit = range_limit + 1
    elif isinstance(configured_limit, int) and configured_limit > 0:
        limit = configured_limit
    else:
        limit = DEFAULT_MAX_READ

    request = urllib.request.Request(url, headers=headers, method="GET")
    effective_timeout = float(check.get("timeout_s", timeout))
    attempts = max(1, retries + 1)
    is_isric = ISRIC_HOST in url
    for attempt in range(attempts):
        try:
            if is_isric:
                # The SoilGrids WCS service is the slowest endpoint in this audit. Match the
                # pilot's conservative access policy and serialize requests to it.
                with _ISRIC_LOCK:
                    time.sleep(0.25)
                    with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                        raw = response.read(limit + 1)
                        result = FetchResult(
                            status=response.status,
                            content_type=response.headers.get("Content-Type", ""),
                            body=raw[:limit],
                            content_range=response.headers.get("Content-Range"),
                            final_url=response.geturl(),
                            truncated=len(raw) > limit,
                        )
            else:
                with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                    raw = response.read(limit + 1)
                    result = FetchResult(
                        status=response.status,
                        content_type=response.headers.get("Content-Type", ""),
                        body=raw[:limit],
                        content_range=response.headers.get("Content-Range"),
                        final_url=response.geturl(),
                        truncated=len(raw) > limit,
                    )
            cache[key] = result
            return result
        except urllib.error.HTTPError as exc:
            body = exc.read(limit + 1) if exc.fp else b""
            result = FetchResult(
                status=exc.code,
                content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
                body=body[:limit],
                content_range=exc.headers.get("Content-Range") if exc.headers else None,
                final_url=exc.geturl() if hasattr(exc, "geturl") else url,
                truncated=len(body) > limit,
                error=f"HTTPError: {exc.reason}",
            )
            if 400 <= exc.code < 500:
                cache[key] = result
                return result
            if attempt == attempts - 1:
                cache[key] = result
                return result
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts - 1:
                result = FetchResult(
                    status=None,
                    content_type="",
                    body=b"",
                    content_range=None,
                    final_url=url,
                    truncated=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return result
        time.sleep(backoff * (attempt + 1))

    return FetchResult(
        status=None,
        content_type="",
        body=b"",
        content_range=None,
        final_url=url,
        truncated=False,
        error="request failed after retries",
    )


def _content_type_matches(expected: str, actual: str) -> bool:
    expected_base = expected.split(";")[0].strip().lower()
    actual_base = actual.split(";")[0].strip().lower()
    if not expected_base:
        return True
    if expected_base == "application/octet":
        return actual_base in {"application/octet", "application/octet-stream"}
    return actual_base == expected_base


def _content_range_total(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"/(\d+)\s*$", value)
    return int(match.group(1)) if match else None


def check_one(
    name: str,
    check: dict[str, Any],
    *,
    timeout: float,
    retries: int,
    backoff: float,
    cache: dict[tuple[str, str], FetchResult],
) -> tuple[str, list[str], FetchResult]:
    problems: list[str] = []
    try:
        result = fetch(check, timeout=timeout, retries=retries, backoff=backoff, cache=cache)
    except FetchError as exc:
        return (
            name,
            [str(exc)],
            FetchResult(
                status=None,
                content_type="",
                body=b"",
                content_range=None,
                final_url="",
                truncated=False,
                error=str(exc),
            ),
        )

    if result.error:
        problems.append(result.error)
    if result.truncated:
        problems.append("response exceeded the bounded read limit; refusing to download more")

    expected_status = check.get("http_status")
    if expected_status is not None and result.status != expected_status:
        problems.append(f"status {result.status!r} != expected {expected_status!r}")

    expected_type = check.get("content_type")
    if isinstance(expected_type, str) and not _content_type_matches(
        expected_type, result.content_type
    ):
        problems.append(f"content-type {result.content_type!r} != expected {expected_type!r}")

    text = result.body.decode("utf-8", "replace")
    for needle in check.get("must_contain") or []:
        if str(needle) not in text:
            problems.append(f"missing marker {needle!r}")
    for needle in check.get("must_not_contain") or []:
        if str(needle) in text:
            problems.append(f"found forbidden marker {needle!r}")

    body_size = len(result.body)
    minimum = check.get("bytes_min")
    maximum = check.get("bytes_max")
    if isinstance(minimum, int) and body_size < minimum:
        problems.append(f"body {body_size}B < minimum {minimum}B")
    if isinstance(maximum, int) and body_size > maximum:
        problems.append(f"body {body_size}B > maximum {maximum}B")

    expected_total = check.get("content_range_total")
    if isinstance(expected_total, int):
        actual_total = _content_range_total(result.content_range)
        if actual_total != expected_total:
            problems.append(f"content-range total {actual_total!r} != expected {expected_total!r}")

    expected_hex = check.get("must_start_with_hex")
    if (
        isinstance(expected_hex, str)
        and expected_hex
        and not result.body.hex().startswith(expected_hex.lower())
    ):
        problems.append(f"body does not start with hex {expected_hex!r}")

    return name, problems, result


def iter_catalog_checks(
    catalog: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    checks: list[tuple[str, dict[str, Any]]] = []
    skipped: list[tuple[str, dict[str, Any]]] = []
    for source in catalog["sources"]:
        if not isinstance(source, dict):
            raise CatalogError("each source must be an object")
        source_id = str(source.get("source_id") or "unknown")
        verification = source.get("verification")
        if isinstance(verification, dict):
            (checks if verification.get("check", True) else skipped).append(
                (f"{source_id}:primary", verification)
            )
        for index, extra in enumerate(source.get("additional_checks") or []):
            if isinstance(extra, dict):
                (checks if extra.get("check", True) else skipped).append(
                    (f"{source_id}:extra{index}", extra)
                )
        license_cfg = source.get("license")
        if isinstance(license_cfg, dict):
            license_verification = license_cfg.get("verification")
            if isinstance(license_verification, dict):
                (checks if license_verification.get("check", True) else skipped).append(
                    (f"{source_id}:license", license_verification)
                )
    return checks, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF_S)
    parser.add_argument("--verbose", action="store_true", help="print every request URL")
    args = parser.parse_args(argv)

    try:
        catalog = load_catalog(args.catalog)
        checks, skipped_checks = iter_catalog_checks(catalog)
    except CatalogError as exc:
        print(f"CATALOG ERROR: {exc}", file=sys.stderr)
        return 2

    if not checks:
        print("CATALOG ERROR: no checks found", file=sys.stderr)
        return 2

    print(
        f"Catalog: {args.catalog.relative_to(ROOT) if args.catalog.is_relative_to(ROOT) else args.catalog}"
    )
    print(f"Checks: {len(checks)} | timeout: {args.timeout:g}s | jobs: {args.jobs}")
    print("-" * 100)

    cache: dict[tuple[str, str], FetchResult] = {}
    results: dict[str, tuple[list[str], FetchResult]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
        futures = {
            executor.submit(
                check_one,
                name,
                check,
                timeout=args.timeout,
                retries=args.retries,
                backoff=args.backoff,
                cache=cache,
            ): name
            for name, check in checks
        }
        for future in futures:
            name = futures[future]
            try:
                resolved_name, problems, result = future.result()
            except Exception as exc:  # pragma: no cover - defensive boundary
                resolved_name, problems, result = (
                    name,
                    [f"{type(exc).__name__}: {exc}"],
                    FetchResult(
                        status=None,
                        content_type="",
                        body=b"",
                        content_range=None,
                        final_url="",
                        truncated=False,
                        error=str(exc),
                    ),
                )
            results[resolved_name] = (problems, result)

    failed = 0
    passed = 0
    for name, _check in checks:
        problems, result = results[name]
        status = result.status if result.status is not None else "ERR"
        size = len(result.body)
        if problems:
            failed += 1
            print(f"FAIL  {name:<52} status={status} type={result.content_type!r} bytes={size}")
            for problem in problems:
                print(f"      - {problem}")
            if result.final_url:
                print(f"      url: {result.final_url}")
        else:
            passed += 1
            print(f"PASS  {name:<52} status={status} type={result.content_type!r} bytes={size}")
        if args.verbose:
            request = _check.get("request") or {}
            print(f"      {request.get('url', '')}")

    for name, check in skipped_checks:
        note = str(check.get("note") or "catalog check=false")
        print(f"SKIP  {name:<52} {note}")

    print("-" * 100)
    print(f"SUMMARY pass={passed} fail={failed} skipped={len(skipped_checks)}")
    if skipped_checks:
        print(
            "Skipped checks are recorded as unavailable/optional in the catalog and are not regressions."
        )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

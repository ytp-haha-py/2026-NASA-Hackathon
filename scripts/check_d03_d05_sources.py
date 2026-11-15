#!/usr/bin/env python3
"""Re-runnable, bounded HTTP checker for D03/D05 open-data sources.

The script intentionally uses only the Python standard library.  Every probe is
either a small metadata/document request or an HTTP byte-range request whose
response is capped.  No raster, table or kernel is downloaded in full.

Exit codes:
    0  all probes match their recorded status/content-type/range/markers
    1  at least one source regressed or a probe could not be completed
    2  invalid command-line usage
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

USER_AGENT = (
    "PlanetaryAnalogExplorer-source-audit/1.0 "
    "(D03-D05 metadata checker; +https://github.com/2026-NASA-Hackathon)"
)
DEFAULT_TIMEOUT_SECONDS = 45
MAX_FULL_BYTES = 262_144  # 256 KiB: directory listings, labels, JSON, XML only
MAX_RANGE_BYTES = 4_096  # byte-range probes read at most 4 KiB
_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+|\*)")


@dataclass(frozen=True)
class Probe:
    """One deterministic HTTP evidence check."""

    probe_id: str
    url: str
    dimension: str
    purpose: str
    mode: str = "full"  # "full" or "range"
    byte_range: tuple[int, int] | None = None
    expected_status: int = 200
    expected_content_types: tuple[str, ...] = ("text/html",)
    expected_total_bytes: int | None = None
    required_markers: tuple[str, ...] = ()

    @property
    def range_header(self) -> str | None:
        if self.mode != "range" or self.byte_range is None:
            return None
        start, end = self.byte_range
        return f"bytes={start}-{end}"


PROBES: tuple[Probe, ...] = (
    Probe(
        probe_id="pds-diviner-pcp-collection",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/"
        ),
        dimension="D03",
        purpose="Diviner PCP collection index is reachable",
        expected_content_types=("text/html",),
        required_markers=("data_derived_pcp", "collection_data_derived_pcp.xml"),
    ),
    Probe(
        probe_id="pds-diviner-pcp-ltim01-label",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/"
            "pcp_avg_tbol_pols_sum_ltim01_240.lbl"
        ),
        dimension="D03",
        purpose="PCP 00:00-00:15 product label: 240 m, 2009-2019, 59-byte rows",
        expected_content_types=("text/plain",),
        required_markers=(
            "PCP_AVG_TBOL_POLS_SUM_LTIM01_240.TAB",
            "MAP_SCALE",
            "240 <m/pix>",
            "START_TIME",
            "STOP_TIME",
            "Empty bins are omitted",
        ),
    ),
    Probe(
        probe_id="pds-diviner-pcp-ltim01-data",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/"
            "pcp_avg_tbol_pols_sum_ltim01_240.tab"
        ),
        dimension="D03",
        purpose="PCP 00:00-00:15 table: header plus first native row",
        mode="range",
        byte_range=(0, 117),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=212_653_759,
        required_markers=("clat", "tbol"),
    ),
    Probe(
        probe_id="pds-diviner-pcp-ltim49-data",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/"
            "pcp_avg_tbol_pols_sum_ltim49_240.tab"
        ),
        dimension="D03",
        purpose="PCP 12:00-12:15 table: header plus first native row",
        mode="range",
        byte_range=(0, 117),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=197_367_449,
        required_markers=("clat", "tbol"),
    ),
    Probe(
        probe_id="pds-diviner-gcp-90s80s-label",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/"
            "global_cumul_avg_cyl_90s80s_002.lbl"
        ),
        dimension="D03",
        purpose="Diviner GCP south-cap label: 0.5 degree bins, 2009-2015",
        expected_content_types=("text/plain",),
        required_markers=(
            "GLOBAL_CUMUL_AVG_CYL_90S80S_002.TAB",
            "0.5 degree latitude",
            "STOP_TIME",
            "-9999",
        ),
    ),
    Probe(
        probe_id="pds-diviner-gcp-90s80s-data",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/"
            "global_cumul_avg_cyl_90s80s_002.tab"
        ),
        dimension="D03",
        purpose="Diviner GCP south-cap table: header plus first 0.5 degree row",
        mode="range",
        byte_range=(0, 225),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=156_211_313,
        required_markers=("clat", "tbol"),
    ),
    Probe(
        probe_id="pds-diviner-gdr-l3-st-label",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_gdr_l3/cylindrical/img/"
            "dgdr_st_avg_cyl_128_img.lbl"
        ),
        dimension="D03",
        purpose="Diviner GDR L3 regolith-temperature label: 128 ppd, 80N-80S",
        expected_content_types=("text/plain",),
        required_markers=(
            "DGDR_ST_AVG_CYL_128_IMG.IMG",
            "MAP_SCALE",
            "236.901175",
            "MAXIMUM_LATITUDE",
            "REGOLITH TEMPERATURE",
        ),
    ),
    Probe(
        probe_id="pds-diviner-ghrm-tbol-xml",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_ghrm/geotiff/"
            "dghrm_tbol_m_70s70n_tif.xml"
        ),
        dimension="D03",
        purpose="Diviner GHRM midnight bolometric mosaic: 237 m, 70N-70S",
        expected_content_types=("application/xml",),
        required_markers=(
            "global high-resolution mosaic of bolometric temperature at midnight",
            "236.901175188667821",
            "start_date_time",
        ),
    ),
    Probe(
        probe_id="pds-diviner-rcp-lacus-mortis-xml",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_rcp/"
            "region_lacus_mortis_cumul_avg_cyl_42n023e_128.xml"
        ),
        dimension="D03",
        purpose="Diviner RCP Lacus Mortis metadata: 128 ppd, non-polar region",
        expected_content_types=("application/xml",),
        required_markers=(
            "Regional Cumulative Product for Lacus Mortis",
            "128 pixels per degree",
            "start_date_time",
        ),
    ),
    Probe(
        probe_id="pds-diviner-prp-south-label",
        url=(
            "https://pds-geosciences.wustl.edu/lro/"
            "urn-nasa-pds-lro_diviner_derived1/data_derived_prp/dlre_prp_south.lbl"
        ),
        dimension="D03",
        purpose="Diviner PRP south label: model-derived annual temperature/ice",
        expected_content_types=("text/plain",),
        required_markers=(
            "LRO-L-DLRE-5-PRP-V2.0",
            "DLRE_PRP_SOUTH.TAB",
            "START_TIME",
        ),
    ),
    Probe(
        probe_id="usgs-themis-ti-product-page",
        url=(
            "https://astrogeology.usgs.gov/search/map/"
            "themis_thermal_inertia_mosaic_quantitative_00n060e_100mpp"
        ),
        dimension="D03",
        purpose="USGS THEMIS quantitative TI product page and public-domain notice",
        expected_content_types=("text/html",),
        required_markers=(
            "THEMIS Thermal Inertia Mosaic Quantitative 00N060E 100mpp",
            "Public domain",
            "100 m per pixel",
        ),
    ),
    Probe(
        probe_id="usgs-themis-ti-cub",
        url=(
            "https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/"
            "THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/"
            "THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub"
        ),
        dimension="D03",
        purpose="THEMIS quantitative ISIS cube header via a 1 KiB range request",
        mode="range",
        byte_range=(0, 1023),
        expected_status=206,
        expected_content_types=("binary/octet-stream",),
        expected_total_bytes=2_532_510_192,
    ),
    Probe(
        probe_id="pds-tes-timap-label",
        url=(
            "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/"
            "mgst_9001/data/global_ti_day_2007.lbl"
        ),
        dimension="D03",
        purpose="Mars TES global TI label: 2.96 km/pixel, 16-bit, 5-5000 TIU",
        expected_content_types=("text/plain",),
        required_markers=(
            "MGS-M-TES-5-TIMAP-V1.0",
            "2.96 <km/pix>",
            "SAMPLE_BITS",
            "MSB_INTEGER",
        ),
    ),
    Probe(
        probe_id="naif-lsk",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls",
        dimension="D05",
        purpose="Pinned leap-second kernel is reachable and unchanged in size",
        mode="range",
        byte_range=(0, 511),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=5_257,
        required_markers=("LEAPSECONDS KERNEL FILE",),
    ),
    Probe(
        probe_id="naif-pck",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
        dimension="D05",
        purpose="Pinned planetary-constants kernel: body radii and IAU_MARS",
        mode="range",
        byte_range=(0, 511),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=131_226,
        required_markers=("KPL/PCK",),
    ),
    Probe(
        probe_id="naif-moon-pa",
        url=("https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de421_1900-2050.bpc"),
        dimension="D05",
        purpose="Pinned DE421 lunar principal-axis binary PCK",
        mode="range",
        byte_range=(0, 511),
        expected_status=206,
        expected_content_types=("",),
        expected_total_bytes=1_770_496,
        required_markers=("DAF/PCK",),
    ),
    Probe(
        probe_id="naif-moon-fk",
        url=("https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_080317.tf"),
        dimension="D05",
        purpose="Pinned lunar frame kernel defining MOON_ME / MOON_PA",
        mode="range",
        byte_range=(0, 511),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=21_437,
        required_markers=("SPICE Lunar Reference Frame Specification Kernel",),
    ),
    Probe(
        probe_id="naif-de421",
        url=(
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/"
            "a_old_versions/de421.bsp"
        ),
        dimension="D05",
        purpose="Pinned DE421 SPK ephemeris (1900-2050) via a 512-byte range",
        mode="range",
        byte_range=(0, 511),
        expected_status=206,
        expected_content_types=("model/vnd.valve.source.compiled-map", ""),
        expected_total_bytes=16_790_528,
        required_markers=("DAF/SPK",),
    ),
    Probe(
        probe_id="nasa-power-daily-point",
        url=(
            "https://power.larc.nasa.gov/api/temporal/daily/point"
            "?parameters=ALLSKY_SFC_SW_DWN,ALLSKY_SFC_LW_DWN,T2M"
            "&community=RE&longitude=-68.4&latitude=-23.5"
            "&start=20240101&end=20240102&format=JSON"
        ),
        dimension="D03/D05",
        purpose="NASA POWER daily point API returns the two irradiance fields",
        expected_content_types=("application/json",),
        required_markers=(
            "ALLSKY_SFC_SW_DWN",
            "ALLSKY_SFC_LW_DWN",
            "MERRA2",
            "kW-hr/m^2/day",
        ),
    ),
    Probe(
        probe_id="lasp-tsis-tsi-24h",
        url="https://lasp.colorado.edu/data/tsis/tsi_data/tsis_tsi_L3_c24h_latest.txt",
        dimension="D05",
        purpose="TSIS-1 TIM total solar irradiance at 1 AU (Earth-Sun only)",
        mode="range",
        byte_range=(0, 2047),
        expected_status=206,
        expected_content_types=("text/plain",),
        expected_total_bytes=None,
        required_markers=(
            "Total Solar Irradiance",
            "tsi_1au",
            "cadence: 24 hours",
        ),
    ),
    Probe(
        probe_id="pds-lro-crater-catalog",
        url=(
            "https://pds-ppi.igpp.ucla.edu/data/"
            "LRO-L-CRAT-3-CDR-CALIBRATED-V1.0/CATALOG/LRO_CRAT_L1_DS.CAT"
        ),
        dimension="D05",
        purpose="LRO CRaTER calibrated energy dataset description (not a dose product)",
        expected_content_types=("application/vnd.ms-pki.seccat", "text/plain"),
        required_markers=(
            "LRO-L-CRAT-3-CDR-CALIBRATED-V1.0",
            "CALIBRATED ENERGY DATA",
            "START_TIME",
        ),
    ),
    Probe(
        probe_id="pds-lro-lend-rdr-catalog",
        url=(
            "https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/"
            "lrolen_0xxx/catalog/rdr_ds.cat"
        ),
        dimension="D04",
        purpose="LRO LEND neutron RDR description; checked to rule out D03/D05 use",
        expected_content_types=("text/plain",),
        required_markers=(
            "LRO-L-LEND-4/5-RDR-V1.0",
            "NEUTRON",
            "neutron",
        ),
    ),
    Probe(
        probe_id="pds-ode-product-query",
        url=(
            "https://oderest.rsl.wustl.edu/live2/"
            "?query=product&ihid=LRO&iid=DLRE&pt=RDR&results=json&limit=2"
        ),
        dimension="D03",
        purpose="PDS Geosciences ODE REST product query accepts LRO/DLRE and returns IDs",
        expected_content_types=("text/xml",),
        required_markers=("Status>Success", "ihid>LRO", "ode_id"),
    ),
    Probe(
        probe_id="pds-geosciences-node",
        url="https://pds-geosciences.wustl.edu/",
        dimension="D03/D05",
        purpose="PDS Geosciences Node root remains the archive entry point",
        expected_content_types=("text/html",),
        required_markers=("PDS Geosciences Node",),
    ),
)


@dataclass
class Result:
    probe: Probe
    ok: bool
    status: int | None
    content_type: str
    bytes_read: int
    total_bytes: int | None
    detail: str


def _content_type(headers: dict[str, str]) -> str:
    return headers.get("content-type", "").split(";")[0].strip().lower()


def _total_from_headers(headers: dict[str, str]) -> int | None:
    content_range = headers.get("content-range", "")
    match = _CONTENT_RANGE_RE.search(content_range)
    if match and match.group(1) != "*":
        return int(match.group(1))
    content_length = headers.get("content-length")
    if content_length and content_length.isdigit():
        return int(content_length)
    return None


def _check_one(probe: Probe, timeout: float) -> Result:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    range_header = probe.range_header
    if range_header:
        headers["Range"] = range_header

    request = urllib.request.Request(probe.url, headers=headers, method="GET")
    status: int | None = None
    response_headers: dict[str, str] = {}
    body = b""
    detail = ""

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            limit = MAX_RANGE_BYTES if probe.mode == "range" else MAX_FULL_BYTES
            body = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        body = error.read(1024)
        detail = f"HTTPError: {error.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return Result(
            probe=probe,
            ok=False,
            status=None,
            content_type="",
            bytes_read=0,
            total_bytes=None,
            detail=f"transport error: {error}",
        )

    content_type = _content_type(response_headers)
    total_bytes = _total_from_headers(response_headers)
    failures: list[str] = []

    if status != probe.expected_status:
        failures.append(f"status {status} != {probe.expected_status}")

    if content_type not in probe.expected_content_types:
        failures.append(
            f"content-type {content_type or '<absent>'!r} not in {probe.expected_content_types!r}"
        )

    if probe.mode == "range":
        if status == 200:
            content_length = response_headers.get("content-length", "")
            if not content_length.isdigit() or int(content_length) > MAX_RANGE_BYTES:
                failures.append("server ignored Range; aborted before a large download")
        if probe.expected_total_bytes is not None and total_bytes != probe.expected_total_bytes:
            failures.append(f"total bytes {total_bytes!r} != expected {probe.expected_total_bytes}")
    else:
        if len(body) > MAX_FULL_BYTES:
            failures.append(f"body exceeded {MAX_FULL_BYTES} bytes; read was capped")

    text = body.decode("utf-8", errors="replace")
    for marker in probe.required_markers:
        if marker not in text:
            failures.append(f"missing marker {marker!r}")

    return Result(
        probe=probe,
        ok=not failures,
        status=status,
        content_type=content_type,
        bytes_read=len(body),
        total_bytes=total_bytes,
        detail="; ".join(failures) if failures else detail,
    )


def _print_result(result: Result) -> None:
    probe = result.probe
    label = "PASS" if result.ok else "FAIL"
    range_note = f" range={probe.range_header}" if probe.range_header else ""
    total_note = f" total={result.total_bytes}" if result.total_bytes is not None else ""
    print(
        f"{label}  {probe.probe_id}  "
        f"status={result.status} type={result.content_type or '<absent>'} "
        f"read={result.bytes_read}{total_note}{range_note}"
    )
    print(f"      GET {probe.url}")
    if result.detail:
        print(f"      {result.detail}")


def _json_payload(results: Iterable[Result]) -> dict[str, object]:
    rows = []
    for result in results:
        rows.append(
            {
                "probe_id": result.probe.probe_id,
                "dimension": result.probe.dimension,
                "purpose": result.probe.purpose,
                "url": result.probe.url,
                "ok": result.ok,
                "status": result.status,
                "content_type": result.content_type,
                "bytes_read": result.bytes_read,
                "total_bytes": result.total_bytes,
                "detail": result.detail,
            }
        )
    failed = sum(1 for row in rows if not row["ok"])
    return {"ok": failed == 0, "passed": len(rows) - failed, "failed": failed, "results": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON document instead of the line-oriented report",
    )
    args = parser.parse_args(argv)

    results = [_check_one(probe, args.timeout) for probe in PROBES]

    if args.json:
        print(json.dumps(_json_payload(results), indent=2, sort_keys=True))
    else:
        print("D03/D05 open-data source checker")
        print("bounded requests only: full bodies <=256 KiB; ranges <=4 KiB")
        print()
        for result in results:
            _print_result(result)
        passed = sum(1 for result in results if result.ok)
        failed = len(results) - passed
        print()
        print(f"summary: {passed} passed, {failed} failed, {len(results)} probes")
        print("scope: metadata/header evidence only; no raster or kernel was downloaded in full")

    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())

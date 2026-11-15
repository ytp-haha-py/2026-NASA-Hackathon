# D02 area-fraction / evidence pilot

Bounded, evidence-only area fractions for geology map units already verified in
`pipelines/geology/records/`. This directory does **not** add a geology
similarity, a ranking or a combined six-dimension score.

## Contents

| File | Purpose |
|---|---|
| `registry.json` | Versioned, non-exclusive legend registry mapping raw USGS codes (per dataset) to Igneous / Impact / Sedimentary / Aqueous-alteration process labels. |
| `evidence.json` | Full evidence document: per-unit records, per-AOI summaries, coverage states and limitations. |
| `evidence.jsonl` | One JSON record per line for portable ingestion. |
| `manifest.json` | Export version, source-record checksum, record counts and SHA-256 of `evidence.json`/`evidence.jsonl`. |
| `earth-glim-license.json` | GLiM licence verification findings, `license_unverified` decision and a legally usable alternative. |
| `SOURCE_VERIFICATION.md` | Source-by-source verification report and measured pilot results. |

The machine-readable registry is authoritative; this README is not a second copy
of the mappings.

## Rules encoded in the registry and exporter

- Raw codes are **dataset-scoped**. `Ec` on the Moon and `cr` on Mars are not the
  same unit, process or material, and a code is never looked up across bodies.
- A code that has no reviewed mapping resolves to `unmapped_code`; it is never
  guessed from a similar code.
- Process labels are **non-exclusive**: a unit may have zero, one or several
  labels, and absence of a label is not evidence of absence.
- `aqueous_alteration` requires a positive, documented alteration statement
  (aqueous/hydrated/clay/sulfate). Iron oxides, hematite or spectral indices
  alone are rejected by validation. Carbonate sedimentary rock does not prove
  aqueous alteration.
- Missing coverage is `no_coverage` or `unknown` with **null** fraction and
  area. It is never written as `0` or `false`.
- Categorical rasters are resampled **nearest-neighbour only**. A target pixel
  outside the source extent requires an explicit nodata value instead of a
  fabricated class.
- Earth GLiM polygon values are not exported while redistribution rights are
  unverified; only availability status is recorded.

## Reproduce offline (no GIS extras, no network)

```bash
# dry run: validate and print summaries without writing
uv run --frozen python -m analog_pipelines.geology.area_fraction --report-only

# write evidence.json / evidence.jsonl / manifest.json
uv run --frozen python -m analog_pipelines.geology.area_fraction
```

The exporter is deterministic: it uses the committed record's `generated_at`
and SHA-256, not wall-clock time, so re-running does not change the evidence.

Focused tests:

```bash
uv run --frozen pytest pipelines/tests/test_geology_area_fraction.py -o addopts='' -q
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt \
  pytest pipelines/tests/test_geology_area_fraction_spatial.py -o addopts='' -q
```

The optional GIS test module exercises live polygon overlay in native CRS with
Shapely/Fiona; the core module needs only PyProj and NumPy.

## Live overlay path

`analog_pipelines.geology.spatial.VectorIndex.measure()` returns per-unit
fraction **and** area, the equal-area analysis CRS, the AOI area, excluded or
explicitly repaired invalid-geometry counts, and the repair mode. Providers store
the measured fraction in `Lithology.overlap_fraction`; this exporter converts
that to portable area/evidence records. Repair is opt-in
(`repair_invalid=True` / `repair_aoi=True`) and always counted; the default is to
exclude invalid source polygons and record the count.

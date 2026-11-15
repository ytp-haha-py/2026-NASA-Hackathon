# D06 Moon/Mars regolith AOI release (Goal F, #170)

Bounded, offline, typed-v2 evidence for D06 regolith properties at one Moon
measurement site (Apollo 15 Station 8 LSRP trench) and one Mars measurement site
(InSight HP3/mole site, Elysium Planitia).

- `aois.json` — bounded measurement-site AOIs in planetary frames.
- `sources.json` — dataset/asset provenance: version, DOI/LID, licence, footprint,
  depth support, QA, URL and SHA-256.
- `source_values.json` — curated values and explicit no-coverage records with
  evidence class, depth, uncertainty, assumptions and source locator.
- `audit.json` — chemistry, thermal-inertia and thickness audits; cross-checks;
  forbidden derivations not performed.
- `observations.jsonl` — typed-v2 D06 observations (authoritative).
- `aoi_results.json` — per-AOI × feature view of the same records.
- `plan.json`, `assets.json`, `requests.jsonl`, `qa.json`, `manifest.json`,
  `REPORT.md` — portable release metadata and checksums.

The builder is offline and deterministic:

```bash
python3 pipelines/d05_d06_aoi/regolith/build_release.py
python3 pipelines/d05_d06_aoi/regolith/build_release.py --check
uv run pytest pipelines/tests/test_d06_aoi_170.py -q
```

No raw PDFs, rasters, caches or credentials are committed. This is not a global
or exhaustive survey. Missing values are explicit `no_coverage`; no value is
inferred from DEM roughness, land cover, thermal inertia or Earth SoilGrids.

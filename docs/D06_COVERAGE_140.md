# D06 Regolith/Soil Coverage (Issue #140)

**Branch:** `phase2/140-d06`  
**Scope:** D06 dimension only (data/provider/ingest/manifests/tests/docs and coverage matrix). No shared API schemas, OpenAPI/contracts, scoring engine, or other-dimension files modified.

## 1) Coverage Matrix (machine-readable)

- **Artifact:** [`docs/D06_COVERAGE_MATRIX.json`](D06_COVERAGE_MATRIX.json)
- **Body x feature:** Earth, Moon, Mars × D06 feature families requested/registered (SoilGrids texture/bulk-density model properties; regolith physical properties: particle size distribution, bulk density, porosity, cohesion, friction, bearing strength; thickness noted as unsupported by current feature registry).
- **Fields per entry:** source, version, licence, product, actual indexed/queryable coverage, measured/modeled/derived/evidence/no_coverage/unsupported state, time/depth/spatial support, CRS, QA, uncertainty, legal score/filter roles, gaps, rebuild instructions.
- **Rule:** Not a global or exhaustive survey. Missing stays missing; `no_coverage`/`unsupported` explicit. No fabricated values, no zero-fill, no DEM/roughness/WorldCover inference, no Earth-to-Moon/Mars transfer.

## 2) Earth SoilGrids (validated, kept)

- **113/113 committed Earth fine-candidate 0–5 cm means kept.** Unique Earth D06 area IDs across committed pilots = 113.
- **Committed releases (portable, checksummed):**
  - `pipelines/pilots/d06_earth_soilgrids/` — 75 obs, 5 AOIs, 0–5 cm
  - `pipelines/pilots/d06_earth_soilgrids_depths/` — 300 obs, 5 AOIs, 0–5/5–15/15–30/30–60 cm (0–5 cm re-export, same observation IDs upserted)
  - `pipelines/pilots/d06_earth_soilgrids_expansion_01/` — 45 obs, 3 AOIs, 0–5 cm
  - `pipelines/pilots/d06_earth_soilgrids_all_0_5cm/` — 1575 obs, 105 AOIs, 0–5 cm
- **Source:** ISRIC SoilGrids 2.0, 250 m global model predictions via public OGC WCS 2.0.1 (`https://maps.isric.org/mapserv`), **CC BY 4.0**. Method/dataset: `soilgrids-wcs-2.0.0` / `soilgrids-250m-v2`. Not local measurements.
- **Properties:** clay/sand/silt (`g kg-1`), soc (`dg kg-1`), bdod fine-earth bulk density (`cg cm-3`). Statistics: mean, Q0.05, Q0.95. Depths kept separate (never pooled).
- **Q05/Q95:** prediction-quantile uncertainty evidence only (not independent scored features, not AOI-mean confidence intervals).
- **Gates:** any added/queryable depth/AOI support must satisfy exact support/version/depth/statistic gates; no unvalidated expansion.

## 3) Moon/Mars sparse regolith evidence

- **Requested features:** grain size (particle size distribution), density (bulk density), porosity, thickness, cohesion, friction (and bearing strength where applicable).
- **Registered D06 features (current):** `particle_size_distribution`, `bulk_density_kg_m3`, `porosity_fraction`, `cohesion_kpa`, `friction_angle_deg`, `bearing_strength_kpa` (all evidence-only, depth-required when detected). **`regolith_thickness_m` is not registered** in current D06 feature registry/shared contracts → recorded as `unsupported` (shared contract/registry change required via #141).
- **Official/licensed source audit (bounded, low-resource):**
  - Moon: NASA PDS Geosciences Apollo 15 LSRP (`urn:nasa:pds:a15lsrp_soil_mechanics`, https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a15lsrp_soil_mechanics/), Apollo 16 LSRP (`urn:nasa:pds:a16lsrp_soil_mechanics`), Apollo 15/17 HFE (`urn:nasa:pds:a15_17_hfe_concatenated`) — public PDS archives with documented provenance/checksums; penetration/thermal records provide sample-site support but mechanics parameters (cohesion/friction/bearing) require reduction-model assumptions and are not packaged as direct typed feature tables.
  - Mars: NASA PDS InSight HP3/RAD (`urn:nasa:pds:insight_hp3_tem`, `urn:nasa:pds:insight_rad`, https://pds-geosciences.wustl.edu/missions/insight/hp3rad.htm) — bounded landing-site calibrated/derived products; physical/mechanics properties largely model-derived/inferred with assumptions.
  - Secondary index (alternative): Gasteiner et al. Lunar Regolith Database (arXiv/CC BY 4.0, https://arxiv.org/abs/2602.03829v2, https://github.com/leoniegasteiner/Lunar-Regolith-Database) — provenance-linked compilation requiring per-record source traceability and separation of simulants.
- **Ingest status in this pass:** no fabricated/zero-filled/inferred values. A bounded explicit-coverage typed release is provided for supported Moon/Mars D06 feature families with `state=no_coverage` and official blocker/provenance notes, making absence machine-queryable. Per-sample numeric ingest of cohesion/friction/density/porosity/PSD/thickness remains **blocked** pending bounded extraction of officially versioned, sample-supported records with explicit depth/basis/assumptions and (for thickness/new semantics) shared-registry alignment via #141.
- **Alternative source path:** ingest selected LSRP/HFE/HP3-RAD-derived values only as `evidence_only` records with reduction-model assumptions, sample IDs, depth support, and source checksums once a bounded parser/mapping is validated; or ingest filtered, source-cited rows from the CC BY lunar compilation pinned to a commit with original-source attribution preserved.

## 4) Bounded ingest/index and representative queries

- **Release:** `pipelines/pilots/d06_moon_mars_regolith_evidence/` (portable acquisition-style release: `manifest.json`, `plan.json`, `observations.jsonl`, `assets.json`, `requests.jsonl`, `qa.json`, `REPORT.md`).
- **Import:** `uv run python scripts/import_typed_release.py --release pipelines/pilots/d06_moon_mars_regolith_evidence --database var/analog.db`
- **Representative queries (all three bodies):**
  - Earth (real indexed data): D06 SoilGrids observations queryable after importing committed SoilGrids releases, e.g. `GET /api/v1/typed/observations?dimension=D06&body=Earth&feature_id=soil_clay_0_5cm_mean&limit=3`.
  - Moon (explicit coverage state): `GET /api/v1/typed/observations?dimension=D06&body=Moon&limit=5` returns explicit `no_coverage` evidence records from this release.
  - Mars (explicit coverage state): `GET /api/v1/typed/observations?dimension=D06&body=Mars&limit=5` returns explicit `no_coverage` evidence records from this release.

## 5) Tests, provenance, limitations

- **Tests/docs added or updated:** this report, `docs/D06_COVERAGE_MATRIX.json`, Moon/Mars pilot + manifest/provenance, and D06-owned tests validating matrix/pilot integrity.
- **Provenance:** source URLs/IDs, licences, versions, method/dataset IDs, checksums, CRS/frame, depth/spatial/temporal support, QA, uncertainty, and legal/score roles recorded in matrix and release manifests.
- **Performance/storage:** bounded/text-only artifacts; no raw rasters committed; no global/high-resolution downloads.
- **Limitations:** Earth remains SoilGrids model prediction (not local measurement), deeper-than-0–5 cm coverage not universal across all 113 AOIs, Moon/Mars lack ingested per-sample numeric regolith measurements in this pass (explicit no_coverage/blocked), thickness unsupported by current feature registry, no claims of global or exhaustive scientific coverage.

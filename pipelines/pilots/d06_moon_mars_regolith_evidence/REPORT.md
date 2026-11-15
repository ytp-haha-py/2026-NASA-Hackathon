# D06 Moon/Mars sparse regolith evidence audit

- Release version: `acquisition-release-1.0.0`
- Scope: `d06-moon-mars-regolith-evidence-v1`
- Generated at: `2026-11-15T12:00:00+00:00`
- Observations: 12 (6 Moon + 6 Mars), all `no_coverage`, all `score_role=evidence_only`
- Areas: moon-apollo15-station8-lsrp (IAU_2015:30100, lunar_planetocentric), mars-insight-elysium-hp3 (IAU_2015:49900, mars_planetocentric)
- Features (supported IDs): particle_size_distribution, bulk_density_kg_m3, porosity_fraction, cohesion_kpa, friction_angle_deg, bearing_strength_kpa

## Official/licensed source audit (bounded)

**Moon (NASA PDS, public/open):**
- Apollo 15 LSRP soil mechanics: urn:nasa:pds:a15lsrp_soil_mechanics — https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a15lsrp_soil_mechanics/
- Apollo 16 LSRP soil mechanics: urn:nasa:pds:a16lsrp_soil_mechanics — https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a16lsrp_soil_mechanics/
- Apollo 15/17 Heat Flow Experiment: urn:nasa:pds:a15_17_hfe_concatenated — https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a15_17_hfe_concatenated/

**Mars (NASA PDS, public/open):**
- InSight HP3 TEM: urn:nasa:pds:insight_hp3_tem — https://pds-geosciences.wustl.edu/insight/urn-nasa-pds-insight_hp3_tem/
- InSight RAD: urn:nasa:pds:insight_rad — https://pds-geosciences.wustl.edu/insight/urn-nasa-pds-insight_rad/
- Mission page: https://pds-geosciences.wustl.edu/missions/insight/hp3rad.htm

**Alternative/provenance-linked source:** Gasteiner et al. (2026), "An Open Database of Lunar Regolith and Simulants Properties", CC BY 4.0, https://arxiv.org/abs/2602.03829v2, https://github.com/leoniegasteiner/Lunar-Regolith-Database (secondary compilation; per-record official-source attribution and simulant separation required).

## Blockers and assumptions

- No numeric values fabricated, zero-filled, or inferred from DEM roughness/WorldCover/Earth transfer.
- Penetration/thermal/mechanics products provide sample or site support but many requested parameters (grain size distribution, density, porosity, cohesion, friction, bearing/thickness) require bounded extraction and/or reduction-model assumptions with explicit depth/basis/method metadata; direct typed per-sample ingest not completed in this pass.
- `regolith_thickness_m` is not a registered D06 feature ID in the current registry/shared contracts; thickness evidence is reported as unsupported in the coverage matrix (shared change via #141).

## Reproduction

```bash
uv run python scripts/import_typed_release.py --release pipelines/pilots/d06_moon_mars_regolith_evidence --database var/analog.db
```

Representative queries (after import): `GET /api/v1/typed/observations?dimension=D06&body=Moon&limit=5` and `...body=Mars...` return explicit no_coverage evidence records.

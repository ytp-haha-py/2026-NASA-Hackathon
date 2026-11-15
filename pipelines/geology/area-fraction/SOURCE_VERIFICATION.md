# D02 source verification report — geology area fractions

Checked 2026-11-15. Scope: bounded pilot over the AOI windows already committed
in `pipelines/geology/areas.json` and the source-derived overlay record
`pipelines/geology/records/geology-e920e7e3ae79e176.json`. No new download, no
global survey and no numeric geology similarity were produced.

## Source status

| Body | Dataset | Exact version / access | Verified licence | Pilot status |
|---|---|---|---|---|
| Moon | USGS Unified Geologic Map of the Moon, GIS v2 (`usgs-moon-geology-v2`) | v2.0, March 3, 2020; `https://astrogeology.usgs.gov/search/map/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2/` | CC0 with requested author citation; product page states CC0 | verified, 1 AOI, source-derived |
| Mars | USGS SIM 3464 Jezero crater and Nili Planum, `GeoUnits_v1` (`usgs-jezero-sim3464`) | 2020; `https://pubs.usgs.gov/publication/sim3464` | USGS-authored GIS public domain; third-party pamphlets materials excluded | verified, 2 explicit test AOIs, source-derived |
| Earth | GLiM polygon GIS via third-party ArcGIS mirror (`glim-niveau-i-mirror`) | mirror describes v1.1; upstream equivalence not verified | **unverified** — see below | availability only; **no geometry or per-unit value exported** |

The Moon and Mars records had already passed the dimension's source audit and
checksum/snapshot checks. This pilot adds no new source data; it maps the
observed raw codes, measures area with an equal-area CRS and exports evidence.

- Moon native frame: Moon 2000 sphere, planetocentric latitude, east-positive
  longitude, LOLA control. Map scale 1:5,000,000. No Earth WGS84 anywhere.
- Mars native frame: Mars 2000 sphere, planetocentric latitude, east-positive
  longitude, body-fixed MTM zone 77.5E. Map scale 1:75,000.
- Earth: AOIs are EPSG:4326; no planetary CRS is substituted.

## Legend registry

`registry.json` is `geology-legend-registry-1.0.0` and non-exclusive. Only codes
observed in the committed record are mapped:

| Dataset | Raw code | Official unit | Labels (non-exclusive) |
|---|---|---|---|
| Moon | `Ec` | Eratosthenian Crater (family `Crater Unit`) | impact (`impact_crater_unit`, mapped_geology) |
| Mars | `cr` | Crater rim unit | impact (`impact_crater_rim`, mapped_geology) |
| Mars | `NHjf2` | Jezero fan 2 unit | sedimentary (`deltaic_lacustrine_deposition`, documented interpretation) |
| Mars | `Aeb` | Eolian bedform unit | sedimentary (`eolian_deposition`, mapped_geology) |
| Mars | `Nue`, `Nle`, `Njf` | etched/floor units | none — `mapped_without_process_evidence` |

Notes:

- No unit in this pilot is labelled `aqueous_alteration`. The registry validation
  rejects aqueous claims that lack a positive documented alteration statement,
  and rejects iron-oxide/spectral-only wording.
- `Nue`, `Nle` and `Njf` keep zero process labels. `Njf` mentions impact craters
  *on* its surface; that is not impact-process evidence for the floor unit
  itself, so no label is guessed.
- Unobserved codes (the rest of both legends) resolve to `unmapped_code`. The
  registry is deliberately not a global code table: Moon `Ec` and Mars `cr`
  resolve only inside their own dataset and never prove cross-body equivalence.
- `Aeb → sedimentary` is added by this registry because the official unit name
  and description document wind-formed bedforms. The legacy record's
  `geological_processes` list (fan/crater families only) is unchanged.

## Measured pilot (real committed AOI windows)

Fractions are of the AOI footprint, measured in a local equal-area projection
(LAEA on the native body radius). They are geometry, not composition.

| Body | AOI | Coverage state | Covered fraction | Measured evidence |
|---|---|---|---:|---|
| Moon | `moon-shackleton-rim-site04-roi2` | partial | 0.999998889958193 | `Ec` impact 0.999998889958193 (3,920,364.16 m² of 3,920,368.51 m²); uncovered 1.11e-6 |
| Mars | `mars-test-jezero-western-delta` | complete | 1.0 | `NHjf2` sedimentary 0.7620289641123452; `Aeb` sedimentary 0.08715784519582853; `cr` impact 0.08819231297289115; `Nue` no process label 0.06262087772500854 |
| Mars | `mars-test-jezero-floor` | complete | 0.9999999999992588 | `Njf` 0.5442482311477604 (no process label); `Nle` 0.29807064163286107 (no label); `Aeb` 0.04149347913127842; `Nue` 0.024949473044708306; `NHjf2` sedimentary 0.0912381750426505 combined over two polygons |

Category fractions (non-exclusive; do not sum into a score):

- Moon: impact 0.999998889958193. Unknown: igneous, sedimentary, aqueous alteration.
- Mars western delta: sedimentary 0.8491868093081738; impact 0.08819231297289115.
  Unknown: igneous, aqueous alteration.
- Mars floor: sedimentary 0.1327316541739289. Unknown: igneous, impact, aqueous alteration.

Earth: all five GLiM AOIs are `license_unverified` with null fraction/area and
no process labels. The other Earth rows (EMIT, lunar minerals, CRISM context)
retain their own `auth_required` / `no_coverage` / `unverified` status; none is
converted to zero.

## Earth GLiM licence verification

Verdict: **`license_unverified`; redistribution not allowed.** Findings, with
the public pages actually read:

- CCGM/CGMW product page for the Lithological Map of the World (1st edition,
  2015): priced product marked `©CCGM-CGMW 2015`; the PDF edition is subject to
  a licence agreement.
  `https://www.ccgm.org/en/product/lithological-map-of-the-world`
- CCGM/CGMW digital-map licence agreement: internal education/research use for
  the licensee's institution, five-year term, no assignment, sale, distribution
  or transfer to third parties.
  `https://www.ccgm.org/en/licence-agreement-for-the-digital-maps-of-the-commission-for-the-geological-map-of-the-world`
- University of Hamburg project page: the map can be ordered from CCGM and the
  original GIS data are distributed through the CCGM link; no open licence is
  stated for the polygon GIS.
  `https://www.geo.uni-hamburg.de/en/geologie/forschung/aquatische-geochemie/glim.html`
- ArcGIS mirror item used by the pilot: `licenseInfo` is empty; provenance and
  upstream equivalence are unverified.
  `https://www.arcgis.com/sharing/rest/content/items/cff7159868224551bf2d03762702c18d?f=pjson`
- PANGAEA 788537: CC BY 3.0 applies to the separate **0.5° gridded** product,
  not to the polygon GIS. That licence and resolution are not transferred.

Decision: keep locally computed GLiM overlays in the external cache; commit only
availability status. Do not publish GLiM geometry or derived per-unit values.

Legally usable alternative: **Generalized Geology of the World, Geological
Survey of Canada Open File 5529 (2007)**, distributed under the **Open
Government Licence – Canada, v2.0** (worldwide, royalty-free, commercial use
allowed with attribution; third-party rights excluded).
`https://doi.org/10.4095/223767`, `https://mrdata.usgs.gov/geology/gscworld`,
`https://open.canada.ca/en/open-government-licence-canada`.
Status: `candidate_not_ingested`. It is ~1:35,000,000 generalized bedrock, far
coarser than GLiM; any future fetch must be opt-in, AOI-bounded and checksummed
(the GIS ZIP is ~550 MB).

## Method and QA

- Native-source CRS for overlay; equal-area LAEA only for area measurement, on
  the correct body radius (`1737400` m Moon, `3396190` m Mars, WGS84 Earth).
- Invalid source polygons are excluded by default and counted; explicit repair
  uses `make_valid` and reports `repaired_count`. Invalid AOIs fail closed unless
  repair is requested.
- Categorical grids use nearest-neighbour resampling only; outside-source pixels
  require explicit nodata. A valid class `0` is a detection, not missing data.
- Every record carries raw code, source feature ID and attributes, product,
  version, licence, map scale, source/analysis CRS, fraction, area, coverage
  state, uncertainty note and QA flags.
- Fraction tolerance is `1e-6`. Moon coverage 0.99999889 is therefore `partial`,
  not rounded up to complete.
- Evidence validation rejects zero-filled availability, cross-body labels,
  duplicate records, fraction sums below coverage, and any
  `similarity`/`combined_score`/`geology_score` key.

## Reproduction

```bash
# dry run first (validates registry + source record, prints summaries)
uv run --frozen python -m analog_pipelines.geology.area_fraction --report-only

# write portable evidence
uv run --frozen python -m analog_pipelines.geology.area_fraction

uv run --frozen pytest pipelines/tests/test_geology_area_fraction.py -o addopts='' -q
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt \
  pytest pipelines/tests/test_geology_area_fraction_spatial.py -o addopts='' -q
```

## Remaining blockers / limits

- Moon/Mars fractions come from the existing committed overlay record; re-running
  the raw polygon overlay requires the ~1.6 GiB external source cache and is not
  part of this offline pilot.
- The Moon AOI is 1.98 km while the source map is 1:5,000,000 with ~3 km vertex
  spacing; sub-map-scale frame alignment between MOON_ME DE421 terrain and Moon
  2000/LOLA remains unverified.
- No validated lithology comparator exists, so no geology similarity or combined
  score is produced.
- GLiM polygon rights remain unresolved; the GSC alternative is identified but
  not ingested or licence-rechecked against a downloaded copy.

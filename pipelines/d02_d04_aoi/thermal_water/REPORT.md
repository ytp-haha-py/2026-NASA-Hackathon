# D03/D04 AOI evidence report (#170 Goal E)

Status: **bounded, evidence-only; no score; not a global survey.**

## Counts

| Item | Value |
|---|---:|
| Reference AOIs | 23 (Moon 18, Mars 5) |
| New typed observations | 64 (GCP 36, TES 10, PRP annual mean 18), all `detected` |
| New modeled evidence rows | 59 (PRP annual max 18, PRP stability depth 18, PRP shallow fraction 18, Odyssey NS 5) |
| Modeled states | `detected` 44, `unknown` 15 |
| Areas / datasets / assets | 39 / 4 / 4 |
| New source failures | 0 |

The 15 `unknown` modeled rows are PRP stability-depth rows where less than half of the window
area has a modeled shallow (`<= 2.8738 m`) stability depth. The shallow-area fraction is still
recorded; the depth is not zero-filled or guessed.

## New sources

| Source | Body / dim | Class | Native support |
|---|---|---|---|
| LRO Diviner GCP 0.5° cumulative | Moon / D03 | measured radiometric composite | 0.5° cell |
| MGS TES TIMAP day/night | Mars / D03 | derived retrieval | 2.96 km/pixel + interpolation mask |
| LRO Diviner PRP south | Moon / D03+D04 | modeled | ~0.3-0.4 km triangle mesh |
| Odyssey NS epithermal 0.5° | Mars / D04 | measured proxy | 0.5° cell (~29.6 km) |

Existing #43/#44 PCP, THEMIS, LP NS, Mini-RF and HEND observations are linked by
`observation_id` in `aoi_evidence.json`; they are not rewritten.

## Explicit gaps

- Moon PCP: 7 noon-window `no_coverage`, 3 `unknown` (existing sparse native bins).
- Moon PRP depth: 15 `unknown` due to <50% valid shallow area; 3 `detected` (site07, site20,
  site23) with mean depths 0.403, 0.249 and 0.229 m.
- Moon existing ice claims: 23 `unknown`, 5 `no_coverage`, 1 `not_detected`; 0 confirmed ice.
- Mars HEND: 5 windows share one 4° cell; claim `unknown`; 0 confirmed ice.
- No new Mars AOIs. No global coverage.

## Non-claims

GCP/TES are coarse context, not AOI measurements. PRP is model-derived, not observed ice.
Odyssey NS counts/second are not the HEND normalized flux ratio. No D03/D04 similarity feature,
no cross-body score, no ice fraction, reserve or mineable-ice estimate.

Full provenance, values and limitations: [`docs/D03_D04_AOI_170.md`](../../../docs/D03_D04_AOI_170.md).

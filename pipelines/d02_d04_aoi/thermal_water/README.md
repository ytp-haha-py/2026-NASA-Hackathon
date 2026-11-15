# D03/D04 AOI evidence package (#170 Goal E)

Bounded, evidence-only D03 thermal and D04 water/ice evidence for the 23 committed Moon/Mars
reference windows. This directory is new; it does not rewrite the #43/#44 pilots.

| File | Contents |
|---|---|
| `manifest.json` | Counts, per-source status, SHA-256 of every committed artifact |
| `sources.json` | Full metadata for the four new sources (URL, DOI, licence, CRS, support, QA, uncertainty) |
| `datasets.json` | Typed-v2 dataset records for the four new sources |
| `assets.json` | Source/window asset records with SHA-256 checksums |
| `areas.json` | Typed-v2 areas for the new native cells/windows |
| `observations.jsonl` | New typed-v2 observations: Diviner GCP, TES TIMAP, PRP annual mean |
| `modeled_evidence.jsonl` | PRP annual maximum, PRP stability depth, PRP shallow-stability area fraction, Odyssey NS counts |
| `aoi_evidence.json` | One entry per reference AOI, with existing #43/#44 observations and new evidence/status |
| `REPORT.md` | Short human-readable summary |

## Rebuild and check

```bash
# First run: bounded range reads + one 604.8 MB PRP cache file under var/cache/
uv run python pipelines/d02_d04_aoi/thermal_water/build.py --download

# Offline rebuild from the cache
uv run python pipelines/d02_d04_aoi/thermal_water/build.py

# Validate checksums/states/AOI count without network
uv run python pipelines/d02_d04_aoi/thermal_water/build.py --check
```

Raw source bytes stay in `var/cache/d03-d04-aoi-170/` (gitignored). Only derived JSON/JSONL and
Markdown are committed. Full source/limitation discussion is in
[`docs/D03_D04_AOI_170.md`](../../../docs/D03_D04_AOI_170.md).

## Non-claims

No D03/D04 similarity score, no cross-body ranking, no global coverage, no confirmed ice
detection, no ice fraction/reserve, and no zero-filling. GCP/TES are coarse context; PRP is
model-derived; Odyssey NS counts/second are not the HEND normalized flux ratio.

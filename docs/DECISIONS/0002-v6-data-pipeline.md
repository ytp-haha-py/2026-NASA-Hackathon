# ADR 0002 — Real data first, bounded on-demand AOIs, context-only land cover

- Status: accepted
- Date: 2026-11-14

## Context

Addendum v6 requires: a verified lunar reference, at least three processed Earth areas, on-demand
analysis of a new Earth AOI, one real non-terrain contextual layer, reproducible team datasets, and
honest distinction between measured/derived/contextual/unknown/unavailable. The MVP already had a
working indexed search over Copernicus DEM windows and a synthetic test fixture.

## Decisions

1. **Keep the same four terrain-shape similarity metrics** and the same 90 m / 1.98 km support for
   indexed windows. Do not add a second scoring algorithm.
2. **On-demand AOI** accepts a bounded bbox or closed GeoJSON outer ring (600–2000 m sides, ≤4 km²,
   lat −80..84) and runs the same extractor at 90 m. It never accepts a URL, which removes an SSRF
   surface and keeps providers allowlisted. A new AOI is persisted as an observed candidate with
   `origin=on_demand` and immediately becomes searchable by the normal comparator.
3. **Async job contract**: return `202` with a durable SQLite job row; a bounded in-process worker
   performs I/O. Jobs left `running` on restart are retried. This is intentionally minimal; a
   future distributed queue replaces the adapter, not the API.
4. **ESA WorldCover 2021 v200 is context-only.** It provides 11 Earth land-cover fractions, is
   reported separately from `features`, and never enters the weighted distance. It is not
   mineralogy, geology, or a lunar observation.
5. **One real lunar reference**: LOLA Site04 5 m/pix Shackleton-rim ROI resampled to the shared
   90 m support. Its ~90% interpolation and error estimates are mandatory caveats. Moon degrees are
   never treated as Earth WGS84.
6. **Three data layers**: Git holds code/contracts/manifests; optional S3-compatible object storage
   holds snapshots/artifacts; SQLite is a rebuildable index. The local path and `make data-demo` work
   with no credentials. DVC is not adopted; a pinned SHA-256 snapshot is the current interface.
7. **Partial/failed providers are explicit.** A WorldCover failure yields `partial` with per-provider
   status; terrain failure yields `failed`. Missing context is never zero-filled, and an AOI with
   <80% valid elevation is rejected rather than scored.

## Consequences

- The demo now has real Earth terrain + land-cover context and a real lunar reference, plus a
  verified new-AOI path and reproducible snapshot rebuild.
- On-demand analysis is local/single-node. Throughput and multi-user auth are deferred.
- Land-cover context can annotate/filter future candidates but cannot be presented as a planetary
  similarity score.
- Mars, other consideration buckets, global indexing, and mission simulation remain deferred; see
  `docs/ROADMAP.md`.
- CI stays offline and credential-free; the online provider path is an explicit opt-in smoke script.

## Verification

- `POST /areas/analyze` real run: 202 → completed, 4 terrain metrics, 11 context fractions,
  Copernicus/WorldCover `verified`, repeat request cached.
- `GET /references` exposes the real LOLA reference; reference-mode search ranks Earth candidates.
- `make data-validate` / `make data-rebuild-index` verify checksums and reproduce
  `candidates=5 datasets=3 references=1 features=20 context_observations=55`.
- Offline tests cover AOI validation/jobs/cache, context math, snapshot integrity, and no-data
  failure behavior.

# Planetary AOI Engine & API (Issue #170, Goal D)

This document defines the additive Goal D engine contract implemented in:

- `apps/api/src/analog_api/domain/planetary_aoi_engine.py`
- `apps/api/src/analog_api/api/routes_planetary_aoi_engine.py`

It is additive to and compatible with the Issue #132 planetary AOI flow
(`POST /api/v1/planetary/areas/analyze`) and keeps #116/#117/#132/#160 semantics
compatible (typed evidence, no global/exhaustive claims, no cross-body scoring,
and no Earth WGS84 interpretation for Moon/Mars).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/planetary/aoi` | Create or reuse a durable AOI analysis job (`202` queued/running, `200` terminal in inline mode). |
| `GET` | `/api/v1/planetary/aoi/{job_id}` | Read job lifecycle and current D01–D06 status report. |
| `GET` | `/api/v1/planetary/aoi/{job_id}/result` | Return persisted result; `409 job_not_ready` while queued/running, `409 job_failed` if failed. |

Shared compatibility: `GET /api/v1/jobs/{job_id}` remains available for job rows,
and typed area persistence remains queryable via `/api/v1/typed/areas/{area_id}`.

## BBox -> body coordinate contract (Earth/Moon/Mars)

- The engine/domain contract treats coordinates in the **declared body frame**.
  Moon/Mars are planetocentric body coordinates and are **never** Earth
  `EPSG:4326`/Web Mercator/OSM coordinates.
- Supported coordinate normalization: signed longitude domain and 0–360 input
  (`bbox_lon_convention`: `signed|360`), normalized to signed canonical
  `[-180, 180)` in the body frame.
- Antimeridian crossing is represented explicitly (`crosses_antimeridian=true`)
  with split longitude pieces; valid crossing requests are not rejected merely
  because `west > east`.
- Polar latitudes are supported within `[-90, 90]` (subject to explicit quota/
  provider coverage checks), using body-appropriate geodesy (`pyproj.Geod` with
  body radii/ellipsoid, not Earth-only assumptions for planetary bodies).
- Invalid/ambiguous inputs (non-finite values, invalid lat order, zero-width
  longitude, unsupported convention/body, or spans exceeding policy limits) are
  rejected with explicit validation errors.

## Actual footprint, area, pixels, quotas (no silent shrink)

- The **requested bbox is authoritative**. The response reports `requested_bbox`,
  `analysis_bbox`, `full_aoi`, `analysis_scope`, and `coverage` explicitly.
- The engine never silently shrinks, snaps, or recenters the AOI. If an analyzer/
  provider cannot cover the full requested bbox, the response must report the
  actual analyzed extent/status (for example `selected_cell_union`, partial/failure
  scope) rather than pretending full coverage.
- Footprint accounting is geodesic in the body frame (width/height/area) and can
  be paired with pixel accounting at analysis resolution (`width/height/total`,
  with `partial`/`invalid`/`valid_fraction` reported when known; unknown counts are
  not zero-filled).
- Quotas/limits (side length, area, longitude span, pixel budget) are enforced as
  explicit validation/quota errors; limits may be tightened by policy/provider but
  are not widened silently.

## Job lifecycle, persistence, cache, provenance

- Durable lifecycle: `queued -> running -> completed|partial|failed` in the shared
  `analysis_jobs` table via `JobStore`/`JobRunner`.
- Idempotency/cache: create requests reuse jobs by `idempotency_key` and/or
  deterministic `cache_key` (body + normalized requested bbox + requested features
  + engine/provider-relevant identity inputs). Failed jobs may be re-queued by the
  store's existing retry semantics.
- Persistence: successful planetary analyses persist typed area/observation/source
  records (existing typed-v2/planetary stores) and `data_version` metadata; runtime
  manifests remain auxiliary/best-effort where configured.
- Provenance in responses includes contract/engine/method versions, coordinate
  frame, dataset/source identifiers, analysis kind/coverage notes, provider status,
  warnings, and explicit error details. No fabricated provenance.

## D01 engine (A/B/C) interface integration

- D01 computation is integrated through a pluggable analyzer/provider boundary
  (the existing `validate`/`analyze` AOI processor/provider contract, injectable as
  `planetary_processor` in `create_app`, and the domain `D01Analyzer` protocol for
  engine-level integration). This allows A/B/C D01 engine implementations to be
  selected or injected without changing the route contract.
- The engine report surfaces actual D01 method/source provenance, support, feature
  values, and requested-vs-analyzed footprint. A backend may only report
  `full_requested_aoi` when the analyzed bbox/scope truly covers the full request.

## D01–D06 real-data status semantics

- **D01**: `available` only when derived from a real, measured/analyzed source for
  the reported scope; includes features, method/provenance, and support metadata.
  Pending/failed/no-coverage states are explicit.
- **D02–D06**: returned only from real stored/indexed/declared evidence available
  to the system. If not measured for the AOI, status must be explicit
  `evidence` (stored evidence), `no_coverage`, `not_indexed`, or `unsupported`
  with reasons/provenance basis. **Never zero-filled and never fabricated.**
- "Unsupported on demand" and "stored evidence, not measured for this AOI" are
  distinct concepts and must not be reported as measured values.

## Compatibility

- Additive routes; existing #132 `/api/v1/planetary/areas/analyze` behavior and
  payload compatibility are preserved.
- Keeps #116 eligibility/typed-evidence separation, #117 planetary map/coordinate
  contracts, #132 job/provenance semantics, and #160 coverage/query contract
  expectations compatible.
- No UI changes. No raster computation files from A/B tracks are modified.

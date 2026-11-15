# Data model

The MVP uses SQLite through SQLAlchemy 2. The storage adapter maps rows to domain dataclasses, so
the API and scoring code do not import ORM objects. `ports/repository.py` defines the narrow
Protocols implemented by both the default SQLite adapter and the optional PostGIS adapter
(`docs/POSTGIS.md`; code and offline contract tests exist, but no PostGIS deployment is verified).

## Tables

### `datasets` — provenance manifest

| Column | Type | Notes |
|---|---|---|
| `dataset_id` | text PK | stable slug, e.g. `copernicus-dem-glo90` |
| `provider` | text | organisation |
| `product_id` | text | provider product/version identifier |
| `title` | text null | human-readable product title |
| `url` | text | official product/registry URL |
| `license` / `license_url` | text | actual terms recorded at ingest |
| `access_timestamp` | text | ISO-8601 retrieval time |
| `source_crs` | text null | native raster CRS |
| `native_resolution_m` | float null | native ground sample distance |
| `processing_version` | text | extractor/method version |
| `checksum` | text null | source checksum or derived-window checksum |
| `source_kind` | text | `earth_dem`, `lunar_dem`, `martian_dem`, `synthetic` |
| `notes` | text null | caveats, interpolation notes |

### `candidates` — Earth windows

| Column | Type | Notes |
|---|---|---|
| `location_id` | text PK | `earth-<slug>` |
| `display_name` | text | human label, never "global top" |
| `lat`, `lon` | float | WGS84 centre |
| `west/south/east/north` | float | WGS84 bbox of the analysis window |
| `dataset_id` | FK datasets | source provenance |
| `window_size_m`, `analysis_resolution_m` | float | 1980 m / 90 m |
| `crs` | text | projected CRS used for extraction |
| `valid_fraction` | float | finite cells / total cells (>= 0.8) |
| `quality_flags` | JSON list | e.g. DSM caveats, synthetic marker |
| `notes` | text null | source/quality note |
| `data_kind` | text | `observed` or `synthetic` |
| `area_km2` | float null | requested/derived AOI area |
| `geometry` | JSON null | GeoJSON Polygon for on-demand AOIs |
| `origin` | text | `indexed` seed window or `on_demand` |
| `created_at` | datetime | seed/analysis time |

### `feature_observations`

One row per candidate/feature. Unique `(candidate_id, feature_id)`. Columns: `value`, `unit`,
`method_version`, `extraction_scale_m`, `valid_fraction`, `quality`
(`verified`, `synthetic`, `suspect`).

### typed v2 tables

`typed_areas`, `typed_source_assets`, `typed_observations`, `typed_processing_runs` are
additive. `TypedObservation` keeps `dataset_ids` and `source_asset_ids` distinct; the
v1 adapter creates explicit source-asset IDs and preserves the actual window/resolution/
bbox while leaving unavailable temporal, depth and uncertainty fields as `unknown`.

### `context_observations`

Earth land-cover context (ESA WorldCover), one row per candidate/feature, unique
`(candidate_id, feature_id)`. Columns: `dataset_id`, `feature_id`, `value` (fraction 0..1), `unit`,
`method_version`, `source_resolution_m`, `valid_fraction`, `quality`, `class_code`, `provider`.
These rows are explicitly `context_only` and never enter the weighted distance.

### `analysis_jobs`

Durable local queue for on-demand AOI work: `job_id` PK, `status`
(`queued|running|completed|partial|failed`), unique `cache_key`, optional unique
`idempotency_key`, `area_id`, `request_json`, `provider_status` JSON, `result_json`, `error_code`,
`error_message`, timestamps. On startup, `running` jobs become `queued` and are resubmitted; the
cache key includes AOI geometry, requested features, method/context versions, and provider product
versions.

### `planetary_references` + `reference_feature_observations`

Only populated when a real Moon/Mars raster ROI was processed and validated. Fields mirror
candidates plus `body` (`Moon`/`Mars`) and optional map coordinates. Empty is a valid state and the
UI must show Moon/Mars presets as unavailable.

### `meta`

Key/value rows. `data_version` is a deterministic SHA-256 prefix over the canonical loaded
manifests; `seeded_at` records the seed time. `data_version` changes when observations or provenance
change, so a stale UI can detect a different index.

## Quality and separation rules

- **Source-observed facts** live in `datasets`, candidate metadata, and observations. The API never
  enriches them with invented context.
- **Computed comparisons** are produced at request time from the persisted numbers and the frozen
  feature registry. They are not stored as facts.
- **Hypothetical examples** exist only in the UI and are explicitly labelled.
- **General limitations** are code-defined text kept separate from dataset-derived provenance.
- Every observation carries `method_version`; comparisons with a different version are excluded.

## Seeding and migration

```bash
uv run python -m analog_api.cli seed            # reset + load committed manifests
uv run python -m analog_api.cli seed --keep-existing
uv run python -m analog_api.cli seed --include-synthetic   # TEST-ONLY demo data
uv run python -m analog_api.cli status
```

Manifests are validated by Pydantic (`domain/manifest.py`) before persistence. The seed recomputes
`data_version` and is idempotent when `--keep-existing` is used.

`seed_manifests(reset=True)` now clears only the derived scientific index. It preserves
on-demand AOIs, their feature/context observations, `analysis_jobs`, typed v2 tables, and
datasets still referenced by those rows. It never calls `drop_all` on the runtime schema.
Regression coverage: `pipelines/tests/test_sqlite_safety.py`.

MVP schema creation is `Base.metadata.create_all`. Before evolving columns in a persistent
deployment, add Alembic:

```bash
uv add --group dev alembic
uv run alembic init apps/api/alembic
# configure sqlalchemy.url from ANALOG_DATABASE_URL, autogenerate, review, then upgrade head
```

The API must never depend on SQLite-specific SQL. The optional PostGIS adapter implements the
same Protocols and preserves entity results (`docs/POSTGIS.md`); it has not been verified against
a live deployment. Deployment still needs a PostgreSQL/PostGIS service, Alembic migrations, a
spatial index, and a backup/restore runbook.

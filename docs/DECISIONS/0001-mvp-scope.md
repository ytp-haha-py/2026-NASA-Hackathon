# ADR 0001 — Indexed-subset analog search for the MVP

- Status: accepted
- Date: 2026-11-14
- Context: NASA Space Apps Challenge 2026 — permanent Moon/Mars base locations challenge

## Decision

Ship an MVP that ranks **a small, reproducible index of Earth terrain windows** against a
user-supplied vector of four local terrain-shape metrics. Expose indexed/eligible/excluded counts
and geographic coverage, and label every result set as `indexed_subset`. Do not implement real-time
global ingestion, an exhaustive global top 10, or AI-generated scientific explanations in the MVP.

## Context and alternatives

1. **Real-time global search over Earth DEM tiles** would require global tiling, heavy egress,
   long-running requests, and an unverifiable "global" claim within hackathon time. It would also
   obscure which windows were actually processed.
2. **A static "top 10 analogs" list** without user-selected dimensions would not match the team's
   workflow (choose dimensions -> common search engine -> ranked locations) and would not be
   scientifically inspectable.
3. **Indexed subset + reproducible pipeline** lets reviewers re-run ingestion for each window,
   inspect provenance, and reproduce every distance from committed numbers. It is honest about
   coverage and extendable to global indexing later.

## Consequences

- The product is a scientific search tool over a curated subset, not a planetary base-site chooser.
- The UI must show "Top N among M indexed regions", coverage bbox, data kinds, and disclaimers.
- Every candidate requires persistent provenance and a shared method version.
- Comparison is limited to terrain shape; gravity, atmosphere, radiation, regolith, mineralogy,
  temperature, and geological history are explicitly not simulated.
- Global indexing, mission presets, and verified Moon/Mars references are follow-up work with
  documented interfaces. Missing data never gets a synthetic value in observed manifests.
- Distances are unitless ranking aids with fixed registry scales; they are never presented as
  similarity percentages.

## Verification

- `POST /api/v1/search` returns deterministic results from committed manifests.
- `GET /api/v1/capabilities` reports real indexed counts and coverage (including zero).
- Synthetic fixtures are marked `data_kind=synthetic` and labelled DEMO / SYNTHETIC.
- Real Copernicus DEM ingestion commands and provenance are documented in `docs/DATA_SOURCES.md`.

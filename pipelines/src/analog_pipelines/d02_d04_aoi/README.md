# D02/D03/D04 AOI footprint discovery (Issue #170 Goal E)

This package provides **bounded, official-source footprint discovery** for a single real AOI (bbox),
then emits **typed-v2 style evidence records** (or explicit `no_coverage`) for D02 (Geology/Mineralogy),
D03 (Thermal), and D04 (Water/Ice). It reuses prior pilot logic where applicable and does **not**
re-download global datasets or fabricate values.

Constraints (from #170):
- Use official footprint discovery (NASA PDS ODE / official sources) to locate intersecting products/footprints
  and record **version, license (if stated), time/depth/resolution** metadata when available.
- For at least one real AOI, D02/D03/D04 must have either a locatable observation or explicit `no_coverage`
  (no borrowing across AOIs).
- Typed-v2 observations must preserve **state/role/support/uncertainty/provenance**.
- QA/eligibility: **evidence-only must not be scored**; **proxy must not be treated as measured**.
- No changes to apps/api shared schema/scoring.

Outputs are written under a user-chosen output directory (committed artifacts should be small and checksummed),
never committed raw caches.

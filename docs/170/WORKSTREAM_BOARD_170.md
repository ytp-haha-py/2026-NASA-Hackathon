# #170 Planetary AOI — eight primary Goals (live board)

Status snapshot: **2026-11-15 22:0x local**, coordinator session
`ses_e253e85e9ffeLjB1ipw0gIVen0` (native goal `a1caf80d-100c-4491-a301-cdf5020f0a69`).
This board records what is actually running, not what is planned.

## Primary Goals

| Goal | Worktree | Branch | Session ID | Native Goal ID | Subagents active | Current work | PR | Blocker |
|---|---|---|---|---|---|---|---|---|
| A Moon Global D01 | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-a-moon-d01` | `feat/170-a-moon-d01` | `ses_e2083965affePyD1DFcE7D4XDk` | `e80e773e-4a7b-4b9e-8df7-1eed2f6e35d6` | A1 LOLA source/range (`ses_e20825451ffenFgraoCAximGuV`), A2 AOI terrain (`ses_e20825450ffecElFXhLRDCEJ0F`) | verify PGDA/SVS LOLA; chunked full-bbox AOI; 3 dispersed Moon AOIs | none yet | official LOLA range/format |
| B Mars Global D01 | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-b-mars-d01` | `feat/170-b-mars-d01` | `ses_e20839605ffeYMRl7a8idjZOnS` | `3f3af04c-4cd8-4fed-b5f2-d8a6f6405823` | B1 MOLA PDS4/tiles (`ses_e20825440ffe7mJj2MBkVAMU7p`), B2 AOI terrain (`ses_e20825440ffd8xlaQU83TUsW45`) | verify PDS4 MEGDR; bounded tile/range; 3 dispersed Mars AOIs | none yet | PDS4/range + tile cache |
| C Raster Infrastructure | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-c-raster-infra` | `feat/170-c-raster-infra` | `ses_e208395a6ffenIr8q3tsnZ7Z57` | `e66337ce-30dd-44d9-a68e-8ddf61210cfa` | C1 source adapter (`ses_e2082543effdWqBExxPAPmYi8J`), C2 cache/resume/budget (`ses_e20825434ffeJiZXUOHLaz36gx`) | range/vsicurl adapter, atomic cache, checkpoint, 2–4 heavy budget | none yet | interface coordination with A/B/D |
| D Planetary AOI Engine/API | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-d-aoi-engine` | `feat/170-d-aoi-engine` | `ses_e2083954fffeW90CHIYNuibl2D` | `88bfc6ce-1906-4eb6-9f76-88e853a40f65` | D1 bbox/projection (`ses_e20825434ffdMR8qfdeLXJ3Fh1`), D2 jobs/API (`ses_e20825433ffelm7NxOsYfDYyzU`) | full-bbox geometry, antimeridian/pole, job lifecycle, six-dim contract | none yet | depends on A/B engines for real values |
| E D02–D04 data | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-e-d02-d04` | `feat/170-e-d02-d04` | `ses_e208394fcffeSJyQb9qptn98oF` | `11d0665b-d552-404c-a411-f01af720cd7d` | E1 ODE/D02 (`ses_e2081db26ffe7QihEAOme91F9t`), E2 D03/D04 (`ses_e2081db25ffenM2LnZSFGDgmSH`) | official footprint discovery, real AOI evidence, QA | none yet | source availability; heavy jobs capped |
| F D05–D06 data | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-f-d05-d06` | `feat/170-f-d05-d06` | `ses_e208394aaffeXhJ8fhWyYGJQsG` | `930a3ed7-deec-40be-bdc2-0c7be8fffae6` | F1 illumination/radiation (`ses_e2081db17ffeO32DuKhgoDWpP2`), F2 regolith (`ses_e2081db17ffd4Vt9xrP4hdb2e2`) | D05 time/geometry separation; D06 real support or `no_coverage` | none yet | ionizing/regolith official products |
| G Cross-body similarity | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-g-cross-body` | `feat/170-g-cross-body` | `ses_e20839456ffehxt4yXD7C3ZvAf` | `bf8d6441-b2a1-4850-bfa8-7cb210afcfec` | G1 comparability gate (`ses_e2081db15ffeR4z6daapoAdBZB`), G2 weighted Earth search (`ses_e2081db0effe4m8JxkkTCF8Zu6`) | shared-feature/scale gate, weights, Earth ranking tests | none yet | depends on A/B/C interfaces |
| H Integration/E2E QA | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-170-h-integration` | `feat/170-h-integration` | `ses_e208393fcffe9inydF80JToj1z` | `a790327f-05f6-482b-9bda-bcb0b0fcabe8` | H1 E2E script (`ses_e2081daffffeukq7Q89HlS8Uo0`), H2 API/types/handoff (`ses_e2081dafeffeKku9nngjxyjctw`) | Moon/Mars E2E from round one; API/TS/CI/handoff | none yet | waits for A–G surfaces |

## Actual concurrency

- **Active workers: 16 subagents + 1 coordinator = 17.** All 16 subagent sessions report
  `outcome=None` with a live `busy` timestamp; they are not planned-only.
- **Queued: 0.** All 16 were launched.
- **Idle: 8 primary Goal sessions.** Their native Goals are verified (`metadata.source=goal`) but
  the OpenCode goal command only sets the goal; the primary sessions return to idle and will be
  prompted to integrate their subagents' outputs. The actual implementation is running in the
  subagents inside each Goal's worktree.
- **Heavy raster concurrency cap: 2–4.** A/B/C are currently doing source verification and adapter
  design; full global downloads are reserved and budgeted by C2. E/F use bounded API/product
  queries. No single job may exhaust RAM/disk/network.

## File ownership (avoid parallel writes)

| Area | Owner |
|---|---|
| LOLA source adapter / Mars MOLA source adapter | A1 / B1 |
| Moon AOI terrain / Mars AOI terrain | A2 / B2 |
| Shared raster source / cache | C1 / C2 |
| Planetary AOI geometry / jobs+API | D1 / D2 |
| D02 + D03–D04 bounded artifacts | E1 / E2 |
| D05 illumination+radiation / D06 regolith | F1 / F2 |
| Cross-body gate / weighted search tests | G1 / G2 |
| E2E script / API types+handoff | H1 / H2 |

Shared `apps/api` schema/scoring changes are **not** delegated; they return to the Coordinator /
Integration path.

## Milestones

- **M0** Moon + Mars each ≥3 dispersed real official DEM AOIs with real elevation/features,
  checksum/version, reproducible command, valid fraction; no-data explicit.
- **M1** full-bbox analysis (not centre 5 km), pixel/area/quota limits, partial/invalid, pole and
  antimeridian tests.
- **M2** six-dimension AOI query with feature/quantity/value/state/method/source/support/time/depth/
  uncertainty/score-role; real D02–D06 only where locatable.
- **M3** legal Earth comparable search from Moon/Mars targets; no mixed-scale score.
- **M4** ≥3 Moon + ≥3 Mars E2E, negative cases, CI/OpenAPI/types/handoff, PR without auto-merge.

## Verification

```bash
opencode session export <session> | grep -c '"source": "goal"'
opencode api GET /api/session/<session>
bash scripts/verify.sh --ci   # per Goal before PR
```

Do not claim completion for a Goal until its own commands/tests and a PR exist.


## Verified M0 source evidence (Coordinator, 2026-11-15)

### Moon — LOLA128 global GeoTIFF

`https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif`

```text
HTTP/1.1 200 OK
Accept-Ranges: bytes
Content-Type: image/tiff
Content-Length: 2705286892
Last-Modified: Fri, 03 May 2024 14:05:13 GMT
range bytes=0-511 -> HTTP 206, 512 bytes, TIFF magic II+\x00
```

The global product is range-readable; A2 should use bounded `/vsicurl/` windows, not a 2.7 GB
download. 128 ppd ≈ 236.9 m at the equator; CRS/nodata must be verified from the file by A1.

### Mars — MOLA MEGDR PDS4 tiles

Index: `https://pds-geosciences.wustl.edu/missions/mgs/megdr.html`

Observed tile URLs (PDS4 Product 1.1):

```text
.../meg004/megr90n000cb.img   (4 ppd)
.../meg016/megr90n000eb.img   (16 ppd)
.../meg032/megr90n000fb.img   (32 ppd)
.../meg064/megr90n000gb.img   (64 ppd north)
.../meg064/megr90n180gb.img   (64 ppd south)
```

B1 must verify byte-range/label semantics and use bounded reads or a small staged tile cache.
MEGDR is global but coarse; it is not replaced by Jezero CTX and not treated as 90 m data.

# AI Disclosure — AnalogX

**Record date:** 15 November 2026 (Asia/Taipei)  
**Scope:** Human–AI collaboration on the AnalogX project for the NASA Space Apps Challenge 2026.

This document describes how AI assistants contributed to project planning, implementation, debugging, data processing, scientific review, testing, documentation, and GitHub coordination. It retains the existing Codex, ChatGPT, OpenCode, and Claude collaboration records and adds a source-based Codex self-audit and a submission-content audit. Here, **SI** follows the challenge's disclosure terminology; the tools are also called AI assistants throughout this document.

The account is **de-identified at the narrative level**: human participants are described by project roles rather than personal names, and private conversation transcripts, account information, machine-specific paths, and OpenCode session identifiers are omitted. Links to public GitHub issues, commits, pull requests, and screenshots remain because they are useful for independent verification; those public records may still identify their authors.

## Collaboration model and accountability

| Participant or tool | Main role | Boundary |
| --- | --- | --- |
| Project team / human maintainer | Define the product, choose research priorities, correct misunderstandings, decide what to accept or reject, and authorize repository changes | Humans retain responsibility for scientific claims, licenses, review, and release decisions |
| **ChatGPT (OpenAI)** | Translate product goals into technical requirements; critique UI, API, and data assumptions; inspect GitHub code/CI; draft issues and agent instructions; review and, when explicitly authorized, merge PRs | Recommendations and conversational analysis are not themselves code execution or scientific verification |
| **OpenCode** | Coordinate multiple coding sessions, native Goals, specialized agents, Git worktrees, and implementation/validation of backend, data, and integration work | Agent completion claims require source, test, CI, and integration evidence |
| **Codex (OpenAI)** | Local setup; architecture review; the independent Geology pilot; Research Presets and other frontend work; debugging, tests, documentation, PR preparation, and version synchronization | The separate contributions and historical test outcomes below do not establish authorship of every backend module or scientific validity of all data |
| **Claude / Claude Code (Anthropic)** | UI/UX, branding/favicon iterations, frontend and related code/documentation changes recorded in section 15 and public commit trailers | Preserve the contributor's reported execution limits; a model label in a commit trailer is not independent verification of the model used |
| Other human/AI collaborators | Independent frontend and backend contributions through repository issues and PRs | Authorship of a PR does not imply every design decision was made by an AI |

The team used a **human-directed, AI-assisted development process**, not an autonomous scientific discovery process. AI-generated suggestions, code, reports, and test fixtures were subject to repository checks and human decisions. A green CI result, an agent's statement of completion, a merged PR, and a deployed public service are distinct states.

### Evidence and interpretation

The original Codex log is a first-party record of its local actions, browser checks, and reported tests. The ChatGPT account below reflects the human–assistant collaboration and GitHub operations; the OpenCode account reflects its reported coordination plus artifacts available in the repository. Public PRs and source code provide stronger evidence of committed changes than conversational status messages alone. Not every locally reported test was independently rerun in this disclosure revision.

### SI disclosure requirements and current status

This audit applies the 2026 requirements supplied by the maintainer:

- **Generated images and videos:** a visible **SI-generated** watermark.
- **Generated audio:** acknowledgment in descriptive text **and metadata**.
- **AI-generated or AI-assisted code and data artifacts:** acknowledge the tool and its actual role in descriptive text and metadata, without relabelling source observations as generated data.
- **Generated content:** must not use, contain, or modify NASA branding or design elements.

The [NASA Space Apps GO team response](https://github.com/nasa/spaceapps/discussions/1849) confirms that AI assistance is permitted and states the NASA branding restriction. A [2026 challenge Navigator response](https://github.com/nasa/spaceapps/discussions/1947) also discusses visible image/video watermarks and project disclosure. The [participant FAQs](https://www.spaceappschallenge.org/resources/participant-faqs/) returned HTTP 502 during this audit; the full audio/code/data metadata requirements above come from the maintainer's supplied rules, rather than a successfully retrieved FAQ. The team should retain the applicable official submission guidance when completing its final check.

**Disclosure is not a certification of complete compliance.** This document and the README links provide descriptive acknowledgment. Artifact-level metadata, visible graphic labels, and final presentation/media assets still require the checks in sections 17–18. Authentic NASA/other-provider observations are not AI-generated merely because AI helped write the software that processes them.

---

## 1. Local setup, server operation, and initial verification (Codex)

The human maintainer requested help running the NASA hackathon project locally.

- Prepared a project checkout and started the Vite web frontend and FastAPI backend.
- Used localhost development endpoints, conventionally port 5173 for the web app and port 8000 for the API; FastAPI exposed interactive documentation at `/docs`.
- Checked API health, capabilities, reference locations, and datasets; submitted real searches in a browser and checked that rankings appeared.
- Restarted the backend after later repository updates and checked selected new API responses.
- Restarted the preview during the #165 work when the local service was no longer running.

Execution logs and some draft materials were retained outside the repository, so this public disclosure does not publish local logs or machine-specific locations. See the [web development guide](../apps/web/README.md).

Initial live AOI analysis and complete Windows compatibility were **not** proven by the initial setup checks. Later testing is described in Section 8.

## 2. Codex / Claude Code interoperability discussion

The team asked how the `openai/codex-plugin-cc` project could allow Claude Code to delegate work to Codex while sharing project rules.

Codex explained the plugin's Codex CLI / app-server integration and discussed the documented commands `/codex:rescue`, `/codex:result`, `/codex:review`, `/codex:adversarial-review`, and `/codex:transfer`. It also inspected the existing `AGENTS.md` and suggested referencing that shared rule file from `CLAUDE.md` rather than maintaining conflicting copies.

**Scope limit:** This was an explanation and local inspection, not evidence that the plugin was installed or that cross-agent transfers were run during this project. See the [plugin documentation](https://github.com/openai/codex-plugin-cc/blob/main/README.md).

## 3. Updating branches and coordinating merges

Development was concurrent: the team and multiple assistants submitted and merged changes while Codex maintained a local checkout.

| Recorded synchronization point | Observed result at that point |
| --- | --- |
| Initial updates | Pulled 11 commits to `2904bc4`, restarted the API, and inspected the updated site |
| Later update | Verified the checkout remained at `2904bc4` |
| Before map-language changes | Pulled 15 commits to `2082b07` |
| After PR #129 | Updated to `c404c07` |
| After PR #169 | Returned to `main` at `90d69ea` |
| During #165 | Incorporated concurrently merged backend, data, and interface work |
| Another requested update | Pulled `34bfe12` |
| After PR #180 | Updated to `b56174f` |
| During #163 | Integrated changes including PR #181 and resolved locale-file conflicts |
| Later local sync | Updated to `a4ea735`, then to subsequent changes as recorded in Section 11 |

Merge-related assistance also included checking GitHub status, updating an outdated PR #155 branch, requesting review, performing explicitly requested merges, and adjusting frontend tests after the new six-dimensional API changed what constituted a real search request. For #163, an older test incorrectly treated fetching `/search/features` as a submitted search; it was changed to observe the actual search POST.

The last pull-only operation was not followed by a complete test-suite rerun.

## 4. Localized Earth basemap labels

The team reported that Earth map labels did not follow the selected Chinese or English interface language.

Codex contributed shared basemap handling that prefers Traditional Chinese place names when that locale is selected, falling back to available Chinese/English or transliterations; English mode prefers English or Latin transliteration. The change applied to map, terrain, and satellite styles while preserving attribution and a raster fallback if vector-layer initialization failed.

It verified language toggling, basemap changes, search markers, and AOI selection state in the browser. Reported results included 99 frontend tests for the initial implementation, 16 subsequent i18n tests, and successful typecheck, lint, build, and API-type checks. Global-discovery behavior without an actual index was not fully tested.

**Evidence:** [PR #129](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/129), merged as `c404c07`. Public deployment was not verified at that time.

## 5. Moon / Mars reference selection on a planetary map

### Design iteration and human decisions

The initial prototype introduced separate map-preview controls for Moon and Mars. The human maintainer then clarified that the planetary buttons themselves should open the map, with reference selection occurring directly on that map rather than in an additional dropdown.

The collaboration included an explicit requirements-checking round. A request for an interactive question interface was initially misunderstood as a website feature; the misunderstanding was corrected **before implementation**. The agreed behaviors were:

| Product decision | Human-approved behavior |
| --- | --- |
| Target selector | Display the selected name; select the site on the map |
| Open or reopen the preview | Click the Moon or Mars body button |
| First visit to a body | Wait for explicit selection, rather than choosing a site automatically |
| After selecting a site | Keep the preview open |
| Switching between Moon and Mars | Remember each body's selection during the page session |
| Overlapping reference markers | Zoom into the cluster before requiring individual selection |

The delivered compact preview opens from the body selector; choosing a reference updates the selected name and marker state. The main ranked-results map remains Earth-focused. Browser verification covered close lunar markers, preview collapse/reopen, remembered selections, and a Mars-reference search returning indexed Earth candidates.

**Evidence:** [Issue #153](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/153), [PR #155](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/155), merged as `864ef69`; [screenshot](screenshots/30-planet-map-reference-selection.jpg). The recorded frontend suite passed 130 tests, with required GitHub checks passing. The implementation uses already available reference sites and NASA Trek imagery; it does **not** establish arbitrary Moon/Mars AOI scientific coverage.

## 6. Removing a redundant six-dimension workspace shortcut

A browser annotation asked to remove the "Open six dimensions" shortcut from the comparison toolbar. Codex removed that control and updated the associated test while verifying that the then-existing dedicated six-dimension section still worked and retained its selected reference.

The documented results were 130 passing frontend tests, plus typecheck, lint, and build. A Windows test run without a worker limit timed out; rerunning with two workers succeeded.

**Evidence:** [PR #169](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/169), merged as `90d69ea`; [toolbar screenshot](screenshots/31-comparison-toolbar.png). Later page simplification further reorganized that section.

## 7. Frontend issue work, layout, and integration

Codex reviewed the frontend issue queue and concurrent changes, including #165, #167, #163, #123, #119, and #118. The work used the existing components and APIs instead of introducing a second independent search system.

### #165 — Coarse/fine search within one results map

The team challenged the earlier design that placed global coarse discovery and fine refinement below the search results in a separate large area. Codex moved these concepts into the indexed-results list and Earth map, including distinct scopes, eligibility and snapshot text, candidate selection, appropriate geographic bounds, and backend-driven refinement status.

Important distinctions were retained:

- A 5 km coarse candidate and a roughly 1.98 km / 90 m fine analysis window do not share an interchangeable score.
- Missing global index coverage cannot be presented as a completed global survey.
- Real indexed searches were exercised; unavailable coarse/refinement stages were tested with **explicitly test-only fixtures**, not represented as live scientific coverage.

The recorded result was 131 passing frontend tests, with lint, typecheck, build, API-type checks, and desktop/mobile browser checks.

**Evidence:** [PR #180](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/180), merged as `b56174f`; [before](screenshots/32-search-results-before.jpg), [after](screenshots/33-search-results-after.jpg), [test-fixture mobile view](screenshots/34-search-results-coarse-fixture-mobile.jpg).

### #163 — Restructuring "About this tool, coverage, and limitations"

The original page displayed method details, feature specifications, data provenance, and limitations as a dense block. Codex reorganized it into a scannable coverage summary and three expandable groups: analysis methods, per-feature specifications, and data provenance.

It preserved scientific caveats, source licenses, coverage and AOI limits, coarse/fine differences, missing-value semantics, method versions, and dynamic API content. Checks included keyboard disclosure operation, delayed source responses, desktop/mobile widths, both locales, and light/dark themes. After incorporating newer six-dimensional changes, the recorded frontend test count was 136 passing.

**Evidence:** [PR #183](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/183), merged as `2d35515`; [desktop before](screenshots/35-about-before-desktop.jpg), [desktop after](screenshots/36-about-after-desktop.jpg), [mobile before](screenshots/37-about-before-mobile.jpg), [mobile after](screenshots/38-about-after-mobile.jpg).

### Related work owned elsewhere

- **#167:** Codex inspected Vite/native-config and dependency problems and prepared an isolated workspace; the eventual fix entered through [PR #182](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/182), authored through a separate contribution. This disclosure does not attribute that full implementation to Codex.
- **#118 / #119 / #123:** Codex considered their requirements and integrated concurrent changes but did not independently complete all their acceptance criteria.
- Concurrent Moon/Mars AOI, six-dimensional API, and dataset improvements were incorporated as inputs to UI work; scientific pipeline authorship belongs to their respective PRs.

## 8. Testing, failures, and what the results do not prove

The original development record reports the following *point-in-time* outcomes, not one current all-project test run:

| Work package | Reported local frontend result |
| --- | --- |
| Initial map-language revision | 99 tests passed; later 16 i18n tests passed |
| Initial planetary preview | 101 tests passed |
| Planetary selection / toolbar | 130 tests passed |
| #165 results integration | 131 tests passed |
| #163 initial layout | 132 tests passed |
| #163 after six-dimensional integration | 136 tests passed |
| #187 simplified results | 114 tests initially; 118 after syncing concurrent changes |

Required GitHub checks were reported successful before the merges of #129, #155, #169, #180, and #183. Local `scripts/verify.sh` runs sometimes failed on Windows because of an unavailable Python `resource` module or temporary-directory permissions; those runs were **not** called complete passes. Git Bash and separate temporary locations were used for the checks that could run.

Browser work covered locale changes, map styles, site selection, AOI drawing, selected-result details, mobile control layouts, and disclosure keyboard access. Missing real coarse coverage was tested with marked fixtures. A passing frontend test suite does not prove all planetary datasets exist, that all six dimensions are scoreable, or that a new commit has been deployed.

## 9. Codex contribution index

| Issue or delivery | PR | Merged commit | Main contribution |
| --- | --- | --- | --- |
| #41 / #42 independent Geology pilot | [#57](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/57) | `47505f2` | Evidence models, map overlays, matching, checksummed snapshot, tests, and OpenCode handoff |
| #130 Research Presets | [#134](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/134) | `1056f0f` | D01 research weights, capability checks, bilingual controls, tests, and methodology |
| Earth label localization | [#129](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/129) | `c404c07` | Locale-aware basemap text and fallback |
| #153 | [#155](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/155) | `864ef69` | Moon/Mars preview, selection and state |
| #168 | [#169](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/169) | `90d69ea` | Removed redundant toolbar shortcut |
| #165 | [#180](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/180) | `b56174f` | Coarse/fine presentation in one results area |
| #163 | [#183](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/183) | `2d35515` | Responsive coverage and limitations layout |

These are traceable implementation records. A commit's author, reviewer, and merger may be different participants; this table combines the original Codex log with the additional self-audit in section 16, not exclusive code authorship.

## 10. Why the original collaboration log was published

The team requested a public record of work for which Codex had been consulted, covering the entire observed collaboration rather than only the final PR. The log was renamed `docs/AI_DISCLOSURE.md` to make the assistance visible to other contributors.

This English revision retains that chronological account while disclosing other AI-assisted planning, coding coordination, and review. It does not publish hidden reasoning, private prompts, private agent logs, credentials, or personal identifiers.

## 11. Subsequent UI simplification (#187)

After another synchronization, the team asked to remove several items from the results page rather than simply hide them:

- The expanded-search control, associated scope/candidate/snapshot captions, and not-ready text.
- A second ranking summary below the map.
- A placeholder asking users to inspect a candidate before selecting one.

The result focused the search page on the map's own Top N ranking and selected-candidate details. Redundant `GlobalPanel` branches and styles were removed; tests addressed searches, empty states, test-fixture labels, coordinates, selected results, language changes, and mobile rendering.

The original development record reports 114 frontend tests passing, then 118 after integrating a concurrently merged filtering change. The previous Codex log was included in this same documentation work.

**Evidence:** [Issue #187](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/187), [PR #189](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/189), merged on 15 November 2026; [desktop screenshot](screenshots/39-simplified-results-desktop.jpg) and [mobile screenshot](screenshots/40-simplified-results-mobile.jpg).

---

提交期間 main 另合併 PR #186 的篩選功能，本分支同步後沿用新版的測試輔助函式，118 項前端測試通過。UI 刪除與這份文件由 PR #189 一併交付。[PR #189](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/189)

## 12. ChatGPT: product definition, critique, GitHub coordination, and review

ChatGPT was used as a conversational technical collaborator alongside the coding environments. The human maintainer repeatedly challenged its recommendations, refined product requirements, and asked it to make concrete GitHub changes. Important examples include the following.

### Additional ChatGPT web usage reported by the maintainer

The following is the maintainer's account of ChatGPT use in the **web application**, supplemented where possible by committed artifacts. It is not an assertion that private conversations were independently recovered from Git history.

- **Challenge analysis and ideation:** reading, translating, and interpreting challenge and evaluation requirements; discussing planetary analog science, research value and limitations; proposing product directions, user scenarios, interactions, six environmental dimensions, candidate indicators and sources, and cross-body feasibility. Name brainstorming included the adopted name **AnalogX**; the maintainer reports this discussion, while Claude's later branding implementation is separately recorded in section 15.
- **Research and matching methodology:** finding and organizing NASA research programs, scientific sources, and field-testing examples; mapping research needs to environmental indicators; discussing weighted normalized Euclidean distance, feature/dimension weights, interpretation, source compatibility, resolution, missing observations, and validation. These discussions are design assistance, not evidence of a validated scientific model.
- **Research-informed Weighting:** helping design three initial presets—**Rover Mobility & Autonomous Navigation**, **Precision Landing & Hazard Avoidance**, and **Geological Fieldwork & Sampling**—using NASA ERNEST, SPLICE, and Artemis JETT / D-RATS as research-context references. Importance ratings are project-designed heuristics, **not NASA official weights or experimentally validated optima**. Ratings are divided by two for the existing sliders; normalization in the existing distance formula preserves their relative proportions. [Methodology and NASA references](SEARCH_METHOD.md#research-informed-weight-presets-custom-mode) and [PR #134](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/134) document the implemented D01 feature. The preset does not certify mobility/landing safety or full geological similarity; it does not create scores for D02–D06. Later separately eligible multidimensional search features are not a result of these preset weights.
- **Development assistance:** reading repository material and code to explain architecture, APIs, frontend behavior, and data flow; writing/refining prompts for Codex; discussing preset interactions, state, and tests; offering Git, local environment, and troubleshooting advice. Advice and prompts are distinguished from the coding agent's execution.
- **External interpretation of exported results:** using AnalogX structured JSON outside the product to interpret terrain, land cover, distances, provenance, and limitations; rechecking the weighted distance and feature contributions; identifying all-zero search targets and discussing possible input/export mistakes and corrections. The existing [ContextExport component](../apps/web/src/components/ContextExport.tsx) copies/downloads structured context. The reported ChatGPT interpretation was AI-assisted analysis of existing values, **not generation of raw observations**, and does not establish the cause of a bug without reproduction. It is not evidence of an integrated runtime AI analyst.
- **UI/UX, presentation, and submission:** proposing interactive maps, visualization, research-led search, and site comparison; planning a seven-page demo narrative, demonstration order, and cases; drafting/revising English titles, subtitles, slogans, and presentation copy; discussing research applications, limitations, future development, submission requirements, SI disclosure, and source attribution. These include both adopted designs and unimplemented ideas. No completed presentation deck is established by this planning account; the final team-owned deck and wording require confirmation.

### From a point-oriented demo to an area-oriented product

The team clarified that the central object is an **Area of Interest (AOI)**: a user draws a region on Earth, Moon, or Mars, examines actual area-supported measurements, and searches for similar **Earth regions**. A selected map marker or the center of a rectangle is not equivalent to aggregating measurements across the rectangle.

ChatGPT reviewed this distinction against existing code and issues. The resulting acceptance criteria emphasized requested geometry, coordinate conventions, data footprint, source resolution, valid coverage fraction, uncertainties, and explicitly missing observations. It highlighted that a global visual basemap does not imply a global scientific raster index.

### Making the six dimensions scientifically honest

The team asked why only terrain controls were available and whether an arbitrary planetary selection could automatically populate search values. ChatGPT separated three concepts:

- **Target:** the desired measured or modeled feature value, used for similarity when eligible.
- **Range:** a hard inclusion filter for candidates; it should normally remain unrestricted when an AOI first populates targets.
- **Weight:** the strength of a feature or dimension within a legitimate similarity calculation; a zero weight does not automatically exclude candidates.

It proposed concise, accessible information icons for these controls and supported dynamic control types based on the backend feature eligibility registry. D01 terrain and eligible Earth D06 SoilGrids properties can participate in scoring; D02–D05 evidence or context cannot silently become weighted scores. Earth-only WorldCover land cover remains a separate hard filter, **not a seventh planetary dimension**. These distinctions informed discussion and updates to [#119](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/119) and [#160](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/160).

### Search layout and information hierarchy

ChatGPT helped evaluate whether coarse discovery and fine refinement deserved a separate UI section. The recommendation was to treat them as **stages of one search**, shown alongside the existing ranked list and map, with separate candidate and final-result semantics. A dedicated [#165](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/165) issue documented the desired UI and acceptance tests.

The team also asked to simplify the dense "About this tool, coverage, and limitations" area and to prioritize visual hierarchy over a long, undifferentiated block. This informed [#163](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/163).

### Reproducible debugging and task organization

ChatGPT reviewed reported local development errors: a missing MapLibre CSS module after an incomplete dependency installation; a subsequent `npm ci` that resolved that import; a Vite extensionless-import warning; and API proxy errors when nothing listened on `127.0.0.1:8000`. It distinguished fatal errors from warnings and created/updated [#167](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/167) with reproduction details, clean-install checks, and the later proxy diagnosis. No claim was made that ChatGPT itself ran the user's local web server.

It also added `frontend` and `backend` GitHub labels, clarified dependencies between API, UI, and data work, and drafted implementation instructions for OpenCode. The instructions consistently called for true validation rather than declaring work finished from the existence of an agent session or a passing fixture test.

### Reviewing and merging backend PRs

At the maintainer's explicit request, ChatGPT inspected [PR #166](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/166) (AOI-first six-dimensional backend) and [PR #161](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/161) (bounded real-data ingest). It checked PR status and CI, identified an accidental machine-specific `compose.override.yaml` in #161, removed that file from the PR branch, and added an ignore rule. It then merged both PRs through GitHub after their relevant checks succeeded, and separately noted that post-merge integration and deployment require their own verification.

ChatGPT contributed **requirements, review, GitHub actions, and this English disclosure revision**. It did not author the complete planetary raster pipeline, collect the underlying scientific observations, or independently certify global six-dimensional coverage.

---

## 13. OpenCode: multiple agents, native Goals, and scientific-data development

OpenCode was used as the coding and orchestration environment for extensive backend and data-pipeline work. The project used separate Git branches/worktrees, primary coding sessions, native `/goal` invocations, specialized subagents, an integration owner, and CI/acceptance gates.

### Phase 2: parallel dimension workstreams

The documented [Phase 2 acceptance report](phase2/ACCEPTANCE.md) and [workstream board](phase2/WORKSTREAM_BOARD.md) describe parallel work on D01 terrain, D02 geology/minerals, D03 thermal data, D04 water/ice, D05 illumination/radiation, D06 surface materials, and backend integration.

Reported verification distinguished an actual native Goal from a merely open or idle session. The project implemented a monitor with durable state, event updates, reconciliation, and completion notifications. The workstreams produced source inventories, coverage matrices, bounded data ingests, tests, OpenAPI contracts, and research limitations.

Representative GitHub records include [#135–#142](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/142), [PR #152](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/152), and [PR #154](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/154).

### P0: AOI-first six-dimensional API and source-grounded ingest

The project prioritized the useful end-to-end contract before complete planetary coverage. [PR #166](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/166) delivered an AOI-first coverage query, legitimate D01/D06 dimension and feature weights, D02–D05 evidence queries, and WorldCover regression behavior. [PR #161](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/161) expanded bounded real data and provenance.

The Phase 2 record reports a local snapshot with 2,185 typed observations, 113 Earth fine terrain candidates, 18 Moon references, and 7 Mars references. Those are **bounded research records**, not whole-planet measurements; source versions and the state of any deployed database must be checked independently. Missing observations and scientifically incompatible features must not be imputed as zero or promoted to a cross-body score.

### Moving from pilot locations toward arbitrary planetary AOIs

The human maintainer repeatedly emphasized that a handful of Moon/Mars reference windows cannot satisfy the intended product. OpenCode agents were directed to work concurrently on [#170](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/170), separating:

- Moon global LOLA DEM access and full-bbox statistics;
- Mars global MOLA DEM access and full-bbox statistics;
- shared raster source/cache infrastructure;
- planetary AOI coordinate and backend contracts;
- D02–D06 spatial evidence and legitimate physical measurements;
- cross-body comparability gates; and
- end-to-end acceptance and data quality.

These were parallel workstreams, not evidence that every geographic location or all six dimensions had already been validated. Related examples include [PR #177](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/177), [PR #179](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/179), [PR #176](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/176), and the integration/contract work in [PR #178](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/178). Individual PR status and integration evidence, rather than this document, determine current availability.

### An observed multi-agent coordination failure

Concurrent subagents were reported editing the same raster-cache file in the same worktree, overwriting one design with another. The two implementations favored different cache-key, locking, checkpoint, and resource-limiting approaches. ChatGPT recommended preserving both versions before deciding, assigning a single file owner, adopting the more reusable shared foundation where justified, porting missing requirements, and testing the combined API.

This incident illustrates a real trade-off: **more agents do not automatically produce faster, safer development**. Effective parallelism requires isolated worktrees for independently owned tasks, explicit ownership of shared files, small integration interfaces, and immediate conflict detection. This document does not claim that a suggested resolution was executed unless a subsequent commit or test confirms it.

### Delivery boundaries

OpenCode could implement and locally validate code, but a locally successful native Goal was not treated as proof of deployment. GitHub CI, merge state, production data version, authorized rollout, and rollback readiness were tracked separately. The project did not treat missing lunar/martian regolith properties, indirect ice proxies, or incomplete radiation observations as measured facts.

---

## 14. Scientific integrity, privacy, and limitations

### Scientific integrity

- Earth, Moon, and Mars share some *physical quantities*, but not automatically comparable observational methods, spatial resolutions, observation times, depths, or uncertainties.
- D01–D06 describes an organizational framework for evidence. It is **not** a claim that all six dimensions are available and scoreable everywhere.
- Raster map tiles are for visualization. A planetary basemap is not itself an indexed global science dataset.
- Candidate counts and published observation totals describe specific data snapshots, not a census of all potentially relevant areas.
- Real observations, model estimates, contextual proxies, and explicitly missing data must remain distinguishable.
- Similarity ranking within the indexed Earth sample is exploratory and should not be construed as a validated landing-site or permanent-base recommendation.

See [data sources](DATA_SOURCES.md), [search methodology](SEARCH_METHOD.md), [multidimensional search](MULTIDIMENSIONAL_SEARCH.md), and the [Phase 2 acceptance report](phase2/ACCEPTANCE.md).

### Disclosure and privacy boundaries

This account intentionally omits private names, personal email addresses, precise local filesystem paths, full conversation histories, private agent messages, session IDs, credentials, and server-internal logs. Public issue/PR references and reproducible technical facts are kept. The names **ChatGPT**, **OpenCode**, and **Codex** identify assistance tools, not additional human members or endorsements by their vendors.

A disclosed AI contribution does not transfer accountability from the project team to an AI provider. Human reviewers remain responsible for deciding whether software is fit for use, scientifically justified, legally distributable, and ready to deploy.

### Scope of this revision

This is a translated and expanded account of the collaboration **through 15 November 2026**, not a live status dashboard. The original Codex portions retain their point-in-time test evidence; the ChatGPT/OpenCode additions describe observed collaboration and link to independently inspectable repository artifacts. The earlier English disclosure revision was prepared using ChatGPT and submitted through the connected GitHub workflow at the maintainer's request. The subsequent SI audit in sections 16–18 was prepared using Codex from repository/history evidence, its confirmable prior work, and the maintainer's ChatGPT account. Preparing this audit does not imply it has been committed, published, approved, or that outstanding compliance work has been completed.

---

## 15. Claude（claude.ai 上的 Claude Code 工作階段）參與的工作

以下記錄同一天另一段由 Claude 協助的工作。Claude 在 claude.ai 的雲端工作階段執行，透過 Claude 桌面 app 連到使用者電腦上的 repo 資料夾修改程式，並用內建瀏覽器操作本機 `http://localhost:5173` 與公開 API（`api.貓熊.台灣`）確認畫面。提交時作者為使用者帳號，提交訊息附 `Co-Authored-By: Claude`。

### 工作方式與限制

- Claude 無法從這個環境推送到 GitHub：使用者電腦端的 shell 連 GitHub 回 HTTP 403，雲端端則因 repo 未安裝 Claude GitHub App 而被拒。每個分支都由使用者在本機 `git push` 後開 PR 與合併。
- 該環境無法安裝 npm 套件，vitest 因缺少原生套件無法執行。Claude 每次修改只跑過 `tsc --noEmit` 與 `eslint`，前端測試請使用者以 `make test-frontend` 確認；本節不記錄任何測試通過數。
- 套用 `design-taste-frontend`（taste-skill）作為介面設計準則，該 skill 檔案沒有提交進 repo。
- 測試精篩介面時，曾在公開 API 實際執行一次 90 m 分析（Sahara 一個窗格），伺服器上因此多一筆隨選 AOI 紀錄。

### 交付並合併的項目

| 請求 | PR | 內容 |
| --- | --- | --- |
| 依 taste-skill 重做介面 | #120 | 移除多數小字、比較維度改成左側清單＋右側細項、權重預設 0、權重列位置固定、去除卡片框改用細分隔線、新增月球／火星地圖檢視 |
| 網站更名與 favicon | #124 | 網站名稱改為 AnalogX；依使用者回饋多次重畫標誌，最後為線條風格的行星與環、不含字母的 favicon，並放在標題前 |
| #123 單一地圖工作流程 | #126 | 移除四個頂層頁籤，改為以地圖為中心的單頁；地球地圖可框選區域並帶入 AOI 分析；月球／火星標示僅供瀏覽 |
| 比對目標與研究情境的編排 | #171 | 「比對目標」與「研究情境」分成兩行；研究情境改成一顆按鈕＋分組選單；放大月球／火星選點地圖並把分隔線移到地圖下方 |
| D02–D06 放進比較維度 | #181 | 讀取 `/search/features` 特徵登錄表；登錄表允許比對的 D06 SoilGrids 20 項提供與 D01 相同的權重／目標／範圍，使用時改走 `/search/multidimensional`；表土依深度切換顯示；D02–D05 列出觀測特徵、天體、筆數與數值範圍 |
| 移除兩個區塊 | #184 | 移除「分析新區域」與「六維比較與觀測」區塊及其專用的框選工具、AOI 目標帶入與六維捷徑，並刪除只測這兩個區塊的整合測試 |

#181 與 #184 合併時的衝突解決提交（`01a8d12`、`234dbc4`）不是 Claude 做的。

### 做了但沒有合併的項目

- 「更多分析工具」區塊的分區、刪小字、結果視覺化、全球粗篩候選畫到主地圖等一系列修改，放在分支 `feat/ui-section-clarity`，使用者後來要求整個刪除該分支，沒有進入 main。
- 介面說明與設計提案（例如研究情境編排的示意圖、D02–D05 為何無法比對的說明）只在對話中提供。

### 已知界線

- D02–D05 目前沒有比對控制項，是因為後端登錄表把這些特徵標為只能當證據；前端沒有替它們虛構可計分的控制。
- 多維搜尋不接受土地覆蓋篩選，介面在兩者同時使用時會拒絕送出並說明，不會靜默忽略篩選。
- 結果詳細資料的逐項比較表當時只列 D01 指標，表土的比對數值未加入。

---

## 16. Additional Codex self-audit: architecture, Geology, and Research Presets

This section adds a separate, confirmable Codex work history to the original frontend log. Evidence includes the merged PRs, the files they introduced, retained review/handoff documents, and Codex's execution record. Current files may contain later changes by other agents; the cited PRs delimit the contribution.

| Work area | How Codex participated | Evidence and attribution boundary |
| --- | --- | --- |
| Architecture and technical planning | Inspected actual branches, dirty/untracked files, contracts, scientific modules and issues; reviewed the proposed canonical-observation / derived-search-index separation, SQLite/DuckDB responsibilities, spatial support, and snapshot design; prepared integration and handoff recommendations | [PR #54 review](PR54_REVIEW.md), [OpenCode handoff](OPENCODE_HANDOFF.md), [Geology integration plan](../pipelines/geology/INTEGRATION_PLAN.md). Reviewing the backend foundation in [PR #54](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/54) is not a claim that this Codex session authored that implementation |
| Scientific acquisition and processing | Wrote and ran bounded source checks, download/cache handling, planetary-CRS map overlays, evidence classification, and provenance/quality reporting during the earlier authorized Geology task; documented unavailable sources rather than inventing observations | [PR #57](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/57), [Geology module](../pipelines/src/analog_pipelines/geology/), [pilot README](../pipelines/geology/README.md). Source maps and spectra were produced by their providers; AI assisted the code and interpretation. The later handoff and this disclosure audit did not download scientific datasets |
| Data management and reproducibility | Developed independent schemas, taxonomy, catalog/manifests, geometry fingerprints, checksummed immutable snapshot validation, and a local raw/cache artifact inventory; preserved existing local work and documented which files were not remotely backed up | [Geology models](../pipelines/src/analog_pipelines/geology/models.py), [snapshot/catalog documentation](../pipelines/geology/README.md), [handoff](OPENCODE_HANDOFF.md). The initial pilot did not replace the shared registry, application SQLite schema, API contract, or terrain snapshot |
| Matching and feature interpretation | Implemented category/evidence matching and mineral comparison gates that respect units, detection states, uncertainty and compatible semantics; tested null/invalid values and planetary spatial support | [Geology matching](../pipelines/src/analog_pipelines/geology/matching.py), [mineral processing](../pipelines/src/analog_pipelines/geology/mineralogy.py), [validation record](../pipelines/geology/VALIDATION.md). Band depth/spectral indices were not converted into mineral weight percent, and terrain Euclidean distance was not substituted for geological evidence |
| Backend/API and database review | Checked API/schema compatibility, v1-to-v2 conversion, stable spatial identifiers, coverage labels, and SQLite rebuild safeguards; recorded findings for the backend owner | [PR54_REVIEW.md](PR54_REVIEW.md). The broader backend, API, typed database, Parquet/DuckDB and six-dimensional integration work has separate PRs and OpenCode/contributor records; it is not all attributed to Codex here |
| Research Presets and frontend | Turned the supplied research ratings into centrally managed configuration; added bilingual, capability-aware controls; preserved targets, filters and limits; handled manual edits/reset and the independent AOI target workflow; adjusted responsive placement after human feedback | [PR #134](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/134), [preset definitions](../apps/web/src/lib/research-presets.ts), [SearchForm](../apps/web/src/components/SearchForm.tsx), [methodology](SEARCH_METHOD.md), [feature documentation](FEATURE_REGISTRY.md). NASA references motivate the research scenarios, not the numerical importance ratings. Later UI changes are separately attributed in section 15 |
| Testing, debugging and validation | Wrote small software fixtures and regression tests, ran the project gate and focused checks, investigated failures, reconciled main-branch changes, and captured real browser UI screenshots | PR #57 records 51 focused Geology/GIS tests and a 14-step full-gate pass; PR #134 records 15 preset tests, 128 frontend tests in its full gate, and an isolated API smoke pass. These are historical outcomes at the PR revisions, not claims about every later revision or scientific validation. [Screenshot capture and reproduction notes](screenshots/research-presets/README.md) distinguish browser captures from generated imagery |
| Delivery, CI/CD and documentation | Prepared separate reviewable branches/PRs, inspected CI, documented reproduction/source limitations and integration tasks, and performed explicitly authorized GitHub actions; helped write English/Traditional Chinese UI text and technical documentation | PR #57 and PR #134 plus the original frontend log above. This Geology/Presets work did not upload R2 objects, configure Cloudflare Tunnel, or deploy production services; infrastructure instructions were handoff material. Earlier local server/browser checks are recorded separately |

### Historical scientific outcome of the independent Geology milestone

At PR #57, the shareable geological-map pilot covered **one Moon ROI and two explicitly marked Mars test AOIs**. Earth GLiM results were withheld from redistribution because the mirror's licensing was unresolved. There were **no validated mineral detections** in that milestone: EMIT remained `auth_required` at the maintainer's instruction, the lunar research product lacked sufficiently verified georeferencing/units/quality, and CRISM indices remained context only. A zero count of verified mineral observations meant **data were not yet usable**, not that the minerals were absent. Later ingest work and observations must be assessed using their own versioned provenance, not this historical count.

### Other agents and evidence strength

The existing Claude account is retained in section 15. [PR #196](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/196) explicitly identifies Claude Code assistance for that disclosure contribution. Git history also contains `Co-Authored-By: Claude` trailers for branding/UI work and earlier contributions such as [PR #12](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/12) (i18n) and [PR #23](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/23) (terrain range filters). Those are evidence of disclosed assistance, not proof of which portions of each file were generated or of the exact model runtime.

OpenCode's orchestration and implementation record remains in section 13 and the linked acceptance/workstream documents. OpenCode is an agent environment, not a uniquely identified underlying language model. The team still needs to confirm provider/model names and any additional LLMs or generative tools; configuration, interoperability discussions, and an agent's completion message alone do not establish actual use. No claim is made here that every repository file was AI-authored or that all generated output received a completed independent human review.

---

## 17. Content and scientific-data classification audit

The inspected baseline is `main` commit `bac0eaa` (including the existing disclosure from PR #196). This is a tracked-repository audit plus the maintainer's reported external ChatGPT use. It does not inspect private design tools, untracked assets on other machines, or the final submission portal.

| Classification | Examples in AnalogX | What the SI disclosure means |
| --- | --- | --- |
| **Original provider scientific observations and source products** | NASA/USGS/ESA and other cited source rasters, spectra, geological maps, and associated metadata; see [data sources](DATA_SOURCES.md) and [source inventory](../catalog/dimension-source-inventory.yaml) | Retain provider, version, license, methods and uncertainty. Some source products already involve expert interpretation or retrieval algorithms; do not call every map a direct sensor measurement. No evidence here establishes that the team generated the original scientific observations with an LLM |
| **Deterministically processed scientific data** | Raster-window terrain statistics, map-overlap fractions, validated typed exports, search distances, derived indexes and checksummed manifests produced from documented inputs | AI-assisted processing code is disclosed as code assistance. Its output remains a reproducible transformation of its sources, not an invented LLM observation. Determinism and checksums alone do not prove a scientifically appropriate method |
| **External model-derived scientific products** | ISRIC SoilGrids 2.0 predictions and uncertainty summaries; provider-classified land cover; modeled geometry/proxies where documented | Preserve model/prediction/proxy labels and provider provenance, separately from this team's generative-AI use. [SoilGrids pilot documentation](D06_SOILGRIDS_DEPTH_PILOT.md) explicitly calls its values predictions, not measurements; its historical eligibility is not a statement of the current feature registry |
| **Synthetic test fixtures** | Small mocked rasters/observations, known-value examples, offline test records, and synthetic performance benchmarks; the [search-method worked example](SEARCH_METHOD.md#worked-example-synthetic-test-fixture-not-a-scientific-observation) is explicitly synthetic | AI may assist fixture/test authoring. Synthetic means constructed for software verification; it does not automatically mean an LLM generated the values. Never promote these fixtures to real observed records or use them to establish scientific comparability/coverage |
| **AI-assisted code, documentation and analysis** | The contributions above, this disclosure, translations, test/capture scripts, and external ChatGPT analysis of exported JSON | Disclose tool, role and artifact scope. Human-authored requirements, generated drafts, accepted code, deterministic results, and conversational interpretations are distinct. Rechecking a distance is analysis, not generation of the source data |
| **AI-generated content and AI-assisted presentation/design** | Generated code/documentation drafts; AI-assisted product copy and branding work; user-reported titles, slogans and demo planning; the favicon discussed below | Inventory the final accepted text/visual assets, confirm tools and extent of generation, and apply the appropriate descriptive, metadata and visible-media acknowledgment. Ideas or draft storyboards are not delivered features or a completed deck |

### Tracked visual and audiovisual assets

The baseline contains 46 tracked PNG/JPEG/WebP/SVG/GIF image assets. A simple scan for C2PA or literal `SI-generated` markers identified the favicon only; absence of those markers does not establish human authorship, absence of generation, or absence of a watermark rendered as pixels/paths.

- **Favicon:** [apps/web/public/favicon.svg](../apps/web/public/favicon.svg) is a code-drawn planet/ring mark with Claude-assisted revision history, most recently commit `276aea8`. Its embedded C2PA payload contains an Anthropic/Claude provision action and an **unknown origin-confidence** assertion. This indicates declared tool involvement, not proof of exclusively AI-generated artwork or a validated signature/complete editing history. No visible **SI-generated** label was identified in the inspected SVG. Metadata does not substitute for a visible watermark. Treat this as an unresolved AI-assisted/generated graphic-labeling item, not evidence that the media audit is complete.
- **Architecture diagrams:** [English](diagrams/architecture.en.svg) and [Traditional Chinese](diagrams/architecture.zh-TW.svg) vector diagrams entered through commit `77dd7f6` / PR #8. This audit does not establish which tool generated their graphics; confirm with the contributor before classifying them. An SVG extension alone does not prove or exclude AI generation.
- **Screenshots:** tracked PNG/JPEG assets include browser captures in [docs/screenshots](screenshots/). The [Research Presets capture source and notes](screenshots/research-presets/README.md) establish automated Chromium capture, rather than text-to-image synthesis. A screen capture is not automatically a generative image; embedded AI-assisted artwork/copy and any synthetic test data still need their own truthful attribution. Other screenshots require contributor confirmation where capture provenance is absent.
- **Video, audio, and presentation files:** no tracked MP4/WebM/MOV, MP3/WAV/OGG/FLAC, PPTX/Keynote, or PDF files were found at the inspected baseline. This does **not** establish that external demo materials, narration, music, slides, or unpublished assets do not use AI. The maintainer reports ChatGPT-assisted demo planning and copy, not a repository-hosted final deck.

No branding clearance is claimed. The final generated visuals and any composites must be reviewed for NASA logos, flags, mission identifiers and design elements. Authentic provider imagery and proper source credits must remain distinguishable from generated artwork; attribution does not itself grant permission or cure a prohibited generated use.

---

## 18. Submission labeling and remaining team checks

| Requirement | Observed evidence | Remaining action / status |
| --- | --- | --- |
| Descriptive disclosure for code, documentation, analysis and data processing | This document identifies tools, uses, artifacts, scientific-data classes and limitations; both README languages link here. Some commits/PRs also disclose AI co-authorship | **Present at project level.** Include the relevant acknowledgment/link in the submitted project description and distributed artifact descriptions; confirm actual published copies |
| Code/data metadata acknowledgment | Scientific manifests already carry source/method/checksum provenance; some commits have AI trailers. The favicon has separate C2PA metadata | **Incomplete / not established consistently.** Scientific provenance or Git trailers alone do not demonstrate SI metadata coverage for every distributed code/data bundle. Add release/artifact metadata identifying the AI-assisted role without replacing provider provenance |
| Visible SI-generated label on generated images/video | Claude-assisted favicon and diagrams/screenshots inventoried above; a visible favicon label was not identified | **Open.** Confirm asset classification and add a visible SI-generated watermark wherever required, including final generated imagery/video. Embedded C2PA is insufficient on its own. Image modifications are outside this documentation-only update |
| Audio acknowledgment in descriptive text and metadata | No tracked audio found; external narration/music/tool use unconfirmed | **Awaiting team confirmation.** If AI audio is used, disclose the tool and role in both places and verify the exported file's actual metadata |
| Generated text/product/presentation content | ChatGPT copy/translation/planning and AI-assisted UI/documentation work are disclosed; final deck and submission text are not available here | **Partially inventoried.** Confirm which drafts were adopted and carry the disclosure into final presentation/project materials and applicable file metadata. Do not label proposed features as delivered |
| NASA branding/design restriction | Rule recorded; an original planet/ring mark is documented, but final media/composites are not comprehensively reviewed | **Open final review.** Check generated assets and submission materials against the applicable NASA guidance; do not claim clearance from this audit |
| Other tools and human accountability | Claude contributor statement, Codex execution/PR history, ChatGPT maintainer account, OpenCode workstream artifacts | **Confirm gaps.** Record the underlying OpenCode model/provider, additional tools, final asset owners, license checks, and actual human acceptance/review; do not infer these from CI alone |

For a future release, a small versioned sidecar or existing extensible release metadata could record **artifact path/glob and checksum, tool/provider (model when confirmed), assistance task, data classification, original dataset/version and method references, review status, and this disclosure's version/link**. This is a recommendation, not an implemented schema or a claim that metadata has already been embedded. Preserve immutable scientific manifests and checksums; publish a new version or separate acknowledgment rather than silently editing pinned artifacts. Do not mark NASA observations as LLM-generated to satisfy an acknowledgment requirement.

Before submission, the team should check off the open items above against the actual exported media, downloadable code/data, final demo and project-page text, retain the applicable official rules, and confirm that visible labels and metadata survive export. This documentation revision changes no scientific observations, algorithms, asset watermarks, API contracts, deployment, or production data.

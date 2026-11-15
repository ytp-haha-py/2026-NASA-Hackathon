# Phase 2 workstream board (Issue #142)

Machine-readable companion: [`workstreams.json`](workstreams.json).

Status snapshot: **2026-11-15**, coordinator session `ses_e253e85e9ffeLjB1ipw0gIVen0`
(goal `a1caf80d-100c-4491-a301-cdf5020f0a69`). This board is the coordinator's source of truth for
worktree/session/goal ownership. It does not replace each Issue's acceptance criteria.

## Workstreams

| Issue | Worktree | Branch | Session ID | Goal status | Subagents | PR | Current blocker |
|---|---|---|---|---|---|---|---|
| #142 Program coordinator | `/home/panda/.local/share/opencode/worktree/204e7a/phase2-coordinator-142` | `phase2/coordinator-142` | `ses_e253e85e9ffeLjB1ipw0gIVen0` | running (`a1caf80d-100c-4491-a301-cdf5020f0a69`) | coordinator + ad-hoc | pending | none |
| #135 D01 terrain | `.../phase2-135-d01` | `phase2/135-d01` | `ses_e20fc8691ffeaMvQ1pjnNWscZp` | running (`7433f495-d3a0-4b1c-9534-0e372fdd4f71`) | delegated inside Goal | pending | shares #108/#97 scope; must coordinate |
| #136 D02 geology | `.../phase2-136-d02` | `phase2/136-d02` | `ses_e20fc69adffe9JAGca55A3aX5R` | running (`8c39f2b9-c93a-48ec-9458-d2ec3f2d1d84`) | delegated inside Goal | pending | taxonomy/crosswalk validation |
| #137 D03 thermal | `.../phase2-137-d03` | `phase2/137-d03` | `ses_e20fc6930ffeoPYLni2B8ZosUx` | running (`1f7c814b-5425-4bbc-982f-2ffe007527f0`) | delegated inside Goal | pending | time/local-solar-time support |
| #138 D04 water/ice | `.../phase2-138-d04` | `phase2/138-d04` | `ses_e20fc68bbffe2Sar4coxcaeuS7` | running (`87ae711a-860d-4874-bf8d-f3813a5f45b5`) | delegated inside Goal | pending | proxy vs confirmed ice separation |
| #139 D05 illumination | `.../phase2-139-d05` | `phase2/139-d05` | `ses_e20fc684affe5mrOOQ6dvy5Ju5` | running (`250c8206-32b1-48ed-9a29-6d1f3d536c7a`) | delegated inside Goal | pending | DEM horizon/shielding inputs |
| #140 D06 regolith | `.../phase2-140-d06` | `phase2/140-d06` | `ses_e20fc67c3ffeEHNB8Ra3Q5CLqP` | running (`b54efab7-61f4-4f01-9902-b48b02b99e3e`) | delegated inside Goal | pending | sparse Moon/Mars in-situ data |
| #141 Integration | `.../phase2-141-integration` | `phase2/141-integration` | `ses_e20fc6751ffeXuPBKd6WZyqGcN` | running (`5f5a47ff-310d-44b8-9be5-4b4626abf4d4`) | delegated inside Goal | pending | dimension×feature weighting contract |

`...` expands to `/home/panda/.local/share/opencode/worktree/204e7a/`.

## Preflight facts

- `origin/main` = `ca0fc8f` (`docs(dev): clarify the unset local-first proxy example (#133)`).
- The original checkout `/home/panda/workspace/2026-NASA-Hackathon` is **not** on `main`; it is on
  `docs/116-117-acceptance` at `07df61b` with uncommitted changes. It is not used for Phase 2.
- #132 is active in `/home/panda/workspace/analogx-issue-132` on `backend/issue-132`
  (session `ses_e2111ccf7ffevCZ3c5V62sEAX8`). Do not modify that worktree.
- #97 and #108 are open but have no dedicated active worktree/session. #135 owns D01 coordination.
- #134 is an open frontend PR (`feat/130-research-weight-presets`); not Phase 2 backend scope.
- Local indices: `var/global-coarse-index` 111 MB; `var/analog.db` 7.0 MB; public coarse pilot
  168 cells. `data/global-index` is absent.
- #112/#116/#117 are merged; reuse, do not reimplement.

## Ownership rules

- Each dimension Goal owns only its provider/ingest/manifests/tests/docs/coverage matrix.
- Shared API schemas, OpenAPI, scoring engine, and cross-dimension contracts belong to #141.
- #118/#119/#123 frontend UI is out of scope.
- No Agent pushes to `main`, resets the production DB, or publishes large data without an explicit
  backup/restore plan.
- The coordinator does not edit a dimension worktree while that Goal is active.

## Verification commands

```bash
git worktree list
opencode session list --format json
opencode session export <session-id> | jq '.info.location.directory, .messages[0].metadata'
opencode api get /api/session/active --param directory=<worktree>
```

Expected for every Goal: a distinct worktree, branch, session id, and first-message metadata
`{"source":"goal","goalID":"..."}`. A session without that metadata is not a native Goal.

## Update log

- 2026-11-15: preflight completed; 7 independent worktrees and sessions created; native Goal
  messages posted to #135–#141; all sessions report `running`.

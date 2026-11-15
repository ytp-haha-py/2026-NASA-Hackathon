# Phase 2 workstream board (Issue #142)

Machine-readable companion: [`workstreams.json`](workstreams.json).

Status snapshot: **2026-11-15**, coordinator session `ses_e253e85e9ffeLjB1ipw0gIVen0`
(goal `a1caf80d-100c-4491-a301-cdf5020f0a69`). This board is the coordinator's source of truth for
worktree/session/goal ownership. It does not replace each Issue's acceptance criteria.

## Workstreams

| Issue | Worktree | Branch | Session ID | Native Goal ID | Goal / review | PR | Blocker |
|---|---|---|---|---|---|---|---|
| #142 Program coordinator | .../phase2-coordinator-142 | phase2/coordinator-142 | ses_e253e85e9ffeLjB1ipw0gIVen0 | a1caf80d-100c-4491-a301-cdf5020f0a69 | active / validated | coordinator | this PR | none |
| #135 D01 | .../phase2-135-d01 | phase2/135-d01 | ses_e20fc8691ffeaMvQ1pjnNWscZp | 0199a330-c186-4293-acd6-71762469219d | phase complete / issue open | #147 | additional D01 ingest, #108/#97 |
| #136 D02 | .../phase2-136-d02 | phase2/136-d02 | ses_e20fc69adffe9JAGca55A3aX5R | 8004c26a-8e34-464d-9bfb-8dc31680d2fe | complete / integrated | #148 | none |
| #137 D03 | .../phase2-137-d03 | phase2/137-d03 | ses_e20fc6930ffeoPYLni2B8ZosUx | 0ceab842-708d-4164-81e2-96c4aeb23ca4 | complete / integrated | #146 | none |
| #138 D04 | .../phase2-138-d04 | phase2/138-d04 | ses_e20fc68bbffe2Sar4coxcaeuS7 | 642a1a88-25bc-4515-a3bb-2d153bad8bb1 | complete / integrated | #144 | none |
| #139 D05 | .../phase2-139-d05 | phase2/139-d05 | ses_e20fc684affe5mrOOQ6dvy5Ju5 | 993b3503-c9c2-4a47-a256-76f6e2f35ed9 | complete / integrated | #145 | none |
| #140 D06 | .../phase2-140-d06 | phase2/140-d06 | ses_e20fc67c3ffeEHNB8Ra3Q5CLqP | 96555f26-c85b-448d-8ed1-6c55880da63c | complete / integrated | #150 | none |
| #141 integration | .../phase2-141-integration | phase2/141-integration | ses_e20fc6751ffeXuPBKd6WZyqGcN | 2f312370-3a07-400f-b15a-95cd4cc8d251 | complete / integrated | #152 | none |


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

## Native Goal verification

The native `/goal` command was executed through the supported session command API:

```text
GET  /api/command                  -> {"name":"goal","description":"keep working until an objective is met [pause|resume|clear]"}
POST /api/session/{id}/command     -> {"name":"goal","text":"<Issue objective>"}
```

Each primary session now contains a **synthetic** message with
`metadata={"source":"goal","goalID":"..."}` and text `A goal is now active for this session.`
This distinguishes a native Goal from an ordinary user prompt, even if that prompt also contained
goal-shaped text. #141 was additionally paused/resumed to verify the native manager accepted it.

## External / adjacent workstreams

| Issue | Scope | Worktree / branch | Session / Goal | Status | Rule |
|---|---|---|---|---|---|
| #132 | Earth/Moon/Mars map query + planetary on-demand | `/home/panda/workspace/analogx-issue-132` / `backend/issue-132` | `ses_e2111ccf7ffevCZ3c5V62sEAX8` / `1c5d9b7e-1818-4d19-bd43-ffb52ceb2228` | active external worker | do not modify or interrupt |
| #108 | Earth global Geomorpho coarse index | none | none | open, no active session | D01 #135 coordinates; reuse local `var/global-coarse-index` |
| #97 | persisted coarse-to-fine refinement | none | none | open, no active session | D01 #135 coordinates; no duplicate implementation |

## Monitoring

The coordinator monitor is documented in [`MONITORING.md`](MONITORING.md) and implemented in `tools/phase2_monitor`:

```bash
uv run python -m tools.phase2_monitor.cli run
uv run python -m tools.phase2_monitor.cli once
uv run python -m tools.phase2_monitor.cli status
uv run python -m tools.phase2_monitor.cli events
```

It uses the supported `/api/event` SSE stream for immediate changes and a 90 s reconciliation loop
for missed/crashed events. Goal state is read from OpenCode's read-only `kv` table
(`plugin:%:session:<session-id>`), not inferred from session idle. State is persisted outside all
worktrees at `/home/panda/.local/state/analogx-phase2/monitor.sqlite3`.

## Final acceptance

See [`ACCEPTANCE.md`](ACCEPTANCE.md) for the three-body public acceptance, data snapshot, formulas and limitations. #135–#139 remain open for additional bounded ingest; #140–#142 are closed.

## Update log

- 2026-11-15: preflight completed; 7 independent worktrees and sessions created; native Goal
  messages posted to #135–#141; all sessions report `running`.

- 2026-11-15 19:30: all seven primary sessions confirmed with a delivered (not queued) native `/goal` synthetic message. `GET /api/command` exposes the native `goal` command; `POST /api/session/{id}/command` with `{"name":"goal","text":...}` was used. #137/#138 opened phase PRs #146/#144; #139 opened #145.

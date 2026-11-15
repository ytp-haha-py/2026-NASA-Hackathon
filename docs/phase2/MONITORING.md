# Phase 2 Goal monitoring (Issue #142)

This is the coordinator's operational monitor for native OpenCode Goals. It is event-driven for
latency and reconciled for correctness. It never writes to a Goal worktree or to OpenCode's
database.

## What actually stores Goal state

OpenCode `2.3.0` exposes the native command:

```text
GET /api/command
{"name":"goal","description":"keep working until an objective is met [pause|resume|clear]"}
```

The goal plugin persists each session's state in the OpenCode SQLite database:

```text
table: kv
key:   plugin:<plugin-hex>:session:<session-id>
value: {"id","objective","status","rounds","maxRounds","reason","updatedAt",...}
```

Observed `status` values include `active`, `complete` and `paused`. The monitor reads this table
**read-only** and maps an unreadable/missing row to `unknown`; it never guesses completion from a
session being idle.

The live event source is the supported SSE endpoint:

```text
GET /api/event
Authorization: Basic base64("opencode:" + password from ~/.local/state/opencode/service.json)
```

The monitor subscribes to `session.execution.*`, `session.idle`, `session.synthetic`,
`session.inbox.*`, `session.tool.failed`, `session.retry.scheduled`, `session.created` and
`server.connected`.

## Commands

```bash
# One reconciliation (safe to run manually)
uv run python -m tools.phase2_monitor.cli once

# Print the persisted table without contacting OpenCode
uv run python -m tools.phase2_monitor.cli status

# Inspect the raw SSE stream
uv run python -m tools.phase2_monitor.cli events

# Long-running monitor with SSE + 90s reconciliation
uv run python -m tools.phase2_monitor.cli run --interval 90

# Supervisor with restart-on-crash and a log outside all worktrees
bash scripts/phase2_monitor.sh
```

Optional coordinator wake-up (only important transitions; disabled by default):

```bash
PHASE2_NOTIFY_SESSION=ses_e253e85e9ffeLjB1ipw0gIVen0 bash scripts/phase2_monitor.sh
```

## Persistence

```text
~/.local/state/analogx-phase2/monitor.sqlite3
~/.local/state/analogx-phase2/monitor.log
```

Tables:

- `workstreams` — Issue, dimension, worktree, branch, session, goal ID/objective, goal/session
  status, last activity, commit, PR, tests, blockers, `validated`, `integrated`.
- `events` — deduplicated SSE event IDs and payloads.
- `transitions` — field-level audit trail of Goal/session/test/validation/integration changes.
- `alerts` — durable important-event queue with unique dedupe keys and notification timestamps.

`validated` and `integrated` are deliberately separate from `goal_status=complete`:

| State | Meaning |
|---|---|
| Goal completed | OpenCode's native goal plugin says `complete` |
| Validated | Coordinator checked commits, PR, tests, Coverage Matrix, science and acceptance |
| Integrated | PR merged through the normal review flow |

A completed Goal has `validated=0` until a coordinator review changes it. The monitor never merges,
deploys, resets data, or writes a Goal worktree.

## Event handling guarantees

- SSE reconnect uses exponential backoff (1 s → 30 s).
- A disconnect produces a `monitor.sse.disconnected` event and the next reconciliation repairs
  any missed state.
- Reconciliation runs every 90 s by default and on `server.connected`.
- Events are deduplicated by OpenCode event ID; repeated delivery does not repeat transitions.
- Multiple Goals completing in one batch are processed independently.
- A session that is `idle`/`succeeded` while its Goal row is still `active` is reported as
  `active + idle` and raises a stale warning after 15 min; it is not marked complete.
- Unreadable Goal state is `unknown`, never `complete`.
- Alerts are persisted even when coordinator notification is disabled; `--notify-session` sends one
  consolidated prompt and marks the alerts delivered. If the HTTP wake-up fails, alerts remain
  unread for the next attempt.

## Current real status

Captured from `uv run python -m tools.phase2_monitor.cli status` on 2026-11-15:

| Issue | Goal | Session | PR | Tests | Validated | Integrated | Notes |
|---|---|---|---|---|---|---|---|
| #132 | active | running | #151 | pass | no | no | external worker; do not modify |
| #135 | complete | succeeded | #147 | pending | no | no | D01 phase ready for review |
| #136 | complete | succeeded | #148 | pass | no | yes | D02 matrix merged |
| #137 | complete | succeeded | #146 | pass | no | yes | D03 matrix merged |
| #138 | complete | succeeded | #144 | pass | no | yes | D04 matrix merged |
| #139 | complete | succeeded | #145 | pending | no | no | D05 phase ready for review |
| #140 | complete | succeeded | #150 | pending | no | no | D06 phase ready for review |
| #141 | active | running | #152 | pending | no | no | dual-layer weights/coverage contract |
| #142 | active | running | #143 | pass | no | yes | coordinator board |

`#97` and `#108` have no active Goal/session and are tracked as `not_started` with blockers.

## Tests and observed events

```text
uv run pytest tools/phase2_monitor/test_core.py -q   -> 14 passed
```

Covered: Goal complete (not auto-validated), blocked Goal, interrupted session, idle session with
active Goal, unknown Goal, two simultaneous completions, duplicate event, restart persistence,
merged PR, deduped alerts, notification delivery and SSE disconnect/reconnect.

Observed real SSE events:

```text
{"type":"server.connected","data":{}}
{"type":"session.created","data":{"sessionID":"ses_...","title":"monitor-sse-probe"}}
```

Observed reconciliation log:

```text
phase2 monitor started: ... interval=5s sse=True notify=None
20:03:54 reconciled after server.connected; alerts=10
```

## Remaining manual step

The monitor can persist alerts and optionally send a coordinator prompt. Automatic source-code
validation, PR approval, merge and production deployment remain human/coordinator decisions by
design. OpenCode does not expose a stable public goal-status endpoint; the implementation reads the
documented native plugin state from the local SQLite `kv` table read-only. If a future OpenCode
version changes that key/schema, the monitor marks the Goal `unknown` until the adapter is updated.

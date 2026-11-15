"""Event-driven + reconciled monitor for Phase 2 OpenCode Goals (Issue #142).

OpenCode stores native Goal state in the read-only ``kv`` table of its local database
(``plugin:<hex>:session:<session-id>`` -> JSON with id/objective/status/rounds/reason).
The monitor never writes to that database or to any Goal worktree. It writes its own
SQLite state at ``~/.local/state/analogx-phase2/monitor.sqlite3`` and uses the supported
``/api/event`` SSE stream plus a low-frequency reconciliation loop.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_SERVICE_JSON = Path("~/.local/state/opencode/service.json").expanduser()
DEFAULT_OPENCODE_DB = Path("~/.local/share/opencode/opencode.db").expanduser()
DEFAULT_STATE_DB = Path("~/.local/state/analogx-phase2/monitor.sqlite3").expanduser()
DEFAULT_INTERVAL_S = 90
DEFAULT_STALE_S = 900
DEFAULT_CONFIG = Path("docs/phase2/workstreams.json")
RELEVANT_EVENT_TYPES = {
    "server.connected",
    "session.created",
    "session.deleted",
    "session.execution.started",
    "session.execution.succeeded",
    "session.execution.failed",
    "session.execution.interrupted",
    "session.idle",
    "session.synthetic",
    "session.inbox.enqueued",
    "session.inbox.delivered",
    "session.inbox.cancelled",
    "session.skill.activated",
    "session.tool.failed",
    "session.retry.scheduled",
}

GOAL_ACTIVE = "active"
GOAL_COMPLETE = "complete"
GOAL_PAUSED = "paused"
GOAL_BLOCKED = "blocked"
GOAL_UNKNOWN = "unknown"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ms_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, UTC).isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True, slots=True)
class GoalState:
    session_id: str
    goal_id: str
    objective: str
    status: str
    rounds: int
    max_rounds: int
    reason: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class SessionState:
    session_id: str
    active: bool
    status: str
    outcome: str | None
    directory: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class GitState:
    branch: str
    head: str
    dirty: bool


@dataclass(frozen=True, slots=True)
class CheckSummary:
    passed: int
    failed: int
    pending: int
    status: str


@dataclass(frozen=True, slots=True)
class PullRequestState:
    number: int
    title: str
    head_branch: str
    state: str
    merge_state: str
    url: str
    checks: CheckSummary
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class Workstream:
    issue: int
    dimension: str
    worktree: str | None
    branch: str | None
    session_id: str | None
    expected_goal_id: str | None = None
    scope: str = ""
    blockers: tuple[str, ...] = ()


@dataclass(slots=True)
class ReconcileResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)


def load_workstreams(config_path: Path = DEFAULT_CONFIG) -> tuple[list[Workstream], dict[str, Any]]:
    raw = _load_json(config_path)
    workstreams: list[Workstream] = []
    coordinator = raw.get("coordinator") or {}
    if coordinator.get("session_id"):
        workstreams.append(
            Workstream(
                issue=int(coordinator.get("issue", 142)),
                dimension="coordinator",
                worktree=coordinator.get("worktree"),
                branch=coordinator.get("branch"),
                session_id=coordinator.get("session_id"),
                expected_goal_id=coordinator.get("goal_id"),
                scope="Phase 2 coordination",
            )
        )
    for item in raw.get("workstreams") or []:
        workstreams.append(
            Workstream(
                issue=int(item["issue"]),
                dimension=str(item.get("dimension") or ""),
                worktree=item.get("worktree"),
                branch=item.get("branch"),
                session_id=item.get("session_id"),
                expected_goal_id=item.get("goal_id"),
                scope=str(item.get("scope") or ""),
                blockers=tuple(item.get("blockers") or ()),
            )
        )
    for item in raw.get("external") or []:
        workstreams.append(
            Workstream(
                issue=int(item["issue"]),
                dimension=str(item.get("dimension") or "external"),
                worktree=item.get("worktree"),
                branch=item.get("branch"),
                session_id=item.get("session_id"),
                expected_goal_id=item.get("goal_id"),
                scope=str(item.get("scope") or ""),
                blockers=tuple(item.get("blockers") or ()),
            )
        )
    return workstreams, raw


class OpenCodeSource:
    """Read-only access to OpenCode Goal/session state."""

    def __init__(
        self,
        *,
        service_json: Path = DEFAULT_SERVICE_JSON,
        opencode_db: Path = DEFAULT_OPENCODE_DB,
        timeout_s: float = 10.0,
    ) -> None:
        self.service = _load_json(service_json)
        self.base_url = str(self.service.get("url") or "http://127.0.0.1:49374")
        if "0.0.0.0" in self.base_url:
            self.base_url = self.base_url.replace("0.0.0.0", "127.0.0.1")
        password = str(self.service.get("password") or "")
        self.auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
        self.opencode_db = opencode_db
        self.timeout_s = timeout_s

    def _request_json(self, path: str) -> dict[str, Any] | None:
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Authorization": self.auth, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None

    def goal_for(self, session_id: str) -> GoalState | None:
        if not self.opencode_db.is_file():
            return None
        try:
            connection = sqlite3.connect(f"file:{self.opencode_db}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return None
        try:
            rows = connection.execute(
                "select key, value from kv where key like ?",
                (f"plugin:%:session:{session_id}",),
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        for _key, raw in rows:
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or "objective" not in payload:
                continue
            status = str(payload.get("status") or GOAL_UNKNOWN)
            if status not in {GOAL_ACTIVE, GOAL_COMPLETE, GOAL_PAUSED, GOAL_BLOCKED}:
                status = GOAL_UNKNOWN
            return GoalState(
                session_id=session_id,
                goal_id=str(payload.get("id") or ""),
                objective=str(payload.get("objective") or ""),
                status=status,
                rounds=int(payload.get("rounds") or 0),
                max_rounds=int(payload.get("maxRounds") or 0),
                reason=payload.get("reason"),
                updated_at=ms_to_iso(payload.get("updatedAt")),
            )
        return None

    def session_info(self, session_id: str) -> SessionState:
        payload = self._request_json(f"/api/session/{session_id}")
        data = (payload or {}).get("data") or {}
        active_payload = self._request_json("/api/session/active")
        active_map = (active_payload or {}).get("data") or {}
        active = session_id in active_map
        outcome = data.get("outcome")
        if active:
            status = "running"
        elif outcome:
            status = str(outcome)
        else:
            status = "unknown"
        return SessionState(
            session_id=session_id,
            active=active,
            status=status,
            outcome=outcome,
            directory=(data.get("location") or {}).get("directory"),
            updated_at=ms_to_iso((data.get("time") or {}).get("updated")),
        )

    def git_state(self, worktree: str | None) -> GitState:
        if not worktree or not Path(worktree).is_dir():
            return GitState(branch="missing", head="-", dirty=False)
        branch = subprocess.run(
            ["git", "-C", worktree, "branch", "--show-current"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--short", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", worktree, "status", "--porcelain"],
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
        )
        return GitState(branch=branch or "detached", head=head or "-", dirty=dirty)

    def pull_requests(self) -> dict[str, PullRequestState]:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--limit",
                "80",
                "--json",
                "number,title,headRefName,state,mergeStateStatus,statusCheckRollup,url,updatedAt",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return {}
        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        output: dict[str, PullRequestState] = {}
        for item in raw:
            checks = _summarise_checks(item.get("statusCheckRollup") or [])
            output[str(item.get("headRefName"))] = PullRequestState(
                number=int(item.get("number") or 0),
                title=str(item.get("title") or ""),
                head_branch=str(item.get("headRefName") or ""),
                state=str(item.get("state") or "UNKNOWN"),
                merge_state=str(item.get("mergeStateStatus") or "UNKNOWN"),
                url=str(item.get("url") or ""),
                checks=checks,
                updated_at=item.get("updatedAt"),
            )
        return output

    def iter_events(self, stop: threading.Event | None = None):
        """Yield parsed SSE events, reconnecting with bounded exponential backoff."""
        backoff = 1.0
        while stop is None or not stop.is_set():
            request = urllib.request.Request(
                self.base_url + "/api/event",
                headers={"Authorization": self.auth, "Accept": "text/event-stream"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    backoff = 1.0
                    for raw in response:
                        if stop is not None and stop.is_set():
                            return
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict) and event.get("type"):
                            yield event
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if stop is not None and stop.is_set():
                    return
                yield {
                    "type": "monitor.sse.disconnected",
                    "created": int(time.time() * 1000),
                    "data": {"error": str(exc), "backoff_s": backoff},
                }
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _summarise_checks(items: Sequence[Mapping[str, Any]]) -> CheckSummary:
    passed = failed = pending = 0
    for item in items:
        conclusion = str(item.get("conclusion") or "").upper()
        status = str(item.get("status") or "").upper()
        if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            passed += 1
        elif conclusion in {
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STARTUP_FAILURE",
        }:
            failed += 1
        elif status in {"IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"}:
            pending += 1
        else:
            pending += 1
    if failed:
        return CheckSummary(passed, failed, pending, "fail")
    if pending:
        return CheckSummary(passed, failed, pending, "pending")
    if passed:
        return CheckSummary(passed, failed, pending, "pass")
    return CheckSummary(passed, failed, pending, "unknown")


class StateStore:
    """Coordinator-owned SQLite state; never inside a Goal worktree."""

    def __init__(self, path: Path = DEFAULT_STATE_DB) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create()

    def _create(self) -> None:
        self.connection.executescript(
            """
            create table if not exists workstreams (
              issue integer primary key,
              dimension text not null,
              scope text not null default '',
              worktree text,
              branch text,
              session_id text,
              goal_id text,
              objective text,
              goal_status text not null default 'unknown',
              goal_reason text,
              session_status text not null default 'unknown',
              last_activity text,
              last_heartbeat text,
              commit_sha text,
              dirty integer not null default 0,
              pr_number integer,
              pr_state text,
              pr_merge_state text,
              test_status text not null default 'unknown',
              blockers text not null default '',
              validated integer not null default 0,
              integrated integer not null default 0,
              updated_at text
            );
            create table if not exists events (
              id text primary key,
              session_id text,
              type text not null,
              created integer,
              payload text,
              processed_at text
            );
            create table if not exists transitions (
              id integer primary key autoincrement,
              issue integer not null,
              field text not null,
              old_value text,
              new_value text,
              reason text,
              created_at text not null
            );
            create table if not exists alerts (
              id integer primary key autoincrement,
              dedupe_key text not null unique,
              issue integer,
              kind text not null,
              message text not null,
              created_at text not null,
              notified_at text
            );
            """
        )
        self.connection.commit()

    def seed(self, workstreams: Iterable[Workstream]) -> None:
        for item in workstreams:
            self.connection.execute(
                """
                insert into workstreams
                  (issue, dimension, scope, worktree, branch, session_id, goal_id, blockers)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(issue) do update set
                  dimension=excluded.dimension, scope=excluded.scope,
                  worktree=excluded.worktree, branch=excluded.branch,
                  session_id=excluded.session_id, goal_id=excluded.goal_id,
                  blockers=excluded.blockers
                """,
                (
                    item.issue,
                    item.dimension,
                    item.scope,
                    item.worktree,
                    item.branch,
                    item.session_id,
                    item.expected_goal_id,
                    "; ".join(item.blockers),
                ),
            )
        self.connection.commit()

    def record_event(self, event: Mapping[str, Any]) -> bool:
        event_id = str(event.get("id") or "")
        if not event_id:
            return False
        data = event.get("data") or {}
        session_id = data.get("sessionID") if isinstance(data, dict) else None
        cursor = self.connection.execute(
            "insert or ignore into events (id, session_id, type, created, payload, processed_at) "
            "values (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                session_id,
                str(event.get("type") or ""),
                int(event.get("created") or 0),
                json.dumps(event, sort_keys=True),
                now_iso(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def _transition(self, issue: int, field_name: str, old: Any, new: Any, reason: str) -> None:
        if old == new:
            return
        self.connection.execute(
            "insert into transitions (issue, field, old_value, new_value, reason, created_at) "
            "values (?, ?, ?, ?, ?, ?)",
            (issue, field_name, None if old is None else str(old), str(new), reason, now_iso()),
        )

    def update(self, issue: int, **fields: Any) -> None:
        current = self.connection.execute(
            "select * from workstreams where issue=?", (issue,)
        ).fetchone()
        if current is None:
            return
        for key, value in fields.items():
            old = current[key]
            if old == value:
                continue
            self.connection.execute(f"update workstreams set {key}=? where issue=?", (value, issue))
            if key in {"goal_status", "session_status", "test_status", "validated", "integrated"}:
                self._transition(issue, key, old, value, "reconciliation")
        self.connection.execute(
            "update workstreams set updated_at=? where issue=?", (now_iso(), issue)
        )
        self.connection.commit()

    def alert(self, *, dedupe_key: str, issue: int, kind: str, message: str) -> bool:
        cursor = self.connection.execute(
            "insert or ignore into alerts (dedupe_key, issue, kind, message, created_at) "
            "values (?, ?, ?, ?, ?)",
            (dedupe_key, issue, kind, message, now_iso()),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def rows(self) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.connection.execute("select * from workstreams order by issue")
        ]

    def unread_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "select * from alerts where notified_at is null order by id limit ?", (limit,)
            )
        ]

    def mark_notified(self, alert_ids: Sequence[int]) -> None:
        if not alert_ids:
            return
        placeholders = ",".join("?" for _ in alert_ids)
        self.connection.execute(
            f"update alerts set notified_at=? where id in ({placeholders})",
            (now_iso(), *alert_ids),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def _has_goal_completed(row: Mapping[str, Any]) -> bool:
    return row.get("goal_status") == GOAL_COMPLETE and not row.get("validated")


def reconcile(
    *,
    workstreams: Sequence[Workstream],
    source: OpenCodeSource,
    store: StateStore,
    stale_seconds: int = DEFAULT_STALE_S,
    pr_states: Mapping[str, PullRequestState] | None = None,
) -> ReconcileResult:
    store.seed(workstreams)
    prs = dict(pr_states) if pr_states is not None else source.pull_requests()
    result = ReconcileResult()
    for item in workstreams:
        existing = next((row for row in store.rows() if row["issue"] == item.issue), {})
        goal = source.goal_for(item.session_id) if item.session_id else None
        session = source.session_info(item.session_id) if item.session_id else None
        git = source.git_state(item.worktree)
        pr = prs.get(item.branch or "")
        goal_status = goal.status if goal else (GOAL_UNKNOWN if item.session_id else "not_started")
        goal_reason = goal.reason if goal else "no native goal session"
        session_status = session.status if session else "no_session"
        last_activity = max(
            [
                value
                for value in [
                    goal.updated_at if goal else None,
                    session.updated_at if session else None,
                ]
                if value
            ],
            default=None,
        )
        fields = {
            "objective": goal.objective if goal else existing.get("objective"),
            "goal_id": goal.goal_id if goal else existing.get("goal_id"),
            "goal_status": goal_status,
            "goal_reason": goal_reason,
            "session_status": session_status,
            "last_activity": last_activity,
            "last_heartbeat": now_iso(),
            "commit_sha": git.head,
            "dirty": int(git.dirty),
            "pr_number": pr.number if pr else existing.get("pr_number"),
            "pr_state": pr.state if pr else existing.get("pr_state"),
            "pr_merge_state": pr.merge_state if pr else existing.get("pr_merge_state"),
            "test_status": pr.checks.status if pr else existing.get("test_status") or "unknown",
            "integrated": int(bool(pr and pr.state == "MERGED"))
            if pr
            else int(existing.get("integrated") or 0),
        }
        store.update(item.issue, **fields)

        if goal_status == GOAL_COMPLETE and not existing.get("validated"):
            store.alert(
                dedupe_key=f"goal-complete:{item.issue}:{fields['goal_id']}",
                issue=item.issue,
                kind="goal_completed",
                message=(
                    f"#{item.issue} native Goal completed but is NOT validated. "
                    f"reason={goal_reason}"
                ),
            )
        if goal_status in {GOAL_BLOCKED, "failed"}:
            store.alert(
                dedupe_key=f"goal-blocked:{item.issue}:{fields['goal_id']}",
                issue=item.issue,
                kind="goal_blocked",
                message=f"#{item.issue} native Goal is {goal_status}: {goal_reason}",
            )
        if (
            item.session_id
            and goal_status == GOAL_UNKNOWN
            and session_status in {"idle", "succeeded"}
        ):
            store.alert(
                dedupe_key=f"goal-unknown:{item.issue}",
                issue=item.issue,
                kind="goal_unknown",
                message=f"#{item.issue} session is {session_status} but Goal state is unknown.",
            )
        if goal_status == GOAL_ACTIVE and session_status in {"idle", "succeeded"} and last_activity:
            age = _age_seconds(last_activity)
            if age is not None and age > stale_seconds:
                store.alert(
                    dedupe_key=f"goal-stale:{item.issue}:{int(age // stale_seconds)}",
                    issue=item.issue,
                    kind="stale",
                    message=(
                        f"#{item.issue} Goal is active but session {session_status}; "
                        f"no activity for {int(age)}s."
                    ),
                )
        if pr and pr.state == "MERGED" and not existing.get("integrated"):
            store.alert(
                dedupe_key=f"pr-merged:{item.issue}:{pr.number}",
                issue=item.issue,
                kind="pr_merged",
                message=f"#{item.issue} PR #{pr.number} merged; run post-merge validation.",
            )
        if (
            pr
            and pr.state == "OPEN"
            and pr.checks.status == "pass"
            and existing.get("validated")
            and not existing.get("integrated")
        ):
            store.alert(
                dedupe_key=f"pr-ready:{item.issue}:{pr.number}",
                issue=item.issue,
                kind="pr_ready",
                message=f"#{item.issue} PR #{pr.number} has passing checks and is validated; ready to integrate.",
            )
        result.rows.append(
            dict(
                store.connection.execute(
                    "select * from workstreams where issue=?", (item.issue,)
                ).fetchone()
            )
        )
    result.alerts = store.unread_alerts()
    return result


def _age_seconds(iso_timestamp: str) -> float | None:
    try:
        then = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (datetime.now(UTC) - then).total_seconds()


def notify_coordinator(
    *,
    source: OpenCodeSource,
    store: StateStore,
    coordinator_session_id: str,
) -> int:
    alerts = store.unread_alerts()
    if not alerts:
        return 0
    lines = ["[Phase2 monitor] Important Goal/PR events:"]
    for item in alerts:
        lines.append(f"- #{item['issue']} {item['kind']}: {item['message']}")
    lines.append(
        "Coordinator: reconcile the evidence, decide validation/integration, and update the board."
    )
    payload = json.dumps({"text": "\n".join(lines)}).encode()
    request = urllib.request.Request(
        source.base_url + f"/api/session/{coordinator_session_id}/prompt",
        data=payload,
        method="POST",
        headers={
            "Authorization": source.auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0
    store.mark_notified([int(item["id"]) for item in alerts])
    return len(alerts)


def render_table(rows: Sequence[Mapping[str, Any]]) -> str:
    header = (
        "issue",
        "dim",
        "goal",
        "session",
        "activity",
        "pr",
        "tests",
        "validated",
        "integrated",
        "commit",
        "blockers",
    )
    body = [
        (
            f"#{row['issue']}",
            str(row.get("dimension") or ""),
            str(row.get("goal_status") or "unknown"),
            str(row.get("session_status") or "unknown"),
            str(row.get("last_activity") or "-"),
            f"#{row['pr_number']}" if row.get("pr_number") else "-",
            str(row.get("test_status") or "unknown"),
            "yes" if row.get("validated") else "no",
            "yes" if row.get("integrated") else "no",
            str(row.get("commit_sha") or "-"),
            str(row.get("blockers") or ""),
        )
        for row in rows
    ]
    widths = [max(len(str(row[i])) for row in [header, *body]) for i in range(len(header))]
    lines = ["  ".join(str(value).ljust(widths[i]) for i, value in enumerate(header))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)) for row in body
    )
    return "\n".join(lines)

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.phase2_monitor.core import (
    GOAL_ACTIVE,
    GOAL_BLOCKED,
    GOAL_COMPLETE,
    GOAL_UNKNOWN,
    CheckSummary,
    GitState,
    GoalState,
    OpenCodeSource,
    PullRequestState,
    SessionState,
    StateStore,
    Workstream,
    _summarise_checks,
    reconcile,
    render_table,
)


def _iso(minutes_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class FakeSource:
    def __init__(self, goals=None, sessions=None, prs=None, git=None):
        self.goals = goals or {}
        self.sessions = sessions or {}
        self.prs = prs or {}
        self.git = git or {}

    def goal_for(self, session_id):
        return self.goals.get(session_id)

    def session_info(self, session_id):
        return self.sessions.get(
            session_id,
            SessionState(session_id, False, "idle", "succeeded", None, _iso(1)),
        )

    def git_state(self, worktree):
        return self.git.get(worktree, GitState("phase2/test", "abc1234", False))

    def pull_requests(self):
        return self.prs


def _goal(session_id, status, goal_id="g1", reason=None, updated=None):
    return GoalState(
        session_id=session_id,
        goal_id=goal_id,
        objective="test objective",
        status=status,
        rounds=1,
        max_rounds=50,
        reason=reason,
        updated_at=updated or _iso(1),
    )


def _workstream(issue=135, branch="phase2/135-d01", session="ses_test"):
    return Workstream(
        issue=issue,
        dimension="D01",
        worktree="/tmp/test-worktree",
        branch=branch,
        session_id=session,
        expected_goal_id="g1",
    )


def test_complete_goal_is_not_auto_validated(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(goals={ws.session_id: _goal(ws.session_id, GOAL_COMPLETE)})
    result = reconcile(workstreams=[ws], source=source, store=store)
    row = result.rows[0]
    assert row["goal_status"] == GOAL_COMPLETE
    assert row["validated"] == 0
    assert any(alert["kind"] == "goal_completed" for alert in result.alerts)
    transitions = store.connection.execute("select * from transitions").fetchall()
    assert any(t["field"] == "goal_status" and t["new_value"] == GOAL_COMPLETE for t in transitions)


def test_active_goal_idle_session_is_not_complete_and_stale_alerts(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(
        goals={ws.session_id: _goal(ws.session_id, GOAL_ACTIVE, updated=_iso(30))},
        sessions={
            ws.session_id: SessionState(ws.session_id, False, "idle", "succeeded", None, _iso(30))
        },
    )
    result = reconcile(workstreams=[ws], source=source, store=store, stale_seconds=60)
    row = result.rows[0]
    assert row["goal_status"] == GOAL_ACTIVE
    assert row["session_status"] == "idle"
    assert row["validated"] == 0
    assert any(alert["kind"] == "stale" for alert in result.alerts)


def test_duplicate_events_are_idempotent(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    event = {
        "id": "evt_1",
        "type": "session.execution.succeeded",
        "created": 1,
        "data": {"sessionID": "ses_test"},
    }
    assert store.record_event(event) is True
    assert store.record_event(event) is False
    assert store.connection.execute("select count(*) from events").fetchone()[0] == 1


def test_two_goals_complete_together(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    a = _workstream(issue=135, branch="phase2/135-d01", session="ses_a")
    b = _workstream(issue=136, branch="phase2/136-d02", session="ses_b")
    source = FakeSource(
        goals={
            a.session_id: _goal(a.session_id, GOAL_COMPLETE, "g1"),
            b.session_id: _goal(b.session_id, GOAL_COMPLETE, "g2"),
        }
    )
    result = reconcile(workstreams=[a, b], source=source, store=store)
    assert {row["goal_status"] for row in result.rows} == {GOAL_COMPLETE}
    assert len([x for x in result.alerts if x["kind"] == "goal_completed"]) == 2


def test_restart_restores_state_without_duplicate_transitions(tmp_path: Path):
    db = tmp_path / "state.db"
    ws = _workstream()
    source = FakeSource(goals={ws.session_id: _goal(ws.session_id, GOAL_COMPLETE)})
    first = StateStore(db)
    reconcile(workstreams=[ws], source=source, store=first)
    transitions = first.connection.execute("select count(*) from transitions").fetchone()[0]
    first.close()

    second = StateStore(db)
    result = reconcile(workstreams=[ws], source=source, store=second)
    assert result.rows[0]["goal_status"] == GOAL_COMPLETE
    assert (
        second.connection.execute("select count(*) from transitions").fetchone()[0] == transitions
    )
    second.close()


def test_blocked_goal_alerts(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(
        goals={ws.session_id: _goal(ws.session_id, GOAL_BLOCKED, reason="missing credential")}
    )
    result = reconcile(workstreams=[ws], source=source, store=store)
    assert any(alert["kind"] == "goal_blocked" for alert in result.alerts)


def test_interrupted_session_is_not_complete(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(
        goals={ws.session_id: _goal(ws.session_id, GOAL_ACTIVE)},
        sessions={
            ws.session_id: SessionState(
                ws.session_id, False, "interrupted", "interrupted", None, _iso(1)
            )
        },
    )
    result = reconcile(workstreams=[ws], source=source, store=store)
    assert result.rows[0]["goal_status"] == GOAL_ACTIVE
    assert result.rows[0]["session_status"] == "interrupted"
    assert result.rows[0]["validated"] == 0


def test_unknown_goal_is_not_treated_as_complete(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(
        goals={},
        sessions={
            ws.session_id: SessionState(ws.session_id, False, "idle", "succeeded", None, _iso(1))
        },
    )
    result = reconcile(workstreams=[ws], source=source, store=store)
    assert result.rows[0]["goal_status"] == GOAL_UNKNOWN
    assert result.rows[0]["validated"] == 0
    assert any(alert["kind"] == "goal_unknown" for alert in result.alerts)


def test_merged_pr_alerts_and_sets_integrated(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    pr = PullRequestState(
        number=147,
        title="D01",
        head_branch=ws.branch,
        state="MERGED",
        merge_state="CLEAN",
        url="https://example/pr/147",
        checks=CheckSummary(3, 0, 0, "pass"),
        updated_at=_iso(0),
    )
    source = FakeSource(
        goals={ws.session_id: _goal(ws.session_id, GOAL_COMPLETE)},
        prs={ws.branch: pr},
    )
    result = reconcile(workstreams=[ws], source=source, store=store)
    assert result.rows[0]["integrated"] == 1
    assert any(alert["kind"] == "pr_merged" for alert in result.alerts)


def test_check_summary_and_table_render():
    assert _summarise_checks([{"conclusion": "SUCCESS"}]).status == "pass"
    assert _summarise_checks([{"conclusion": "FAILURE"}]).status == "fail"
    assert _summarise_checks([{"status": "IN_PROGRESS"}]).status == "pending"
    assert _summarise_checks([]).status == "unknown"
    table = render_table(
        [
            {
                "issue": 135,
                "dimension": "D01",
                "goal_status": "complete",
                "session_status": "idle",
                "last_activity": _iso(1),
                "pr_number": 147,
                "test_status": "pass",
                "validated": 0,
                "integrated": 0,
                "commit_sha": "abc1234",
                "blockers": "",
            }
        ]
    )
    assert "complete" in table and "no" in table


def test_goal_state_reader_accepts_plugin_kv(tmp_path: Path):
    db = tmp_path / "opencode.db"
    import sqlite3

    connection = sqlite3.connect(db)
    connection.execute(
        "create table kv (key text, value text, time_created integer, time_updated integer)"
    )
    payload = {
        "id": "goal-1",
        "objective": "obj",
        "status": "active",
        "rounds": 2,
        "maxRounds": 50,
        "updatedAt": 1794743508860,
        "reason": "working",
    }
    connection.execute(
        "insert into kv values (?, ?, ?, ?)",
        ("plugin:006f:session:ses_x", json.dumps(payload), 1, 1),
    )
    connection.commit()
    connection.close()

    source = OpenCodeSource.__new__(OpenCodeSource)
    source.opencode_db = db
    goal = source.goal_for("ses_x")
    assert goal is not None
    assert goal.goal_id == "goal-1"
    assert goal.status == GOAL_ACTIVE
    assert goal.updated_at is not None


def test_sse_reconnects_after_disconnect(monkeypatch):
    import threading
    import urllib.error

    from tools.phase2_monitor import core

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(self.lines)

    def fake_urlopen(request, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection reset")
        return FakeResponse(
            [
                b"\n",
                b": heartbeat\n",
                b'data: {"id":"evt_ok","type":"session.execution.succeeded","data":{"sessionID":"ses_x"}}\n',
            ]
        )

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(core.time, "sleep", lambda _seconds: None)
    source = OpenCodeSource.__new__(OpenCodeSource)
    source.base_url = "http://127.0.0.1:1"
    source.auth = "Basic x"
    stop = threading.Event()
    events = []
    for event in source.iter_events(stop):
        events.append(event)
        if len(events) >= 2:
            break
    assert events[0]["type"] == "monitor.sse.disconnected"
    assert events[1]["id"] == "evt_ok"
    assert calls["n"] >= 2


def test_complete_alert_is_deduped(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    ws = _workstream()
    source = FakeSource(goals={ws.session_id: _goal(ws.session_id, GOAL_COMPLETE)})
    reconcile(workstreams=[ws], source=source, store=store)
    reconcile(workstreams=[ws], source=source, store=store)
    count = store.connection.execute(
        "select count(*) from alerts where kind='goal_completed'"
    ).fetchone()[0]
    assert count == 1


def test_notify_marks_alerts_sent(tmp_path: Path, monkeypatch):
    from tools.phase2_monitor import core

    store = StateStore(tmp_path / "state.db")
    store.alert(dedupe_key="k1", issue=135, kind="goal_completed", message="done")
    sent = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse()

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    source = OpenCodeSource.__new__(OpenCodeSource)
    source.base_url = "http://127.0.0.1:1"
    source.auth = "Basic x"
    count = core.notify_coordinator(source=source, store=store, coordinator_session_id="ses_coord")
    assert count == 1
    assert sent and "Phase2 monitor" in sent[0]["text"]
    assert store.unread_alerts() == []

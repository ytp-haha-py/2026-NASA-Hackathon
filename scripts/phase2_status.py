#!/usr/bin/env python
"""Verify the Phase 2 worktree/session/goal board (Issue #142).

This is a coordinator tool. It does not edit any Goal worktree.

    uv run python scripts/phase2_status.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "docs/phase2/workstreams.json"


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def git_state(directory: Path) -> tuple[str, str, str]:
    if not directory.is_dir():
        return "MISSING", "-", "-"
    branch = run(["git", "branch", "--show-current"], cwd=directory).stdout.strip()
    head = run(["git", "log", "-1", "--format=%h"], cwd=directory).stdout.strip()
    status = run(["git", "status", "--porcelain"], cwd=directory).stdout.strip()
    return branch or "DETACHED", head, "dirty" if status else "clean"


def session_state(session_id: str) -> tuple[str, str, str, str]:
    exported = run(["opencode", "session", "export", session_id])
    if exported.returncode != 0:
        return "UNREADABLE", "-", "-", exported.stderr.strip()[:80]
    try:
        payload = json.loads(exported.stdout)
    except json.JSONDecodeError:
        return "BAD_JSON", "-", "-", ""
    messages = payload.get("messages") or []
    metadata = (messages[0].get("metadata") if messages else None) or {}
    goal_id = metadata.get("goalID") or "-"
    source = metadata.get("source") or "-"
    info = payload.get("info") or {}
    directory = (info.get("location") or {}).get("directory") or "-"
    outcome = info.get("outcome") or "active"
    return source, goal_id, directory, outcome


def active_sessions() -> set[str]:
    result = run(["opencode", "api", "get", "/api/session/active"])
    if result.returncode != 0:
        return set()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()
    return set((payload.get("data") or {}).keys())


def main() -> int:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    active = active_sessions()
    failures: list[str] = []
    warnings: list[str] = []
    rows: list[tuple[str, ...]] = []

    coordinator = board["coordinator"]
    rows.append(
        (
            "#142 coordinator",
            "coordinator",
            coordinator["session_id"],
            coordinator["goal_id"],
            "running" if coordinator["session_id"] in active else "not-running",
            *git_state(Path(coordinator["worktree"])),
        )
    )
    for workstream in board["workstreams"]:
        issue = f"#{workstream['issue']} {workstream['dimension']}"
        source, goal_id, session_dir, outcome = session_state(workstream["session_id"])
        running = "running" if workstream["session_id"] in active else f"idle/{outcome}"
        branch, head, status = git_state(Path(workstream["worktree"]))
        rows.append(
            (issue, "goal", workstream["session_id"], goal_id, running, branch, head, status)
        )
        if source != "goal":
            failures.append(f"{issue}: first message source is {source!r}, not 'goal'")
        if goal_id != workstream["goal_id"]:
            failures.append(f"{issue}: goalID {goal_id} != board {workstream['goal_id']}")
        if session_dir != workstream["worktree"]:
            failures.append(f"{issue}: session directory {session_dir} != {workstream['worktree']}")
        if running.startswith("UNREADABLE") or running.startswith("BAD_JSON"):
            failures.append(f"{issue}: session state {running}")
        if branch != workstream["branch"]:
            failures.append(f"{issue}: branch {branch} != {workstream['branch']}")
        if status == "dirty":
            warnings.append(f"{issue}: worktree is dirty (expected while the Goal is editing)")

    header = ("workstream", "kind", "session", "goal", "active", "branch", "head", "worktree")
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(header)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))

    if warnings:
        print("\nWARN:")
        for warning in warnings:
            print(f"  - {warning}")

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"\nPASS: {len(rows)} workstream(s) have a worktree, branch, session and native Goal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

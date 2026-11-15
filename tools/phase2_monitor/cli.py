"""CLI for the Phase 2 Goal monitor."""

from __future__ import annotations

import argparse
import contextlib
import json
import queue
import signal
import threading
import time
from pathlib import Path

from .core import (
    DEFAULT_CONFIG,
    DEFAULT_INTERVAL_S,
    DEFAULT_OPENCODE_DB,
    DEFAULT_SERVICE_JSON,
    DEFAULT_STALE_S,
    DEFAULT_STATE_DB,
    RELEVANT_EVENT_TYPES,
    OpenCodeSource,
    StateStore,
    load_workstreams,
    notify_coordinator,
    reconcile,
    render_table,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["run", "once", "status", "events"], nargs="?", default="run"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=DEFAULT_STATE_DB)
    parser.add_argument("--service-json", type=Path, default=DEFAULT_SERVICE_JSON)
    parser.add_argument("--opencode-db", type=Path, default=DEFAULT_OPENCODE_DB)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--stale-seconds", type=int, default=DEFAULT_STALE_S)
    parser.add_argument("--notify-session", default=None)
    parser.add_argument("--no-sse", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _snapshot(store: StateStore, as_json: bool) -> None:
    rows = store.rows()
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print(render_table(rows))


def _run_once(args: argparse.Namespace, source: OpenCodeSource, store: StateStore) -> int:
    workstreams, config = load_workstreams(args.config)
    result = reconcile(
        workstreams=workstreams,
        source=source,
        store=store,
        stale_seconds=args.stale_seconds,
    )
    _snapshot(store, args.json)
    notify_session = args.notify_session or (config.get("monitor") or {}).get("notify_session")
    if notify_session and result.alerts:
        count = notify_coordinator(
            source=source, store=store, coordinator_session_id=str(notify_session)
        )
        print(f"notified coordinator: {count} alert(s)")
    return 0


def _run_events(args: argparse.Namespace, source: OpenCodeSource) -> int:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    for event in source.iter_events(stop):
        print(json.dumps(event, ensure_ascii=False))
        if stop.is_set():
            break
    return 0


def _run_monitor(args: argparse.Namespace, source: OpenCodeSource, store: StateStore) -> int:
    workstreams, config = load_workstreams(args.config)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    event_queue: queue.Queue[dict] = queue.Queue()

    def event_worker() -> None:
        for event in source.iter_events(stop):
            event_queue.put(event)
            if stop.is_set():
                return

    worker = threading.Thread(target=event_worker, daemon=True, name="phase2-sse")
    worker.start()
    last_reconcile = 0.0
    notify_session = args.notify_session or (config.get("monitor") or {}).get("notify_session")
    print(
        f"phase2 monitor started: config={args.config} db={args.db} "
        f"interval={args.interval}s sse={not args.no_sse} notify={notify_session}",
        flush=True,
    )
    while not stop.is_set():
        now = time.monotonic()
        event = None
        with contextlib.suppress(queue.Empty):
            event = event_queue.get(timeout=1.0)
        if event is not None:
            event_type = str(event.get("type") or "")
            if event_type in RELEVANT_EVENT_TYPES:
                store.record_event(event)
                if now - last_reconcile >= 2.0:
                    result = reconcile(
                        workstreams=workstreams,
                        source=source,
                        store=store,
                        stale_seconds=args.stale_seconds,
                    )
                    last_reconcile = now
                    print(
                        f"{time.strftime('%H:%M:%S')} reconciled after {event_type}; "
                        f"alerts={len(result.alerts)}",
                        flush=True,
                    )
                    if notify_session and result.alerts:
                        notify_coordinator(
                            source=source,
                            store=store,
                            coordinator_session_id=str(notify_session),
                        )
        if now - last_reconcile >= args.interval:
            result = reconcile(
                workstreams=workstreams,
                source=source,
                store=store,
                stale_seconds=args.stale_seconds,
            )
            last_reconcile = now
            print(
                f"{time.strftime('%H:%M:%S')} periodic reconciliation; alerts={len(result.alerts)}",
                flush=True,
            )
            if notify_session and result.alerts:
                notify_coordinator(
                    source=source, store=store, coordinator_session_id=str(notify_session)
                )
    stop.set()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = StateStore(args.db)
    try:
        if args.command == "status":
            _snapshot(store, args.json)
            return 0
        source = OpenCodeSource(service_json=args.service_json, opencode_db=args.opencode_db)
        if args.command == "events":
            return _run_events(args, source)
        if args.command == "once":
            return _run_once(args, source, store)
        if args.no_sse:
            return _run_once(args, source, store)
        return _run_monitor(args, source, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

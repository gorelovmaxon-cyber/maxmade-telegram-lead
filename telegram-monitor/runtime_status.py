"""Local process health and lifecycle evidence; never include message bodies or secrets."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
import os
from pathlib import Path

STATE = Path(__file__).resolve().parent / "state"


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def update(component: str, **fields) -> dict:
    STATE.mkdir(exist_ok=True)
    path = STATE / (component + "-health.json")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if previous.get("pid") != os.getpid():
        previous = {"started_at": stamp()}
    record = {**previous, **fields, "component": component, "pid": os.getpid(), "updated_at": stamp()}
    temporary = STATE / (component + "-health-" + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)
    return record


def event(component: str, action: str, **fields) -> None:
    STATE.mkdir(exist_ok=True)
    with (STATE / "lifecycle.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"at": stamp(), "pid": os.getpid(), "component": component,
                                 "action": action, **fields}, ensure_ascii=True) + "\n")


@contextmanager
def lifecycle(component: str):
    update(component, state="starting", last_network_ok=None, error_type=None)
    event(component, "started")
    try:
        yield
    except BaseException as exc:
        state = "stopped" if isinstance(exc, KeyboardInterrupt) else "error"
        update(component, state=state, error_type=type(exc).__name__)
        event(component, "exited", error_type=type(exc).__name__)
        raise
    else:
        update(component, state="stopped")
        event(component, "exited", result="normal")

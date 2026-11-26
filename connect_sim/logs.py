"""Local run logs, laid out like CloudWatch log groups and log streams.

Each run writes to <runs-dir>/connect-sim/<flow>/<scenario>-<UTC stamp>/ with
events.jsonl (one line per Action, in order) and summary.json (verdict and reason).
The group is /connect-sim/<flow>. The stream is one scenario run.
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

GROUP_ROOT = "connect-sim"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def level_of(event):
    if event.get("error"):
        return "ERROR"
    if event.get("notes"):
        return "WARN"
    return "INFO"


def format_event(event):
    """One console line per Action, with notes printed under it as WARN lines."""
    level = level_of(event)
    text = event.get("message", "")
    if event.get("error"):
        text = f"{event['error']}: {text}"
    line = f"#{event['seq']:02d} {level:<5} {event['action']:<24} {event.get('type', ''):<24} {text}"
    if event.get("next"):
        line += f"  -> {event['next']}"
    lines = [line]
    for note in event.get("notes", []):
        lines.append(f"     WARN  {note}")
    return "\n".join(lines)


class RunLog:
    """Writes one scenario run. Each event is appended as soon as it happens."""

    def __init__(self, runs_dir, flow_name, scenario_name, echo=True):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.group = f"{GROUP_ROOT}/{flow_name}"
        self.stream = f"{scenario_name}-{stamp}"
        self.dir = Path(runs_dir) / self.group / self.stream
        self.dir.mkdir(parents=True, exist_ok=True)
        self.echo = echo
        self.events_path = self.dir / "events.jsonl"
        self.events_path.write_text("", encoding="utf-8")

    def event(self, event):
        record = {"ts": now_iso(), **event}
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.echo:
            print(format_event(record))

    def close(self, summary):
        with open(self.dir / "summary.json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)


def list_streams(runs_dir, flow_name):
    """Return stream directories for one flow, oldest first."""
    base = Path(runs_dir) / GROUP_ROOT / flow_name
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if (p / "summary.json").is_file())


def read_summary(stream_dir):
    with open(Path(stream_dir) / "summary.json", encoding="utf-8") as fh:
        return json.load(fh)


def read_events(stream_dir, only_problems=False):
    events = []
    with open(Path(stream_dir) / "events.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if only_problems and level_of(event) == "INFO":
                continue
            events.append(event)
    return events


def prune(runs_dir, flow_name, scenario_name, keep):
    """Delete the oldest runs of one scenario, keeping the newest 'keep'. Return the removed folders.

    Only folders named <scenario>-<UTC stamp> are candidates, so a scenario whose name
    starts the same way (ok and ok-2, for example) is never touched.
    """
    if keep is None or keep < 1:
        return []
    pattern = re.compile(rf"^{re.escape(scenario_name)}-\d{{8}}T\d{{6}}Z$")
    base = Path(runs_dir) / GROUP_ROOT / flow_name
    if not base.is_dir():
        return []
    mine = sorted(p for p in base.iterdir() if p.is_dir() and pattern.match(p.name))
    removed = mine[:-keep] if len(mine) > keep else []
    for folder in removed:
        shutil.rmtree(folder)
    return removed

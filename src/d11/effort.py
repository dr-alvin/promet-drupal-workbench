"""Explicit human effort ledger; never infer zero human work from missing entries."""

from datetime import datetime

from .common import Problem


def effort_totals(entries):
    active = {}
    confirmed = False
    intervals = []
    totals = {k: 0.0 for k in ("engineering", "technical_qa", "business_uat", "waiting")}
    for e in entries:
        op = e["operation"]
        if op == "confirm":
            if active:
                raise Problem("Stop all timers before confirming effort tracking")
            confirmed = True
            continue
        confirmed = False
        key = (e["person"], e["category"])
        if op == "start":
            if key in active or (
                key[1] in ("engineering", "technical_qa")
                and any(k[0] == key[0] and k[1] in ("engineering", "technical_qa") for k in active)
            ):
                raise Problem("Effort timer is already running for this operator")
            active[key] = datetime.fromisoformat(e["at"])
        elif op == "stop":
            if key not in active:
                raise Problem("No matching effort timer")
            start = active.pop(key)
            end = datetime.fromisoformat(e["at"])
            seconds = (end - start).total_seconds()
            if key[1] in ("engineering", "technical_qa"):
                intervals.append([start.timestamp(), end.timestamp()])
            if seconds < 0:
                raise Problem("Effort stop precedes start")
            totals[e["category"]] += seconds
        elif op == "correction":
            totals[e["category"]] += e["seconds"]
        if any(v < 0 for v in totals.values()):
            raise Problem("Correction would make effort negative")
    return {
        "categoriesSeconds": totals,
        "humanEffortSeconds": totals["engineering"] + totals["technical_qa"] if entries else None,
        "openTimers": [{"person": k[0], "category": k[1]} for k in active],
        "trackingComplete": confirmed,
        "supervisedIntervals": intervals,
    }


def unattended_seconds(commands, effort, entries):
    if not effort["trackingComplete"] or any(
        e["operation"] == "correction" and e.get("seconds") for e in entries
    ):
        return None

    def merge(intervals):
        result = []
        for start, end in sorted(intervals):
            if result and start <= result[-1][1]:
                result[-1][1] = max(end, result[-1][1])
            else:
                result.append([start, end])
        return result

    tool = merge(
        [
            [
                datetime.fromisoformat(c["startedAt"]).timestamp(),
                datetime.fromisoformat(c["finishedAt"]).timestamp(),
            ]
            for c in commands
        ]
    )
    human = merge(effort["supervisedIntervals"])
    seconds = sum(b - a for a, b in tool)
    for a, b in tool:
        for c, d in human:
            seconds -= max(0, min(b, d) - max(a, c))
    return round(max(0, seconds), 3)

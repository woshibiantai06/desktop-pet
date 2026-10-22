#!/usr/bin/env python3
"""Check declared desktop-pet action routes; no rendering or external packages."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path, PureWindowsPath
import sys


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def audit(data, root, *, check_files=False, portable=False):
    errors, warnings, route_results = [], [], []
    result = {"ok": False, "errors": errors, "warnings": warnings, "routes": route_results}
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list) or not data["actions"]:
        errors.append("Manifest must be an object with a nonempty actions list.")
        return result
    actions = {}
    for index, action in enumerate(data["actions"]):
        label = f"actions[{index}]"
        if not isinstance(action, dict):
            errors.append(f"{label}: must be an object")
            continue
        required = ("asset_id", "start_state", "end_state")
        if not all(nonempty(action.get(key)) for key in required):
            errors.append(f"{label}: asset_id, start_state and end_state must be nonempty strings")
            continue
        asset_id = action["asset_id"]
        if asset_id in actions:
            errors.append(f"{label}: duplicate asset_id {asset_id!r}")
            continue
        actions[asset_id] = action
        if action.get("kind") not in ("loop", "transition", "effect"):
            errors.append(f"{asset_id}: kind must be loop, transition or effect")
        if action.get("kind") == "loop" and action["start_state"] != action["end_state"]:
            errors.append(f"{asset_id}: a loop must end in its start_state")
        if "fps" in action:
            fps = action["fps"]
            try:
                valid_fps = type(fps) in (int, float) and math.isfinite(fps) and fps > 0
            except OverflowError:
                valid_fps = False
            if not valid_fps:
                errors.append(f"{asset_id}: fps must be finite and positive")
        if "frame_count" in action and not (type(action["frame_count"]) is int and action["frame_count"] > 0):
            errors.append(f"{asset_id}: frame_count must be a positive integer")
        if "path" in action:
            raw = action["path"]
            if not nonempty(raw):
                errors.append(f"{asset_id}: path must be a nonempty string")
                continue
            try:
                local = Path(raw)
                resolved = (root / local).resolve()
                if portable and (local.is_absolute() or PureWindowsPath(raw).drive or not resolved.is_relative_to(root)):
                    errors.append(f"{asset_id}: portable path must remain inside the manifest directory")
                if check_files and not resolved.exists():
                    errors.append(f"{asset_id}: missing path {raw!r}")
            except (OSError, ValueError) as exc:
                errors.append(f"{asset_id}: invalid path: {exc}")

    initial = data.get("initial_state")
    if initial is not None:
        if not nonempty(initial):
            errors.append("initial_state must be a nonempty string")
        else:
            reachable = {initial}
            while True:
                expanded = reachable | {a["end_state"] for a in actions.values() if a["start_state"] in reachable}
                if expanded == reachable:
                    break
                reachable = expanded
            unreachable = sorted(key for key, action in actions.items() if action["start_state"] not in reachable)
            if unreachable:
                warnings.append("Unreachable from initial_state: " + ", ".join(unreachable))

    routes = data.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes must be a list")
        routes = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            errors.append(f"routes[{index}]: must be an object")
            continue
        label = route.get("name", f"routes[{index}]")
        ids = route.get("actions")
        if not nonempty(route.get("start_state")) or not nonempty(route.get("end_state")) or not isinstance(ids, list):
            errors.append(f"{label}: requires start_state, end_state and actions list")
            continue
        state = route["start_state"]
        before = len(errors)
        for step, asset_id in enumerate(ids):
            if not nonempty(asset_id) or asset_id not in actions:
                errors.append(f"{label} step {step}: unknown asset_id {asset_id!r}")
                break
            action = actions[asset_id]
            if action["start_state"] != state:
                errors.append(f"{label} step {step} ({asset_id}): current {state!r}, requires {action['start_state']!r}")
                break
            state = action["end_state"]
        if len(errors) == before and state != route["end_state"]:
            errors.append(f"{label}: final state {state!r}, expected {route['end_state']!r}")
        route_results.append({"name": label, "ok": len(errors) == before, "final_state": state})
    result["action_count"] = len(actions)
    result["ok"] = not errors
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--check-files", action="store_true")
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        result = audit(data, args.manifest.resolve().parent, check_files=args.check_files, portable=args.portable)
    except (OSError, ValueError, RecursionError) as exc:
        result = {"ok": False, "errors": [str(exc)], "warnings": [], "routes": []}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read states, history, and statistics from the local Home Assistant API."""

from __future__ import annotations

import argparse
from fnmatch import fnmatch
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_FILE = PROJECT_ROOT / ".local" / "ha-token"
DEFAULT_HA_URL = "http://homeassistant.local:8123"


def api_request(url: str, token: str, path: str, payload: dict | None = None):
    """Call one of the explicitly selected read-only HA endpoints."""
    command = ["curl", "-sS", "--fail", "--max-time", "30", "--config", "-"]
    if payload is not None:
        command.extend(("-H", "Content-Type: application/json", "--data", json.dumps(payload)))
    command.append(url.rstrip("/") + path)
    result = subprocess.run(
        command,
        input=f'header = "Authorization: Bearer {token}"\n',
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def matches(item: dict, patterns: list[str]) -> bool:
    if not patterns:
        return True
    entity_id = item["entity_id"].lower()
    friendly_name = item.get("attributes", {}).get("friendly_name", "").lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if fnmatch(entity_id, pattern) or fnmatch(friendly_name, pattern):
            return True
        if not any(character in pattern for character in "*?["):
            if pattern in entity_id or pattern in friendly_name:
                return True
    return False


def print_states(items: list[dict], full_json: bool) -> None:
    if full_json:
        print(json.dumps(items, indent=2, sort_keys=True))
        return
    for item in items:
        attributes = item.get("attributes", {})
        unit = attributes.get("unit_of_measurement", "")
        name = attributes.get("friendly_name", "")
        print(f"{item['entity_id']}: {item['state']} {unit} | {name}".rstrip())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", default=DEFAULT_HA_URL)
    result.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    subparsers = result.add_subparsers(dest="command", required=True)

    states = subparsers.add_parser("states", help="Read current entity states")
    states.add_argument("patterns", nargs="*", help="Entity/name substrings or shell-style globs")
    states.add_argument("--json", action="store_true", help="Print complete state objects")

    history = subparsers.add_parser("history", help="Read short-term recorder history")
    history.add_argument("--start", required=True, help="ISO-8601 start time")
    history.add_argument("--end", required=True, help="ISO-8601 end time")
    history.add_argument("--entity", action="append", required=True, dest="entities")

    statistics = subparsers.add_parser("statistics", help="Read long-term recorder statistics")
    statistics.add_argument("--start", required=True, help="ISO-8601 start time")
    statistics.add_argument("--end", required=True, help="ISO-8601 end time")
    statistics.add_argument("--entity", action="append", required=True, dest="entities")
    statistics.add_argument(
        "--period", choices=("5minute", "hour", "day", "week", "month", "year"), default="hour"
    )
    return result


def main() -> None:
    args = parser().parse_args()
    token = args.token_file.read_text().strip()

    if args.command == "states":
        items = api_request(args.url, token, "/api/states")
        selected = sorted((item for item in items if matches(item, args.patterns)), key=lambda item: item["entity_id"])
        print_states(selected, args.json)
        return

    if args.command == "history":
        entity_ids = ",".join(args.entities)
        path = f"/api/history/period/{args.start}?end_time={args.end}&filter_entity_id={entity_ids}"
        print(json.dumps(api_request(args.url, token, path), indent=2, sort_keys=True))
        return

    payload = {
        "start_time": args.start,
        "end_time": args.end,
        "statistic_ids": args.entities,
        "period": args.period,
        "types": ["change", "last_reset", "max", "mean", "min", "state", "sum"],
    }
    response = api_request(
        args.url,
        token,
        "/api/services/recorder/get_statistics?return_response",
        payload,
    )
    print(json.dumps(response.get("service_response", response), indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"HA read failed: {error}", file=sys.stderr)
        raise SystemExit(1)

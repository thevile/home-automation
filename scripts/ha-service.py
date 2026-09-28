#!/usr/bin/env python3
"""Call a Home Assistant service for explicitly supplied entities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("service")
    parser.add_argument("--entity", required=True)
    parser.add_argument("--data", default="{}")
    parser.add_argument("--url", default="http://homeassistant.local:8123")
    parser.add_argument("--token-file", type=Path, default=PROJECT_ROOT / ".local" / "ha-token")
    args = parser.parse_args()

    payload = json.loads(args.data)
    payload["entity_id"] = args.entity
    token = args.token_file.read_text().strip()
    command = [
        "curl", "-sS", "--fail", "--max-time", "30", "--config", "-",
        "-H", "Content-Type: application/json", "--data", json.dumps(payload),
        f"{args.url.rstrip('/')}/api/services/{args.domain}/{args.service}",
    ]
    result = subprocess.run(
        command,
        input=f'header = "Authorization: Bearer {token}"\n',
        text=True,
        capture_output=True,
        check=True,
    )
    print(result.stdout)


if __name__ == "__main__":
    main()

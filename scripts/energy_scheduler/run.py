"""Run once offline/live, or continuously as a supervised HA app."""
import argparse
from datetime import datetime
import json
import logging
from pathlib import Path
import os
import signal
import threading
import time

from .ha_adapter import HAAdapter
from .models import UTC
from .observation import ObservationStore, calibrate
from .planner import plan, validate_settings

STOP = threading.Event()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path, help="Offline snapshot; never connect to HA")
    parser.add_argument("--api-url", default="http://homeassistant.local:8123/api")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--publish", action="store_true", help="Publish only the optimizer-owned sensor")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--summary", action="store_true", help="Omit interval and observation detail from console output")
    args = parser.parse_args()
    if args.snapshot and (args.publish or args.loop):
        parser.error("Offline snapshots cannot be published or looped")
    if args.loop and not args.data_dir:
        parser.error("Continuous observation requires --data-dir")
    settings = json.loads(args.settings.read_text())
    validate_settings(settings)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = None
    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = ObservationStore(args.data_dir / "observations.sqlite")
    adapter = None
    if not args.snapshot:
        token = args.token_file.read_text().strip() if args.token_file else os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            parser.error("Supply --token-file or SUPERVISOR_TOKEN")
        adapter = HAAdapter(args.api_url, token, settings)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    failures = 0
    last_published_bucket = None
    last_fingerprint = None
    try:
        while not STOP.is_set():
            try:
                snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else adapter.snapshot()
                if store:
                    store.record(snapshot)
                calibration = calibrate(store.samples(), settings) if store else {}
                result = plan(snapshot, settings, calibration=calibration)
                if store:
                    store.record_plan(result)
                material = {"forecast": snapshot["forecast"], "tesla": {
                    key: snapshot["readings"].get(key, {}).get("value")
                    for key in ("soc", "target", "home", "cable")},
                    "room_guard": snapshot["readings"].get("room", {}).get("value")
                    if settings["room_confirmed"] else None}
                fingerprint = json.dumps(material, sort_keys=True)
                bucket = int(datetime.now(UTC).timestamp()) // 900
                should_publish = (not args.loop or last_published_bucket != bucket
                                  or fingerprint != last_fingerprint)
                if args.publish and should_publish:
                    adapter.publish(result)
                    last_published_bucket, last_fingerprint = bucket, fingerprint
                if not args.loop:
                    output = {k: v for k, v in result.items() if k not in ("timeline", "observations")} if args.summary else result
                    print(json.dumps(output, indent=2, allow_nan=False))
                else:
                    logging.info("Shadow: Tesla=%s DHW=%s heating=%s samples=%s",
                                 result["tesla"]["status"], result["hot_water"]["status"],
                                 result["heating"]["status"], calibration.get("background_samples", 0))
                failures = 0
            except Exception as error:
                # Never log request headers, tokens or unrestricted HA data.
                logging.error("Shadow calculation failed (%s)", type(error).__name__)
                if args.publish:
                    try:
                        adapter.unavailable()
                    except Exception:
                        pass
                failures += 1
                if not args.loop or failures >= 5:
                    raise SystemExit(1) from None
            if not args.loop:
                break
            # Read HA's cache each minute, not Tesla. This coalesces changes,
            # refreshes heartbeat and includes every quarter-hour boundary.
            STOP.wait(max(1, 60 - time.time() % 60))
    finally:
        if args.loop and args.publish:
            try:
                adapter.unavailable()
            except Exception:
                pass
        if store:
            store.close()


if __name__ == "__main__":
    main()

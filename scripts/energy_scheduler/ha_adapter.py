"""HA state I/O. The HTTP allowlist excludes every service/command endpoint."""
import json
from datetime import datetime
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

from .models import UTC, instant

OUTPUT_ENTITY = "sensor.energy_shadow_raw"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing redirect of authenticated HA request")


class HAAdapter:
    def __init__(self, api_url, token, settings, transport=None):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.settings = settings
        self.transport = transport or build_opener(NoRedirect()).open

    def _request(self, method, path, payload=None):
        if (method, path) not in {("GET", "/states"), ("POST", f"/states/{OUTPUT_ENTITY}")}:
            raise ValueError("Shadow adapter only permits reading states and publishing its own result")
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        req = Request(self.api_url + path, data=body, method=method,
                      headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with self.transport(req, timeout=20) as response:
            return json.load(response)

    def snapshot(self):
        states = {s["entity_id"]: s for s in self._request("GET", "/states")}
        now = datetime.now(UTC).isoformat()
        readings = {}
        for key, entity in self.settings["entities"].items():
            if key == "price":
                continue
            source = states.get(entity, {})
            readings[key] = {"value": source.get("state"),
                             "reported": source.get("last_reported") or source.get("last_updated")}
        # Unchanged HA entities can keep old timestamps even while charge_state
        # is being fetched. An updated read-only charge sensor is evidence of a
        # shared charge_state refresh, unlike an optimistic number/switch write.
        # Retain each entity's original stamp and label this inferred freshness.
        charge_keys = ("soc", "current", "power", "energy_added", "voltage", "charging")
        stamps = []
        for key in charge_keys:
            source = states.get(self.settings["entities"].get(key), {})
            stamp = source.get("last_updated")
            if (stamp and source.get("state") not in (None, "unknown", "unavailable")
                    and not source.get("context", {}).get("user_id")):
                try:
                    stamps.append(instant(stamp))
                except ValueError:
                    pass
        if stamps:
            evidence = max(stamps).isoformat()
            for key in (*charge_keys, "target", "cable"):
                if key in readings:
                    readings[key]["entity_reported"] = readings[key]["reported"]
                    readings[key]["reported"] = max(
                        (stamp for stamp in (readings[key]["reported"], evidence) if stamp), key=instant)
                    readings[key]["freshness_basis"] = "shared_charge_state_sensor_update"
        total = states.get(self.settings["entities"].get("p1_total_power"), {})
        if total.get("state") not in (None, "unknown", "unavailable") and total.get("last_updated"):
            try:
                evidence = instant(total["last_updated"]).isoformat()
                for key in ("phase_1", "phase_2", "phase_3"):
                    readings[key]["entity_reported"] = readings[key]["reported"]
                    readings[key]["reported"] = max(
                        (readings[key]["reported"], evidence), key=instant)
                    readings[key]["freshness_basis"] = "shared_p1_telegram_update"
            except (TypeError, ValueError):
                pass
        attributes = states.get(self.settings["entities"]["price"], {}).get("attributes", {})
        # Never persist GPS, unrelated states or arbitrary entity attributes.
        forecast = {k: attributes.get(k) for k in ("raw_today", "raw_tomorrow", "tomorrow_valid")}
        forecast["unit"] = attributes.get("unit_of_measurement")
        return {"acquired_at": now, "readings": readings, "forecast": forecast}

    def publish(self, result):
        payload = {"state": result["status"], "attributes": {
            "friendly_name": "Energy shadow raw", "icon": "mdi:calendar-clock",
            **result}}
        if len(json.dumps(payload)) > 150000:
            raise ValueError("Result exceeds publication limit")
        return self._request("POST", f"/states/{OUTPUT_ENTITY}", payload)

    def unavailable(self):
        self.publish({"status": "unavailable", "mode": "shadow", "valid_until": None,
                      "calculated_at": datetime.now(UTC).isoformat(),
                      "warnings": ["Scheduler could not obtain or process current inputs"]})

"""Bounded, private observations and conservative empirical calibration."""
from datetime import timedelta
import json
import sqlite3

from .models import instant, numeric, reading


class ObservationStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS samples (stamp TEXT PRIMARY KEY, readings TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS plans (stamp TEXT PRIMARY KEY, result TEXT NOT NULL)")

    def record(self, snapshot):
        now = instant(snapshot["acquired_at"])
        # One sample per minute, even if the process is repeatedly restarted.
        stamp = now.replace(second=0, microsecond=0).isoformat()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO samples VALUES (?, ?)",
                            (stamp, json.dumps(snapshot["readings"])))
            cutoff = (now - timedelta(days=8)).isoformat()
            self.db.execute("DELETE FROM samples WHERE stamp < ?", (cutoff,))
            self.db.execute("DELETE FROM plans WHERE stamp < ?", (cutoff,))

    def record_plan(self, result):
        now = instant(result["calculated_at"])
        stamp = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0).isoformat()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO plans VALUES (?, ?)", (stamp, json.dumps(result)))

    def samples(self):
        return [{"acquired_at": stamp, "readings": json.loads(data)}
                for stamp, data in self.db.execute("SELECT stamp, readings FROM samples ORDER BY stamp")]

    def close(self):
        self.db.close()


def calibrate(samples, settings):
    backgrounds, rates, session = [], [], []
    freshness = settings["freshness_seconds"]

    def finish_session():
        if len(session) < 2:
            return
        hours = (instant(session[-1]["acquired_at"]) - instant(session[0]["acquired_at"])).total_seconds() / 3600
        gain = numeric(reading(session[-1], "soc").value) - numeric(reading(session[0], "soc").value)
        # Whole-percent SOC and polling make short sessions misleading. Reserve
        # one percentage point for quantization and 15% for operating variation.
        if hours >= 1 and gain >= 3:
            rate = (gain - 1) / hours * 0.85
            if 0.1 <= rate <= 10:
                rates.append(rate)

    for snap in samples:
        now = instant(snap["acquired_at"])
        tesla_fresh = all(reading(snap, k).fresh(now, freshness["tesla"])
                          for k in ("soc", "current", "charging", "home", "cable"))
        current = numeric(reading(snap, "current").value)
        soc = numeric(reading(snap, "soc").value)
        charging = reading(snap, "charging").value == "charging"
        stable = (tesla_fresh and soc is not None and 0 <= soc <= 100 and current is not None
                  and abs(current - settings["current_a"]) < 0.5 and charging
                  and reading(snap, "home").value == "home" and reading(snap, "cable").value == "on")
        if session and ((now - instant(session[-1]["acquired_at"])).total_seconds() > 150
                        or not stable or soc < numeric(reading(session[-1], "soc").value)):
            finish_session()
            session = []
        if stable:
            session.append(snap)
        amps = [numeric(reading(snap, f"phase_{p}").value) for p in (1, 2, 3)]
        p1_fresh = all(reading(snap, f"phase_{p}").fresh(now, freshness["p1"]) for p in (1, 2, 3))
        if p1_fresh and all(a is not None and a >= 0 for a in amps):
            # Only subtract Tesla current when contemporaneous enough to identify
            # it. Otherwise leave it in the envelope (conservative overestimate).
            if (charging and reading(snap, "current").fresh(now, freshness["p1"])
                    and reading(snap, "charging").fresh(now, freshness["p1"])
                    and reading(snap, "home").value == "home" and current is not None):
                index = settings["charging_phase"] - 1
                amps[index] = max(0, amps[index] - current)
            backgrounds.append((now, amps))
    finish_session()
    span = (backgrounds[-1][0] - backgrounds[0][0]).total_seconds() / 3600 if backgrounds else 0
    envelope = None
    if len(backgrounds) >= 360 and span >= 6:
        # Upper observed envelope, not an assertion that future loads are bounded.
        envelope = [round(max(row[1][i] for row in backgrounds), 2) for i in range(3)]
    hot_water = calibrate_hot_water(samples)
    return {"soc_gain_per_hour": round(min(rates), 4) if rates else None,
            "qualifying_sessions": len(rates), "background_amps": envelope,
            "background_samples": len(backgrounds), "observation_hours": round(span, 2),
            "confidence": "provisional", "hot_water": hot_water}


def calibrate_hot_water(samples):
    """Infer completed DHW cycles from its dedicated electrical-energy counter."""
    completed = []
    active = None
    previous = None

    def finish():
        if not active:
            return
        minutes = (active["last"] - active["start"]).total_seconds() / 60 + 15
        rise = (active["end_temp"] - active["start_temp"]
                if active["end_temp"] is not None and active["start_temp"] is not None else None)
        if (active["kwh"] >= 0.5 and 15 <= minutes <= 240 and rise is not None
                and rise >= 1 and active["end_temp"] >= 49.5):
            completed.append({"minutes": minutes, "kwh": active["kwh"],
                              "end_temp": active["end_temp"]})

    for snap in samples:
        now = instant(snap["acquired_at"])
        energy = numeric(reading(snap, "dhw_kwh").value)
        tank = numeric(reading(snap, "tank").value)
        if active and now - active["last"] > timedelta(minutes=35):
            finish()
            active = None
        if previous:
            prev_time, prev_energy, prev_tank = previous
            if (energy is not None and prev_energy is not None
                    and timedelta(0) < now - prev_time <= timedelta(minutes=20)
                    and 0.02 <= energy - prev_energy <= 5):
                if active is None:
                    active = {"start": prev_time, "last": now, "kwh": 0,
                              "start_temp": prev_tank, "end_temp": tank}
                active["last"] = now
                active["kwh"] += energy - prev_energy
                active["end_temp"] = tank
        previous = (now, energy, tank)
    # A cycle is not complete until no energy increase has been seen for 35 min.
    if active and samples and instant(samples[-1]["acquired_at"]) - active["last"] > timedelta(minutes=35):
        finish()
    if len(completed) < 3:
        return {"status": "needs_calibration", "qualifying_cycles": len(completed),
                "required_cycles": 3, "duration_minutes": None, "electrical_kwh": None}
    return {"status": "provisional", "qualifying_cycles": len(completed),
            "required_cycles": 3,
            "duration_minutes": round(max(c["minutes"] for c in completed) + 30, 1),
            "electrical_kwh": round(max(c["kwh"] for c in completed) * 1.2, 2)}

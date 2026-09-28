"""Behavioral tests for price scheduling, freshness, calibration and I/O isolation."""
import copy
from datetime import datetime, timedelta, time
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from energy_scheduler.ha_adapter import HAAdapter, OUTPUT_ENTITY
from energy_scheduler.models import UTC, Slot, instant
from energy_scheduler.observation import ObservationStore, calibrate, calibrate_hot_water
from energy_scheduler.planner import allocate, blocks, covers, forecast, plan, validate_settings

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Europe/Helsinki")


def settings():
    result = json.loads((ROOT / "home-assistant/energy_scheduler/settings.json").read_text())
    result["soc_gain_per_hour"] = 3
    return result


def day(date, value=0.1):
    cursor = datetime.combine(date, time(), ZONE).astimezone(UTC)
    end = datetime.combine(date + timedelta(days=1), time(), ZONE).astimezone(UTC)
    result = []
    while cursor < end:
        stop = cursor + timedelta(minutes=15)
        result.append({"start": cursor.isoformat(), "end": stop.isoformat(), "value": value})
        cursor = stop
    return result


def snapshot(now=None):
    now = now or datetime(2026, 9, 22, 22, tzinfo=ZONE).astimezone(UTC)
    values = dict(home="home", cable="on", soc="37", target="50", current="9", power="2",
                  charging="charging", phase_1="3", phase_2="12", phase_3="2", tank="44", room="20")
    localdate = now.astimezone(ZONE).date()
    return {"acquired_at": now.isoformat(),
            "readings": {k: {"value": v, "reported": now.isoformat()} for k, v in values.items()},
            "forecast": {"unit": "EUR/kWh", "raw_today": day(localdate),
                         "raw_tomorrow": day(localdate + timedelta(days=1)), "tomorrow_valid": True}}


class PlannerTests(unittest.TestCase):
    def test_negative_prices_and_equal_energy_cost_comparison(self):
        snap = snapshot()
        for row in snap["forecast"]["raw_tomorrow"][4:20]:
            row["value"] = -0.1
        result = plan(snap, settings())
        t = result["tesla"]
        self.assertEqual(t["status"], "ready")
        self.assertEqual(t["expected_departure_soc"], 50)
        self.assertTrue(t["floor_feasible"])
        self.assertGreater(t["savings_eur"], 0)
        self.assertAlmostEqual(t["planned_hours"], 13 / 3, places=3)
        self.assertTrue(all(row["heat_pump"] == "baseline" for row in result["timeline"]))

    def test_dst_local_days(self):
        for date, expected in ((datetime(2026, 3, 29).date(), 92), (datetime(2026, 10, 25).date(), 100)):
            now = datetime.combine(date, time(), ZONE).astimezone(UTC)
            snap = snapshot(now)
            self.assertEqual(len(snap["forecast"]["raw_today"]), expected)
            slots, warnings = forecast(snap, now, ZONE)
            self.assertFalse(warnings)
            self.assertEqual(len(slots), expected + 96)
            self.assertTrue(covers(slots, now, slots[-1].end))

    def test_missing_tomorrow_no_fake_guarantee_or_savings(self):
        snap = snapshot()
        snap["forecast"]["tomorrow_valid"] = False
        result = plan(snap, settings())
        t = result["tesla"]
        self.assertEqual(t["status"], "partial")
        self.assertFalse(t["forecast_complete_to_departure"])
        self.assertIsNone(t["savings_eur"])
        self.assertLess(t["expected_departure_soc"], 50)

    def test_price_gaps_not_bridged(self):
        snap = snapshot()
        snap["forecast"]["raw_tomorrow"].pop(8)
        t = plan(snap, settings())["tesla"]
        self.assertEqual(t["status"], "partial")
        gap = instant(snap["forecast"]["raw_tomorrow"][7]["end"])
        for b in t["blocks"]:
            self.assertFalse(instant(b["start"]) <= gap < instant(b["end"]))

    def test_bad_forecast_rejected(self):
        for kind in ("duplicate", "nan", "wrong_day", "units"):
            snap = snapshot()
            if kind == "duplicate":
                snap["forecast"]["raw_today"].append(snap["forecast"]["raw_today"][0])
            elif kind == "nan":
                snap["forecast"]["raw_today"][0]["value"] = float("nan")
            elif kind == "wrong_day":
                snap["forecast"]["raw_today"] = snap["forecast"]["raw_tomorrow"]
            else:
                snap["forecast"]["unit"] = "snt/kWh"
            result = plan(snap, settings())
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["timeline"])

    def test_partial_current_slot_energy(self):
        now = datetime(2026, 9, 22, 22, 7, 30, tzinfo=ZONE).astimezone(UTC)
        slots, _ = forecast(snapshot(now), now, ZONE)
        self.assertEqual(slots[0].hours, 0.125)
        self.assertEqual(slots[0].start, now)

    def test_stale_soc_cannot_be_fixed_by_new_acquisition(self):
        snap = snapshot()
        snap["readings"]["soc"]["reported"] = (instant(snap["acquired_at"]) - timedelta(hours=1)).isoformat()
        result = plan(snap, settings())
        self.assertEqual(result["tesla"]["status"], "stale")
        self.assertFalse(result["tesla"]["blocks"])

    def test_away_and_unplugged(self):
        for key, value in (("home", "not_home"), ("cable", "off")):
            snap = snapshot()
            snap["readings"][key]["value"] = value
            self.assertEqual(plan(snap, settings())["tesla"]["status"], "ineligible")

    def test_current_ceiling_and_office_exclusion(self):
        config = settings()
        config["current_a"] = 16
        with self.assertRaises(ValueError):
            validate_settings(config)
        config = settings()
        config["entities"]["room"] = "sensor.h5102_1e0a_temperature"
        with self.assertRaises(ValueError):
            validate_settings(config)

    def test_capacity_and_stale_p1(self):
        snap = snapshot()
        result = plan(snap, settings(), calibration={"background_amps": [1, 20, 1]})
        self.assertEqual(result["tesla"]["status"], "infeasible")
        self.assertFalse(result["tesla"]["blocks"])
        snap["readings"]["phase_2"]["reported"] = None
        self.assertEqual(plan(snap, settings())["tesla"]["status"], "stale")

    def test_target_reached_needs_no_rate(self):
        snap, config = snapshot(), settings()
        config["soc_gain_per_hour"] = None
        snap["readings"]["soc"]["value"] = "80"
        t = plan(snap, config)["tesla"]
        self.assertEqual(t["status"], "ready")
        self.assertFalse(t["blocks"])
        self.assertEqual(t["planned_grid_kwh"], 0)

    def test_no_calibration_has_candidates_without_completion(self):
        config = settings()
        config["soc_gain_per_hour"] = None
        result = plan(snapshot(), config)
        self.assertEqual(result["tesla"]["status"], "needs_calibration")
        self.assertIsNone(result["tesla"]["expected_departure_soc"])
        self.assertTrue(result["tesla"]["candidate_windows"])
        self.assertEqual(result["hot_water"]["status"], "needs_calibration")

    def test_floor_conflict_and_insufficient_time(self):
        snap = snapshot()
        snap["readings"]["target"]["value"] = "30"
        self.assertEqual(plan(snap, settings())["tesla"]["status"], "infeasible")
        snap = snapshot(datetime(2026, 9, 23, 6, 45, tzinfo=ZONE).astimezone(UTC))
        t = plan(snap, settings())["tesla"]
        self.assertEqual(t["status"], "infeasible")
        self.assertFalse(t["floor_feasible"])

    def test_room_confirmation_and_comfort_priority(self):
        snap, config = snapshot(), settings()
        snap["readings"]["cable"]["value"] = "off"
        for row in snap["forecast"]["raw_today"][-8:]:
            row["value"] = 0.01
        snap["readings"]["room"]["value"] = "18"
        without_guard = plan(snap, config)["heating"]
        self.assertEqual(without_guard["status"], "price_candidate")
        self.assertEqual(without_guard["room_guard"], "not_configured")
        self.assertTrue(without_guard["blocks"])
        config["room_confirmed"] = True
        guarded = plan(snap, config)["heating"]
        self.assertEqual(guarded["status"], "comfort_priority")
        self.assertFalse(guarded["blocks"])
        snap["readings"]["room"]["reported"] = None
        stale_guard = plan(snap, config)["heating"]
        self.assertEqual(stale_guard["status"], "price_candidate")
        self.assertEqual(stale_guard["room_guard"], "unavailable")

    def test_heating_window_obeys_higher_priority_allocations(self):
        now = datetime(2026, 9, 22, 15, tzinfo=ZONE).astimezone(UTC)
        snap, config = snapshot(now), settings()
        for row in snap["forecast"]["raw_today"][60:]:
            row["value"] = 0.01
        model = {"status": "provisional", "qualifying_cycles": 3,
                 "duration_minutes": 105, "electrical_kwh": 2}
        result = plan(snap, config, calibration={"hot_water": model})
        schedules = [result[key]["blocks"] for key in ("tesla", "hot_water", "heating")]
        for index, first in enumerate(schedules):
            for second in schedules[index + 1:]:
                self.assertTrue(all(not (instant(a["start"]) < instant(b["end"])
                                        and instant(b["start"]) < instant(a["end"]))
                                    for a in first for b in second))

    def test_measured_hot_water_reserves_time_before_tesla(self):
        now = datetime(2026, 9, 22, 15, tzinfo=ZONE).astimezone(UTC)
        snap = snapshot(now)
        model = {"status": "provisional", "qualifying_cycles": 3,
                 "duration_minutes": 105, "electrical_kwh": 2}
        result = plan(snap, settings(), calibration={"hot_water": model})
        self.assertEqual(result["hot_water"]["status"], "planned_provisional")
        self.assertTrue(result["hot_water"]["blocks"])
        self.assertTrue(all(not (s["tesla_hours"] and s["hot_water_hours"]) for s in result["timeline"]))
        self.assertGreater(result["hot_water"]["estimated_cost_eur"], 0)

    def test_blocks_do_not_overlap_and_final_partial_block(self):
        now = instant(snapshot()["acquired_at"])
        slots = [Slot(now + timedelta(minutes=15*i), now + timedelta(minutes=15*(i+1)), i % 4 / 10) for i in range(16)]
        selected = allocate(slots, 2.1, 1)
        self.assertAlmostEqual(sum(s.hours for s in selected), 2.1)
        self.assertTrue(all(a.end <= b.start for a, b in zip(selected, selected[1:])))
        for b in blocks(selected)[:-1]:
            self.assertGreaterEqual((instant(b["end"]) - instant(b["start"])).total_seconds(), 3600)


class ObservationTests(unittest.TestCase):
    def test_hot_water_cycles_require_repeated_reach_to_50(self):
        samples = []
        start = datetime(2026, 9, 20, 10, tzinfo=ZONE).astimezone(UTC)
        for d in range(3):
            first = start + timedelta(days=d)
            for minute in range(121):
                energy = 0.3 * min(minute // 15, 4)
                tank = 43 + 2 * min(minute // 15, 4)
                samples.append({"acquired_at": (first + timedelta(minutes=minute)).isoformat(),
                                "readings": {"dhw_kwh": {"value": str(energy)},
                                             "tank": {"value": str(tank)}}})
        self.assertEqual(calibrate_hot_water(samples[:121])["status"], "needs_calibration")
        result = calibrate_hot_water(samples)
        self.assertEqual(result["status"], "provisional")
        self.assertEqual(result["qualifying_cycles"], 3)
        self.assertGreaterEqual(result["duration_minutes"], 90)
        self.assertGreater(result["electrical_kwh"], 1)
    def test_calibration_requires_long_stable_session(self):
        start = instant(snapshot()["acquired_at"])
        samples = []
        for minute in range(121):
            snap = snapshot(start + timedelta(minutes=minute))
            snap["readings"]["soc"]["value"] = str(37 + minute // 20)
            samples.append(snap)
        result = calibrate(samples, settings())
        self.assertGreater(result["soc_gain_per_hour"], 0)
        self.assertLess(result["soc_gain_per_hour"], 3)
        self.assertIsNone(result["background_amps"])
        self.assertIsNone(calibrate(samples[:20], settings())["soc_gain_per_hour"])
        for snap in samples:
            snap["readings"]["current"]["value"] = "5"
        self.assertIsNone(calibrate(samples, settings())["soc_gain_per_hour"])

    def test_phase_background_does_not_double_count_tesla(self):
        start = instant(snapshot()["acquired_at"])
        samples = [snapshot(start + timedelta(minutes=i)) for i in range(361)]
        result = calibrate(samples, settings())
        self.assertEqual(result["background_amps"], [3, 3, 2])

    def test_store_retention_and_duplicate_minute(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "obs.sqlite")
            snap = snapshot()
            store.record(snap)
            store.record(snap)
            self.assertEqual(len(store.samples()), 1)
            store.record(snapshot(instant(snap["acquired_at"]) + timedelta(days=9)))
            self.assertEqual(len(store.samples()), 1)
            store.close()


class AdapterTests(unittest.TestCase):
    def test_forbidden_requests_never_reach_transport(self):
        calls = []
        adapter = HAAdapter("http://example/api", "secret", settings(), lambda *a, **kw: calls.append(a))
        for method, path in (("POST", "/services/switch/turn_on"), ("POST", "/states/number.kurre_charge_current"),
                             ("DELETE", f"/states/{OUTPUT_ENTITY}"), ("GET", "/services")):
            with self.assertRaises(ValueError):
                adapter._request(method, path)
        self.assertFalse(calls)

    def test_snapshot_drops_coordinates_and_uses_report_time(self):
        config = settings()
        raw = [{"entity_id": config["entities"]["home"], "state": "home", "last_reported": "2026-09-22T19:00:00+00:00",
                "last_changed": "2026-01-01T00:00:00+00:00", "attributes": {"latitude": 60, "longitude": 20}}]
        adapter = HAAdapter("http://example/api", "secret", config,
                            lambda *a, **kw: io.BytesIO(json.dumps(raw).encode()))
        snap = adapter.snapshot()
        self.assertNotIn("latitude", json.dumps(snap))
        self.assertEqual(snap["readings"]["home"]["reported"], raw[0]["last_reported"])

    def test_publication_and_unavailable_only_own_entity(self):
        calls = []
        def transport(req, **kwargs):
            calls.append(req)
            return io.BytesIO(b"{}")
        adapter = HAAdapter("http://example/api", "secret", settings(), transport)
        adapter.publish(plan(snapshot(), settings()))
        adapter.unavailable()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(req.full_url.endswith("/states/" + OUTPUT_ENTITY) for req in calls))
        self.assertEqual(json.loads(calls[-1].data)["state"], "unavailable")

    def test_group_freshness_never_uses_commandable_entity_as_evidence(self):
        config = settings()
        old, recent = "2026-09-22T18:00:00+00:00", "2026-09-22T19:00:00+00:00"
        raw = [{"entity_id": config["entities"][key], "state": value,
                "last_updated": stamp, "last_reported": stamp}
               for key, value, stamp in (("target", "80", recent), ("soc", "40", old),
                                          ("energy_added", "1", old))]
        def get():
            return HAAdapter("http://example/api", "secret", config,
                             lambda *a, **kw: io.BytesIO(json.dumps(raw).encode())).snapshot()
        self.assertEqual(get()["readings"]["soc"]["reported"], old)
        raw[-1]["last_updated"] = recent
        self.assertEqual(get()["readings"]["soc"]["reported"], recent)
        self.assertEqual(get()["readings"]["soc"]["entity_reported"], old)
        raw[0]["last_updated"] = "2026-09-22T20:00:00+00:00"
        raw[0]["last_reported"] = "2026-09-22T20:00:00+00:00"
        self.assertEqual(get()["readings"]["target"]["reported"], "2026-09-22T20:00:00+00:00")

    def test_p1_group_refresh_keeps_unchanged_phase_current_usable(self):
        config = settings()
        old, recent = "2026-09-22T18:00:00+00:00", "2026-09-22T19:00:00+00:00"
        raw = [{"entity_id": config["entities"][key], "state": value,
                "last_updated": stamp, "last_reported": stamp}
               for key, value, stamp in (("phase_1", "1", old), ("phase_2", "2", recent),
                                          ("phase_3", "3", old), ("p1_total_power", "0.7", recent))]
        snap = HAAdapter("http://example/api", "secret", config,
                         lambda *a, **kw: io.BytesIO(json.dumps(raw).encode())).snapshot()
        self.assertEqual([snap["readings"][f"phase_{p}"]["reported"] for p in (1, 2, 3)], [recent]*3)
        self.assertEqual(snap["readings"]["phase_1"]["entity_reported"], old)


if __name__ == "__main__":
    unittest.main()

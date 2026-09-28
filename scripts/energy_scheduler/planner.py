"""Pure shadow planner. All times are UTC instants, prices EUR/kWh."""
from datetime import datetime, timedelta, time
import math
from zoneinfo import ZoneInfo

from . import VERSION
from .models import Slot, UTC, instant, numeric, reading


def validate_settings(settings):
    ZoneInfo(settings["timezone"])
    time.fromisoformat(settings["departure"])
    for key in ("current_a", "power_kw", "soft_limit_a", "minimum_block_minutes"):
        value = numeric(settings[key])
        if value is None or value <= 0:
            raise ValueError(f"Invalid {key}")
    if settings["current_a"] > 9 or settings["charging_phase"] not in (1, 2, 3):
        raise ValueError("This shadow release supports at most 9 A on one phase")
    if settings["soft_limit_a"] >= 25:
        raise ValueError("Planning ceiling must remain below the 25 A main-fuse rating")
    if not 0 <= settings["floor_soc"] <= 100:
        raise ValueError("Invalid floor SOC")
    rate = settings.get("soc_gain_per_hour")
    if rate is not None and (numeric(rate) is None or not 0 < float(rate) <= 20):
        raise ValueError("Invalid SOC gain rate")
    if settings["entities"].get("room") == "sensor.h5102_1e0a_temperature":
        raise ValueError("The independently heated office cannot represent heat-pump comfort")
    if numeric(settings["heating_window_hours"]) is None or settings["heating_window_hours"] < 2:
        raise ValueError("Heating windows need a two-hour minimum hold")
    if (numeric(settings["heating_price_spread_eur_kwh"]) is None
            or settings["heating_price_spread_eur_kwh"] < 0):
        raise ValueError("Invalid heating price spread")
    if not 19 <= settings["heating_room_guard_c"] <= 25:
        raise ValueError("Invalid optional room guard")
    if not 0 < settings["heating_shift_c"] <= 0.5:
        raise ValueError("Initial heating shift must be at most 0.5 C")


def deadline(now, zone, clock, weekdays=None):
    local = now.astimezone(zone)
    for offset in range(8):
        date = local.date() + timedelta(days=offset)
        candidate = datetime.combine(date, time.fromisoformat(clock), zone).astimezone(UTC)
        if candidate > now and (weekdays is None or date.weekday() in weekdays):
            return candidate
    raise ValueError("No deadline")


def forecast(snapshot, now, zone):
    raw = snapshot.get("forecast", {})
    if raw.get("unit") != "EUR/kWh":
        raise ValueError("Expected all-in EUR/kWh prices")
    slots, warnings = [], []
    today = now.astimezone(zone).date()
    for key, date in (("raw_today", today), ("raw_tomorrow", today + timedelta(days=1))):
        if key == "raw_tomorrow" and raw.get("tomorrow_valid") is not True:
            warnings.append("Tomorrow prices are not available")
            continue
        periods = raw.get(key, [])
        day_start = datetime.combine(date, time(), zone).astimezone(UTC)
        day_end = datetime.combine(date + timedelta(days=1), time(), zone).astimezone(UTC)
        day = []
        for item in periods:
            start, end = instant(item["start"]), instant(item["end"])
            price = numeric(item["value"])
            if price is None or end - start != timedelta(minutes=15):
                raise ValueError("Invalid quarter-hour price interval")
            if start < day_start or end > day_end:
                raise ValueError("Price interval belongs to a different local day")
            day.append(Slot(start, end, price))
        day.sort(key=lambda s: s.start)
        if any(a.end > b.start for a, b in zip(day, day[1:])):
            raise ValueError("Duplicate or overlapping forecast intervals")
        if (not day or day[0].start != day_start or day[-1].end != day_end
                or any(a.end != b.start for a, b in zip(day, day[1:]))):
            warnings.append(f"Incomplete forecast for {date.isoformat()}")
        slots.extend(Slot(max(s.start, now), s.end, s.price) for s in day if s.end > now)
    return slots, warnings


def covers(slots, start, end):
    cursor = start
    for slot in slots:
        if slot.end <= cursor:
            continue
        if slot.start > cursor:
            return False
        cursor = slot.end
        if cursor >= end:
            return True
    return False


def windows(slots, duration):
    """All contiguous windows of the requested hours, clipped at their end."""
    result = []
    for index in range(len(slots)):
        remaining, candidate = duration, []
        for slot in slots[index:]:
            if candidate and candidate[-1].end != slot.start:
                break
            used = min(slot.hours, remaining)
            candidate.append(Slot(slot.start, slot.start + timedelta(hours=used), slot.price))
            remaining -= used
            if remaining < 1e-8:
                result.append(candidate)
                break
    return result


def allocate(slots, hours, minimum_hours, selected=None):
    """Deterministic cheapest-block heuristic; retain earlier priority allocations."""
    chosen = list(selected or [])
    available = list(slots)
    for taken in chosen:
        available = subtract(available, taken)
    remaining = hours
    while remaining > 1e-8:
        duration = min(minimum_hours, remaining)
        candidates = windows(available, duration)
        if duration < minimum_hours and chosen:
            last_end = max(s.end for s in chosen)
            # A short standalone block is allowed only at final completion;
            # elsewhere it must extend an existing full-length block.
            candidates = [seq for seq in candidates if seq[0].start >= last_end or any(
                seq[0].start == s.end or seq[-1].end == s.start for s in chosen)]
        if not candidates:
            break
        block = min(candidates, key=lambda seq: (
            round(sum(s.price * s.hours for s in seq), 10),
            0 if any(seq[0].start == s.end or seq[-1].end == s.start for s in chosen) else 1,
            seq[0].start))
        chosen.extend(block)
        for taken in block:
            available = subtract(available, taken)
        remaining -= duration
    return sorted(chosen, key=lambda s: s.start)


def subtract(slots, taken):
    result = []
    for slot in slots:
        if taken.end <= slot.start or taken.start >= slot.end:
            result.append(slot)
        else:
            if slot.start < taken.start:
                result.append(Slot(slot.start, taken.start, slot.price))
            if taken.end < slot.end:
                result.append(Slot(taken.end, slot.end, slot.price))
    return result


def blocks(slots):
    result = []
    for slot in sorted(slots, key=lambda s: s.start):
        if result and result[-1]["end"] == slot.start.isoformat():
            result[-1]["end"] = slot.end.isoformat()
        else:
            result.append({"start": slot.start.isoformat(), "end": slot.end.isoformat()})
    return result


def hot_water_plan(snapshot, settings, slots, now, zone, calibration):
    deadline_at = deadline(now, zone, "18:00", (1, 5))
    model = calibration.get("hot_water") or {}
    tank = reading(snapshot, "tank")
    tank_c = numeric(tank.value)
    horizon_start = max(now, deadline_at - timedelta(hours=6))
    latest_end = deadline_at - timedelta(minutes=30)
    duration = (model.get("duration_minutes") or 60) / 60
    eligible_slots = [Slot(max(s.start, horizon_start), min(s.end, latest_end), s.price)
                      for s in slots if s.end > horizon_start and s.start < latest_end]
    candidates = sorted(windows(eligible_slots, duration),
                        key=lambda b: (sum(s.hours * s.price for s in b), b[0].start))[:3]
    result = {"status": "needs_calibration", "deadline": deadline_at.isoformat(),
              "target_c": 50, "temperature_c": tank_c, "reasons": [], "blocks": [],
              "deadline_covered": covers(slots, horizon_start, deadline_at),
              "model": model, "estimated_cost_eur": None,
              "candidate_windows": [{"start": b[0].start.isoformat(), "end": b[-1].end.isoformat(),
                "mean_price_eur_kwh": round(sum(s.price * s.hours for s in b) / duration, 4)}
                for b in candidates]}
    if model.get("status") != "provisional":
        result["reasons"].append("Need three observed cycles reaching about 50 C to estimate heating time and energy")
    elif not tank.fresh(now, settings["freshness_seconds"]["heat_pump"]) or tank_c is None:
        result.update(status="stale", reasons=["Tank temperature is stale or unavailable"])
    elif not result["deadline_covered"]:
        result.update(status="partial", reasons=["Price forecast does not cover the next Tue/Sat deadline"])
    elif not candidates:
        result.update(status="infeasible", reasons=["No contiguous measured-duration window before the deadline"])
    else:
        chosen = candidates[0]
        mean_price = sum(s.hours * s.price for s in chosen) / duration
        result.update(status="planned_provisional", blocks=blocks(chosen),
                      estimated_cost_eur=round(mean_price * model["electrical_kwh"], 4),
                      reasons=["Measured cycle envelope; readiness still depends on draw and tank retention",
                               "Heat pump runs as a baseline load; avoid overlapping proposed Tesla charging"])
        return result, chosen
    return result, []


def space_heating_plan(snapshot, settings, slots, now, reserved):
    """Offer a small price-shift experiment, with optional room veto."""
    room = reading(snapshot, "room")
    confirmed = settings["room_confirmed"]
    room_fresh = confirmed and room.fresh(now, settings["freshness_seconds"]["heat_pump"])
    room_c = numeric(room.value) if room_fresh else None
    room_guard = "available" if room_c is not None else "unavailable" if confirmed else "not_configured"
    horizon_end = now + timedelta(hours=24)
    available = [Slot(s.start, min(s.end, horizon_end), s.price)
                 for s in slots if s.start < horizon_end]
    for booking in reserved:
        available = subtract(available, booking)
    duration = settings["heating_window_hours"]
    candidate_windows = windows(available, duration)
    candidate_windows.sort(key=lambda b: (
        sum(s.price * s.hours for s in b), b[0].start))
    price_hours = sum(s.hours for s in slots if s.start < horizon_end)
    mean_price = (sum(s.price * s.hours for s in slots if s.start < horizon_end) / price_hours
                  if price_hours else None)
    result = {"status": "waiting_forecast", "room_guard": room_guard,
              "room_c": room_c, "minimum_room_c": 19,
              "guard_threshold_c": settings["heating_room_guard_c"],
              "shift_c": settings["heating_shift_c"], "blocks": [],
              "candidate_windows": [], "expected_savings_eur": None,
              "baseline_mean_price_eur_kwh": round(mean_price, 4) if mean_price is not None else None,
              "recommendation": "Retain normal heating", "reasons": []}
    for candidate in candidate_windows[:3]:
        result["candidate_windows"].append({
            "start": candidate[0].start.isoformat(), "end": candidate[-1].end.isoformat(),
            "mean_price_eur_kwh": round(sum(s.price * s.hours for s in candidate) / duration, 4)})
    if room_c is not None and room_c <= settings["heating_room_guard_c"]:
        result.update(status="comfort_priority", reasons=[
            "Representative room is near the 19 C floor; retain normal heating"])
        return result, []
    if not candidate_windows:
        result["reasons"] = ["No uninterrupted two-hour price window after higher-priority loads"]
        return result, []
    chosen = candidate_windows[0]
    chosen_price = sum(s.price * s.hours for s in chosen) / duration
    if (mean_price is not None and mean_price - chosen_price
            < settings["heating_price_spread_eur_kwh"]):
        result.update(status="no_price_advantage", reasons=[
            "Cheapest heating window is too close to the forecast average price"])
        return result, []
    result.update(status="price_candidate", blocks=blocks(chosen),
                  recommendation=f"Shadow only: consider a +{settings['heating_shift_c']:.1f} C active heating-target trial, then return to baseline",
                  reasons=["A price-ranked trial; room comfort and net savings are unverified",
                           "No price-based heating reduction is proposed yet"])
    if room_guard != "available":
        result["reasons"].append("No trusted room-temperature guard is available")
    if not covers(slots, now, horizon_end):
        result["reasons"].append("Forecast does not cover the full 24-hour comparison horizon")
    return result, chosen


def plan(snapshot, settings, now=None, calibration=None):
    validate_settings(settings)
    now = now or instant(snapshot["acquired_at"])
    zone = ZoneInfo(settings["timezone"])
    calibration = calibration or {}
    expiry = (now + timedelta(minutes=18)).isoformat()
    result = {"version": VERSION, "mode": "shadow", "calculated_at": now.isoformat(),
              "valid_until": expiry, "warnings": [], "timeline": [],
              "observations": snapshot.get("readings", {}), "calibration": calibration}
    try:
        slots, warnings = forecast(snapshot, now, zone)
        result["warnings"].extend(warnings)
    except (ValueError, TypeError, KeyError) as error:
        slots = []
        result["warnings"].append(f"Invalid forecast: {error}")
    result["forecast_end"] = slots[-1].end.isoformat() if slots else None
    departure = deadline(now, zone, settings["departure"])
    result["hot_water"], dhw_booking = hot_water_plan(snapshot, settings, slots, now, zone, calibration)
    complete = covers(slots, now, departure)
    tesla = {"status": "needs_calibration", "reasons": [], "departure": departure.isoformat(),
             "floor_soc": settings["floor_soc"], "current_a": settings["current_a"],
             "power_kw": settings["power_kw"], "blocks": [], "expected_departure_soc": None,
             "floor_feasible": None, "target_shortfall_soc": None,
             "estimated_cost_eur": None, "savings_eur": None, "planned_hours": 0,
             "planned_grid_kwh": 0, "latest_start_for_floor": None,
             "forecast_complete_to_departure": complete}
    result["tesla"] = tesla
    required = ("soc", "target", "home", "cable")
    stale = [key for key in required if not reading(snapshot, key).fresh(now, settings["freshness_seconds"]["tesla"])]
    soc, target = (numeric(reading(snapshot, k).value) for k in ("soc", "target"))
    tesla.update({"soc": soc, "target_soc": target})
    eligible = reading(snapshot, "home").value == "home" and reading(snapshot, "cable").value == "on"
    rate = settings.get("soc_gain_per_hour") or calibration.get("soc_gain_per_hour")
    tesla["soc_gain_per_hour"] = rate
    tesla["rate_source"] = "configured" if settings.get("soc_gain_per_hour") else "observed" if rate else "missing"

    currents = [numeric(reading(snapshot, f"phase_{p}").value) for p in (1, 2, 3)]
    p1_fresh = all(reading(snapshot, f"phase_{p}").fresh(now, settings["freshness_seconds"]["p1"]) for p in (1, 2, 3))
    envelope = calibration.get("background_amps")
    result["capacity"] = {"status": "provisional" if p1_fresh else "stale",
                          "present_phase_amps": currents, "background_envelope_amps": envelope,
                          "soft_limit_a": settings["soft_limit_a"],
                          "reason": "Observed background includes normal heat-pump operation; cloud control is not fuse protection"}
    feasible = True
    if envelope:
        forecast_amps = list(envelope)
        forecast_amps[settings["charging_phase"] - 1] += settings["current_a"]
        feasible = max(forecast_amps) <= settings["soft_limit_a"]
        result["capacity"]["projected_phase_amps"] = forecast_amps
    else:
        result["warnings"].append("Insufficient background-load history; capacity is unverified")

    until_departure = [Slot(s.start, min(s.end, departure), s.price) for s in slots if s.start < departure]
    for booking in dhw_booking:
        until_departure = subtract(until_departure, booking)
    candidate_windows = windows(until_departure, settings["minimum_block_minutes"] / 60)
    tesla["candidate_windows"] = [dict(start=b[0].start.isoformat(), end=b[-1].end.isoformat(),
        estimated_cost_eur=round(sum(s.hours * s.price for s in b) * settings["power_kw"], 4))
        for b in sorted(candidate_windows, key=lambda b: (sum(s.hours * s.price for s in b), b[0].start))[:3]]

    selected = []
    if stale:
        tesla.update(status="stale", reasons=["Stale or unavailable: " + ", ".join(stale)])
    elif not eligible:
        tesla.update(status="ineligible", reasons=["Vehicle must be home and plugged in"])
    elif soc is None or target is None or not 0 <= soc <= 100 or not 0 <= target <= 100:
        tesla.update(status="stale", reasons=["Invalid SOC or target"])
    elif target < settings["floor_soc"]:
        tesla.update(status="infeasible", reasons=["Vehicle charge limit conflicts with departure floor"])
    elif soc >= target:
        tesla.update(status="ready", floor_feasible=True, expected_departure_soc=soc,
                     target_shortfall_soc=0, estimated_cost_eur=0, savings_eur=0)
    elif not rate:
        tesla["reasons"].append("Need measured SOC gain per hour at 9 A; windows are price candidates only")
    elif not slots:
        tesla.update(status="partial", reasons=["No usable prices"])
    elif not p1_fresh:
        tesla.update(status="stale", reasons=["P1 readings unavailable or stale"])
    elif not feasible:
        tesla.update(status="infeasible", reasons=["Background envelope leaves insufficient phase capacity"])
    else:
        minimum = settings["minimum_block_minutes"] / 60
        floor_hours = max(0, settings["floor_soc"] - soc) / rate
        target_hours = max(0, target - soc) / rate
        protected_hours = min(target_hours, math.ceil(floor_hours / minimum) * minimum)
        selected = allocate(until_departure, protected_hours, minimum)
        # Extend to the normal target after protecting the floor's chosen intervals.
        selected = allocate(until_departure, max(0, target_hours - sum(s.hours for s in selected)), minimum, selected)
        hours = sum(s.hours for s in selected)
        predicted = min(target, soc + rate * hours)
        floor_feasible = predicted + 1e-6 >= settings["floor_soc"]
        target_met = predicted + 1e-6 >= target
        tesla.update(status="partial" if not complete else "ready" if target_met else "infeasible",
                     blocks=blocks(selected), planned_hours=round(hours, 4),
                     planned_grid_kwh=round(hours * settings["power_kw"], 4),
                     expected_departure_soc=round(predicted, 2), floor_feasible=floor_feasible,
                     target_shortfall_soc=round(max(0, target - predicted), 2),
                     estimated_cost_eur=round(sum(s.hours * s.price for s in selected) * settings["power_kw"], 4))
        tesla["reasons"].append("Conditional estimate at constant observed rate; capacity model is provisional")
        if not target_met:
            tesla["reasons"].append("Insufficient feasible charging time for normal target")
        if not complete:
            tesla["reasons"].append("Forecast does not fully cover departure")
        # Latest start uses the same contiguous-block constraint, not scattered minutes.
        for slot in reversed(until_departure):
            suffix = [s for s in until_departure if s.start >= slot.start]
            late = allocate(suffix, floor_hours, minimum)
            if sum(s.hours for s in late) + 1e-6 >= floor_hours:
                tesla["latest_start_for_floor"] = slot.start.isoformat() if floor_hours else None
                break
        if complete:
            remain, baseline = hours, 0
            for slot in until_departure:
                used = min(slot.hours, remain)
                baseline += used * slot.price * settings["power_kw"]
                remain -= used
            if remain < 1e-6:
                tesla["baseline_cost_eur"] = round(baseline, 4)
                tesla["savings_eur"] = round(baseline - tesla["estimated_cost_eur"], 4)

    result["heating"], heating_booking = space_heating_plan(
        snapshot, settings, slots, now, [*dhw_booking, *selected])
    for slot in slots:
        # All allocations share one timeline. Heat pump remains baseline, not a
        # second synthetic load on top of the measured background envelope.
        portions = [s for s in selected if s.start < slot.end and s.end > slot.start]
        hours = sum((min(s.end, slot.end) - max(s.start, slot.start)).total_seconds() / 3600 for s in portions)
        hot_water_hours = sum((min(s.end, slot.end) - max(s.start, slot.start)).total_seconds() / 3600
                              for s in dhw_booking if s.start < slot.end and s.end > slot.start)
        heating_hours = sum((min(s.end, slot.end) - max(s.start, slot.start)).total_seconds() / 3600
                            for s in heating_booking if s.start < slot.end and s.end > slot.start)
        result["timeline"].append(dict(slot.json(), tesla_hours=round(hours, 6),
            tesla_kwh=round(hours * settings["power_kw"], 6),
            hot_water_hours=round(hot_water_hours, 6),
            space_heating_hours=round(heating_hours, 6),
            heat_pump="hot_water_proposed" if hot_water_hours else
                "space_heating_proposed" if heating_hours else "baseline",
            reason="Hot-water deadline" if hot_water_hours else
                "Tesla price allocation" if hours else
                "Space-heating price candidate" if heating_hours else "Observe normal operation"))
    result["status"] = "observing" if slots else "unavailable"
    return result

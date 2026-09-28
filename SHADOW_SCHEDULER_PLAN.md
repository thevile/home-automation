# Shadow scheduler implementation plan

Status: initial shadow implementation deployed on 2026-09-23. Calibration and
multi-day observation are underway; active control is a later phase.

## Deliverable

Produce an explainable, shared 15-minute schedule for Tesla charging, domestic
hot water, and radiator heating. Publish recommendations and their assumptions
in HA, then observe them against actual operation before enabling controls.

Shadow operation reads existing HA data and publishes optimizer-owned results.
It does not wake the Tesla, change its schedule/current/limit, or write heat-pump
settings. Existing vehicle and heat-pump schedules continue operating normally.
Predicted savings are counterfactual estimates, not realized savings.

## Implementation shape

Use a small Python planning engine with a separate HA adapter. Keep the planner
independent of HA so recorded input snapshots and synthetic cases can exercise
the exact scheduling logic offline. YAML holds HA configuration and dashboard
presentation, not the allocation algorithm.

Proposed files:

- `scripts/energy_scheduler/planner.py`: input validation and allocation.
- `scripts/energy_scheduler/models.py`: typed snapshots, intervals, results.
- `scripts/energy_scheduler/ha_adapter.py`: read allowed entities and publish
  only optimizer-owned results; no actuator service calls.
- `scripts/energy_scheduler/run.py`: one-shot calculation and periodic runner.
- `home-assistant/energy_scheduler/settings.json`: non-secret entity mapping,
  deadlines, current ceiling, model assumptions, and freshness limits.
- `home-assistant/apps/energy_shadow/`: supervised Home Assistant app.
- `home-assistant/dashboards/energy_scheduler.json`: separate recommendation view.
- `tests/test_energy_scheduler.py`: meaningful scheduling and failure cases.

First run locally with saved snapshots, then with read-only live inputs. Before
continuous deployment, inspect the HA installation and select a supported,
supervised runtime (HA app/container or another always-on host). SSH access
alone does not establish a suitable runtime. A laptop run is a development
session, not an overnight scheduling service.

Prefer no external dependencies for the planning core. Keep credentials in the
existing ignored local secret location or the deployed runtime's secret store.
Bound logs and snapshot retention; omit coordinates, tokens, and unrelated
entities. Store derived home/away eligibility rather than GPS coordinates.

## Input contract and known settings

Entity IDs are mapped in `ENERGY_OPTIMIZATION_PLAN.md`.

| Input | Initial policy |
| --- | --- |
| All-in price | EUR/kWh from Nord Pool `raw_today` and valid `raw_tomorrow`; do not add tariffs again |
| Time | Europe/Helsinki for deadlines; timezone-aware instants for interval arithmetic |
| Tesla departure | Next 07:00 local time |
| Tesla minimum / target | 40% floor; current vehicle limit as target (last observed 80%) |
| Present charging | Single-phase, phase 2, 9 A operating ceiling; approximately 2 kW observed |
| Tesla eligibility | Home and plugged in, with usable source data |
| Supply | 25 A per phase; provisional planning ceiling 22 A, not a protection guarantee |
| Room comfort | Optional trusted sensor vetoes price shifting at 19.5 °C; 19 °C floor cannot be verified without it; office excluded |
| Hot water | Current baseline Eco 45 °C / Comfort 50 °C; Tue/Sat ready by 18:00 |

The 16 A entity maximum is not permission to increase the current above 9 A.
Initially propose only 0 A or 9 A. Lower-current operation requires measurement.
Phase 2 was inferred from a live test; recheck after charger or wiring changes.

Record acquisition time and source freshness separately. Reading HA now does
not make cached Tesla data fresh. `last_changed` is not a freshness timestamp:
an unchanged reading can still be current. Inspect available source timestamps
and polling behavior during implementation, and expose uncertainty explicitly.
Provisional freshness settings: P1 two minutes; heat-pump feedback five minutes;
Tesla twenty minutes for an actionable recommendation. Validate these against
actual reporting cadence. Sleeping-car estimates may remain visible but must be
marked stale and cannot establish readiness or home/plugged-in eligibility.

## Forecast and recalculation

- Calculate at startup, every 15-minute boundary, and on material changes to
  forecast, cable/home status, SOC, target, or configured deadlines.
- Debounce input bursts, serialize calculations, and avoid repeated Tesla polls.
- Use all available valid intervals through the end of the forecast. Evaluate
  only deadlines within that horizon; flag deadlines beyond it as uncovered.
- Check finite prices, positive durations, ordering, overlap, duplicates, and
  gaps. Negative and zero prices are valid. Never pad missing prices with zero.
- Validate complete local days by timestamps; DST days can have 92 or 100
  quarter-hours rather than 96. Treat repeated local clock times as distinct
  instants and handle overnight charging across midnight correctly.
- Clip the current interval to the remaining time. Calculate energy and cost
  using actual duration, not an assumed full quarter-hour.
- If tomorrow is missing, show the known partial plan and any unresolved
  departure need. Do not label a partial forecast a fully optimized schedule.

## Allocation rules

One planner owns the shared timeline. Each interval records consumer demand,
assumed per-phase background load, available capacity, and allocation reasons.
The priority order remains measured room-comfort guard (when available), urgent
hot water, minimum Tesla charge, normal Tesla target, then optional space
preheating. Without a trusted room sensor, baseline heating is preserved.

### Tesla: first operational model

1. Determine home/plugged-in eligibility and the next departure deadline.
2. Calculate separately the charging time needed for the 40% floor and the
   configured target. Respect a configured target below the floor by reporting
   the conflict, rather than silently changing the vehicle setting.
3. Estimate SOC gain per hour from observed stable sessions when available.
   Until then require an explicitly configured usable-capacity/efficiency
   estimate, labelled provisional. Without either model, offer price-ranked
   candidate windows but no invented completion time.
4. Reserve feasible time for the minimum SOC first, then allocate remaining
   time toward the target. Compare block costs using all-in prices.
5. Start with a configurable 60-minute minimum charging block (allow a shorter
   final block when completing a target/deadline). Use deterministic allocation
   that minimizes cost subject to these rules, preferring fewer starts and
   earlier completion on ties. This is a bounded planning policy, not a claim
   of globally optimal control.
6. Compute a latest feasible start and remaining-time margin for the floor.
   If the floor or target is unattainable, report the expected departure SOC,
   shortfall, and reason. Never claim an unconditional 07:00 guarantee.

At roughly 2 kW, an overnight window may not reach 80%. The planner must display
that constraint, not assume the normal target is reachable. Battery conversion
losses matter only for time/energy estimates; an empirical SOC-gain model can
absorb them without a separate loss model.

### Heat pump: included from the first version

Collect hot-water temperature, outside temperature, operating settings,
compressor starts, and available energy counters from the start.

Hot water: identify existing heating cycles and estimate a conservative cycle
duration and electrical demand. Confirm how to distinguish hot-water production
from space heating. Whole-house power and COP alone cannot reliably isolate it.
The deployed app uses the dedicated daily hot-water electrical-energy counter
and tank temperature to identify completed cycles. After three cycles reaching
about 50 °C, it uses the slowest observed duration plus 30 minutes and a
conservative electrical-energy envelope. Until then, report
`needs_calibration` with candidate cheap windows; do not claim 50 °C readiness.
Once calibrated, reserve a contiguous window ending before 18:00 Tue/Sat with
a 30-minute completion buffer, while keeping phase demand provisional.
Select among windows in a limited look-ahead period (initially six hours) to
avoid assuming a tank heated much earlier remains ready despite usage. Record
temperature-at-deadline predictions as uncertain until tested.

Space heating: retain normal operation as the baseline and independently rank
the cheapest contiguous two-hour heating window after reserving hot-water and
Tesla time. When there is meaningful price spread, show a small +0.5 °C
heating-target trial followed by a return to baseline. This recommendation
does not depend on room temperature. A confirmed, fresh representative room
sensor may veto the shift at or below 19.5 °C; stale or absent room data is
reported as an unavailable guard, not as proof of comfort. Never substitute the
office sensor. No price-based heating reduction or heating-curve change is
proposed yet. Do not assign precise heat-storage capacity, COP benefit, or
savings without supporting measurements.

Do not model the same heat pump as simultaneously producing space heating and
hot water. Reserve its capacity once; expose uncertain phase demand explicitly.

### Household capacity

Use P1 history to build a conservative background-load envelope per phase.
Fresh P1 values provide a present-time check, not a forecast for the entire
night. Subtract identifiable existing Tesla demand before adding simulated
Tesla demand so it is not counted twice. Avoid counting modeled heat-pump
demand both in background load and as a new allocation.

Without sufficient load history, show capacity feasibility as provisional.
Compare predicted and observed headroom throughout the shadow period.

Cloud Tesla commands and a predicted 22 A ceiling are not dependable fuse
protection. Before active control, establish a conservative electrical loading
limit or local charger load balancing that remains effective during HA/network
outages. Cloud-based current reduction is supplementary coordination.

## Outputs and observation

Publish a small summary and a detailed rolling schedule under an optimizer-owned
namespace. Proposed summary fields:

- Calculation time, input freshness, model version, forecast coverage.
- Per-consumer status: ready, partial, stale, ineligible, infeasible, or
  needs_calibration, with explicit reasons.
- Tesla next recommended block, planned hours, expected departure SOC, floor
  feasibility, target shortfall, and estimated grid kWh/cost.
- Hot-water next deadline and model confidence; heating baseline/eligibility.
- Per-phase capacity assumptions and unresolved conflicts.

Detailed intervals include start/end, price, consumer allocations, estimated
power/energy, and reason. Bound payload size and retain revisioned snapshots
outside entity history where needed. Mark results unavailable when the runner
stops updating; restart must not leave an old recommendation appearing current.

The HA view shows the price timeline, proposed consumer blocks, deadlines,
actual consumption, and limitations. Compare Tesla cost with charging as soon
as eligible at the same current, delivered energy, capacity constraints, and
deadline. If either scenario cannot deliver that energy or lacks prices, report
the comparison as unavailable. Keep hot-water/space-heating savings unestimated
until their models support a fair comparison.

## Implementation and acceptance checklist

- [x] Inspect source freshness, usable heat-pump energy/cycle signals, runtime,
      and select the publication mechanism.
- [x] Implement snapshot schema and pure planner with offline fixtures.
- [x] Verify negative prices, missing tomorrow, DST transitions, partial slots,
      target already met, insufficient time, stale inputs, and capacity conflicts.
- [x] Verify office exclusion, optional room guard, heat-pump non-overlap, and
      no double-counted load.
- [x] Add HA input/publication adapter and prove shadow mode cannot issue device
      commands; validate unavailable behavior.
- [x] Verify app restart retains observations and republishes a current plan.
- [x] Deploy the supervised shadow runner and recommendation view.
- [ ] Observe at least seven days, including overnight charging and Tue/Sat
      hot-water deadlines. Extend observation if qualifying cycles are absent.
- [ ] Compare estimated charging duration/energy and load headroom with actual
      measurements; record errors and adjust assumptions.
- [ ] Review evidence before enabling any consumer's controls separately.

The initial implementation is intentionally conditional: it cannot infer a
charging completion time before observing a stable session at 9 A, nor promise
hot-water readiness before calibrating a production cycle. A sleeping vehicle
keeps its last known values in HA but is marked stale in recommendations.

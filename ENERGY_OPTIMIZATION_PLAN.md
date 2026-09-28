# Electricity Consumption Optimization Plan

## Goal

Minimize the variable electricity cost of space heating, domestic hot water,
and Tesla Model Y charging without compromising comfort, hot-water
availability, vehicle readiness, or the house's 25 A main fuses.

Scheduling uses the all-in quarter-hour price from
`sensor.nordpool_kwh_fi_eur_3_10_0255`, including spot price, retailer margin,
electricity tax, and time-dependent distribution tariff.

## Progress

- [x] Migrate Nord Pool from a UI config entry to version-controlled YAML.
- [x] Apply the retailer margin of 0.430 cents/kWh, VAT included.
- [x] Apply the current Finnish electricity tax and VAT.
- [x] Apply distribution rates of 4.32 cents/kWh from 07:00 to 22:00 and 1.46
      cents/kWh from 22:00 to 07:00, both VAT included.
- [x] Verify all 96 quarter-hour prices and both tariff transitions in HA.
- [x] Fix the Default dashboard units and incomplete-tomorrow behavior.
- [x] Establish SSH deployment access to `homeassistant.local`.
- [x] Add the official Tesla Fleet integration and inventory its entities.
- [x] Deploy a read-only shared shadow scheduler and HA recommendation view.
- [ ] Complete seven-day shadow observation and review.
- [ ] Enable Tesla charging control.
- [ ] Enable domestic-hot-water control.
- [ ] Enable conservative space-heating control.

## Confirmed constraints

### Electrical supply

- Main fuses: 25 A per phase.
- P1Mini32 provides whole-house power and per-phase current measurements.
- A future three-phase charger may draw up to 16 A per phase (about 11 kW),
  leaving limited headroom for the heat pump and other loads.
- Price optimization must never replace dynamic fuse protection.

### Tesla Model Y

- Control charging through the vehicle, independent of charger brand.
- Normal departure deadline: 07:00.
- Guaranteed minimum state of charge at departure: 40%.
- Use the vehicle's configured charge limit as the normal target initially.
- Tesla Fleet is integrated with Home Assistant as `Kurre`.
- Current integration values: 43% state of charge, 80% charge limit, and a
  configured charging-current ceiling of 16 A.
- The present charger is single-phase. Live control was verified at 9 A: HA
  changed the current, started charging at about 2 kW on phase 2, and stopped
  charging successfully.

### Space heating

- Heat distribution uses radiators.
- The 19 °C floor is an optional room-sensor guard when a suitable sensor is
  confirmed and fresh. Without one, the scheduler cannot verify room comfort.
- `sensor.h5102_1e0a_temperature` is in an independently heated office. Never
  use it for heat-pump optimization or its temperature safety override.
- The location and suitability of `sensor.h5102_bfe2_temperature` as the
  optional representative heat-pump-heated room sensor needs confirmation.

### Domestic hot water

- Current Eco setpoint: 45 °C; current Comfort setpoint: 50 °C.
- Hot water is especially needed Tuesday and Saturday evenings.
- On Tuesday and Saturday it must be ready by 18:00.
- No absolute minimum tank temperature has been specified. Retain a
  conservative availability fallback and preserve the controller's built-in
  hygiene/legionella program.

## Entity map

Price forecast:

- `sensor.nordpool_kwh_fi_eur_3_10_0255`
  - `raw_today`
  - `raw_tomorrow`
  - `tomorrow_valid`

Electrical load:

- `sensor.current_phase_1`
- `sensor.current_phase_2`
- `sensor.current_phase_3`
- `sensor.momentary_active_import`
- `sensor.momentary_active_import_phase_1`
- `sensor.momentary_active_import_phase_2`
- `sensor.momentary_active_import_phase_3`

Heat-pump controls:

- `select.stiebel_eltron_wpf_10_heating_operating_mode_setting`
- `number.stiebel_eltron_wpf_10_heating_comfort_setpoint`
- `number.stiebel_eltron_wpf_10_heating_eco_setpoint`
- `number.stiebel_eltron_wpf_10_heating_curve_slope_setting`
- `number.stiebel_eltron_wpf_10_hot_water_comfort_setpoint`
- `number.stiebel_eltron_wpf_10_hot_water_eco_setpoint`

Relevant feedback:

- `sensor.stiebel_eltron_wpf_10_heating_write_status`
- `sensor.stiebel_eltron_wpf_10_hot_water_write_status`
- `sensor.stiebel_eltron_can_hot_water_temperature`
- `sensor.stiebel_eltron_can_outside_temperature`
- `sensor.stiebel_eltron_can_compressor_starts`
- `sensor.stiebel_eltron_can_cop_heating`
- `sensor.stiebel_eltron_can_cop_hot_water`

Tesla controls and feedback:

- `device_tracker.kurre_location`
- `binary_sensor.kurre_charge_cable`
- `sensor.kurre_battery_level`
- `sensor.kurre_charging`
- `sensor.kurre_charger_current`
- `sensor.kurre_charger_power`
- `sensor.kurre_charge_energy_added`
- `sensor.kurre_time_to_full_charge`
- `number.kurre_charge_current`
- `number.kurre_charge_limit`
- `switch.kurre_charge`

## Shared scheduler

The optimizer operates on 15-minute intervals and builds a rolling schedule
from `raw_today` plus `raw_tomorrow` only when `tomorrow_valid` is true.
Incomplete forecasts cause conservative fallback behavior.

All consumers share one schedule. Independent cheapest-price automations could
select the same intervals, overload a phase, and create a new demand peak.

Priority during a capacity conflict:

1. Honor the 19 °C floor when measured by a trusted room sensor; otherwise
   preserve baseline heating rather than inferring that comfort is safe.
2. Meet a near-term hot-water deadline.
3. Guarantee at least 40% Tesla charge by 07:00.
4. Charge the Tesla toward its normal target.
5. Opportunistically preheat the house.

Tesla charging is reduced or stopped first when fuse headroom is insufficient.
The heat pump is not abruptly interrupted solely for load management.

## Tesla policy

Calculate required charging time from current and target state of charge,
usable battery capacity, measured or expected charging power, and time until
07:00.

- Reserve enough feasible intervals to guarantee 40% by departure.
- Use the cheapest remaining intervals to approach the normal charge limit.
- Prefer contiguous blocks to avoid unnecessary start/stop commands.
- Naturally account for the night distribution advantage in the all-in price.
- Do not wake or poll the vehicle more often than necessary.
- Treat missing or stale P1 measurements as a reason to pause discretionary
  charging.

Initial limits for a future three-phase charger:

- Charger ceiling: 16 A per phase.
- Normal house soft limit: 22 A on every phase.
- Protective reduction/stop threshold: tune below the 25 A fuse rating.
- Confirm the charger's minimum current; normally it is 6 A per phase.

## Domestic-hot-water policy

- On Tuesday and Saturday, select the cheapest sufficiently long window that
  completes heating by 18:00 and temporarily target 50 °C.
- Return to the normal 45 °C target after the boost window.
- On other days, prefer the cheapest suitable interval while retaining a
  conservative availability fallback.
- Use contiguous windows; do not change setpoints every 15 minutes.
- Confirm controller read-back after every setting change.
- Preserve the heat pump's built-in hygiene/legionella schedule.
- A near-term 18:00 deadline takes priority over discretionary Tesla charging,
  subject to electrical limits and any available room-temperature guard.

The observation phase must determine tank heating time and whether raising the
active Eco setpoint reliably starts a heating cycle.

## Space-heating policy

Radiators offer less useful thermal storage than underfloor heating, so begin
conservatively.

- Do not change the heating-curve slope during the initial rollout.
- Initially propose only a small +0.5 °C heating-target trial in a cheap
  two-hour window, then return to the established baseline. This is a trial,
  not a demonstrated saving; price-based reductions need comfort evidence.
- The planner may rank heating windows without a room sensor. If a confirmed,
  fresh representative sensor is at or below 19.5 °C, keep normal heating
  instead. An unavailable sensor does not imply that the room is above 19 °C.
- Use a minimum mode or setpoint hold time of about two hours.
- Avoid aggressive shifting during cold weather or when recovery would require
  inefficient high water temperatures.
- Monitor compressor starts, COP, energy use, and room temperature when
  available. Without room feedback, comfort cannot be guaranteed from HA.
- Never use the independently heated office sensor for these decisions.

## Rollout

### Phase 1: Tesla integration

- [x] Configure official Tesla Fleet with Vehicle Information, Vehicle
      Location, and Vehicle Commands scopes.
- [x] Verify the virtual key by successfully issuing a charging command while
      the vehicle is connected.
- [x] Confirm battery, cable, charging, current, limit, and charge-switch
      entities.
- [x] Confirm the present charger is single-phase and verify control at the
      current 9 A setting (about 2 kW on phase 2).
- [ ] Confirm the present charger's real minimum current if dynamic current
      reduction below 9 A is enabled later. HA reports a 16 A maximum; the
      entity's nominal 0 A minimum is not proof of the charger's lowest stable
      charging current.

### Phase 2: Shadow scheduler

- [x] Write the [shadow scheduler implementation plan](SHADOW_SCHEDULER_PLAN.md).
- [x] Calculate proposed allocations without issuing commands.
- [x] Show selected intervals, reasons, deadlines, expected energy, and cost.
- [x] Report incomplete forecasts and fallback decisions explicitly.
- [ ] Compare proposals with actual household load for several days.

### Phase 3: Tesla control

- [ ] Enforce the 07:00/40% guarantee.
- [ ] Add per-phase P1 fuse protection.
- [ ] Respect manual overrides.
- [ ] Record planned versus delivered energy and estimated savings.

### Phase 4: Hot-water control

- [ ] Measure typical tank heating duration and electrical consumption.
- [ ] Test one manual setpoint-controlled boost with read-back.
- [ ] Enable Tuesday/Saturday boosts completing by 18:00.
- [ ] Add ordinary-day scheduling and availability fallback.

### Phase 5: Space-heating control

- [ ] Optionally confirm a representative heated-room sensor for comfort limits.
- [ ] Establish an unoptimized comfort and energy baseline.
- [ ] Test small manual Comfort/Eco shifts.
- [ ] Enable shadow recommendations, then guarded control.
- [ ] Tune using comfort, outside temperature, COP, and compressor starts.

## Safety and fallback rules

- Every automation has a master enable/disable helper.
- Manual changes suspend optimization for a defined override period.
- Commands require fresh source data and controller read-back where available.
- Failed writes, stale sensors, unavailable prices, incomplete forecasts, or
  unavailable controllers cause safe fallback rather than repeated writes.
- HA startup does not issue changes until all source entities are available.
- Restore documented baseline setpoints and modes when optimization ends or is
  disabled.
- Start in shadow mode and enable one controllable load at a time.
- Tesla cloud commands provide supplementary load coordination, not reliable
  fuse protection during network/HA outages. Before active control, establish
  conservative electrical limits or local charger load balancing.

## Open decisions

- Which room contains `sensor.h5102_bfe2_temperature`, and can it serve as an
  optional comfort guard? This is not a prerequisite for shadow suggestions.
- Should the daily Tesla target differ from the vehicle's configured limit?
- Present charger: single-phase at 9 A, approximately 2 kW; retain this ceiling.
  What measured SOC gain rate should the charging-time model use?
- Which supported always-on runtime should host the shadow scheduler?
- What ordinary-day hot-water fallback should apply after observing usage?
- How long should manual overrides remain active?

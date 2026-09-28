# Energy shadow operations

The local Home Assistant app `local_energy_shadow` runs the Python planner every
minute. It reads HA's existing state cache, records selected observations in a
bounded SQLite database under the app's persistent `/data`, and publishes its
own `sensor.energy_shadow_raw` on each 15-minute boundary or material Tesla/
price change. It never calls HA device services. Home Assistant OS starts the
app at boot. If publication stops, the dashboard marks the plan expired after
18 minutes.

The separate `energy-shadow` dashboard is installed from
`home-assistant/dashboards/energy_scheduler.json`. Updating it saves a timestamped
backup in ignored `.local/dashboard-backups/` and verifies read-back. No Home
Assistant restart is required for app or dashboard updates.

Configuration lives in `settings.json` beside this file. It currently caps
proposed charging at 9 A on phase 2. Do not raise that limit merely because HA
advertises a larger number-entity maximum. Heating price windows are ranked
without a room sensor. Keep `room_confirmed` false until a representative
heat-pump-heated room sensor is identified; then a fresh reading at or below
19.5 °C vetoes price shifting. A missing reading removes that guard, so the
recommendation remains provisional. The office sensor is explicitly rejected.

To refresh the app after a code/settings change, stage it into a new directory
under ignored `.local/`, copy the staged contents to `/addons/energy_shadow/`,
then run `ha apps rebuild local_energy_shadow` and
`ha apps restart local_energy_shadow` over SSH. To refresh the dashboard run
`node scripts/ha-install-shadow-dashboard.mjs` locally. Check
`ha apps logs local_energy_shadow` and the `sensor.energy_shadow_raw` timestamp.

The first Tesla charging estimate requires a stable observed 9 A session long
enough for battery SOC to rise by at least 3 percentage points. Until then the
view shows price candidates with no completion claim. Hot water needs three
observed cycles that reach roughly 50 °C before a Tue/Sat boost window is
proposed. Space heating offers a two-hour price candidate without a room sensor;
it needs thermal-response and comfort observation before active control. A
sleeping car's old HA values remain marked stale rather than triggering a wake
command.

For local verification, run `python3 tests/test_energy_scheduler.py` and use
`PYTHONPATH=scripts python3 -m energy_scheduler.run --settings
home-assistant/energy_scheduler/settings.json --token-file .local/ha-token
--summary` for a live read-only calculation.

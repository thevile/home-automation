This is an ESPHome configuration for an MCP2515 CAN-bus interface to a Stiebel
Eltron heat pump. Build and wiring background is in the original Home Assistant
[forum discussion](https://community.home-assistant.io/t/configured-my-esphome-with-mcp2515-can-bus-for-stiebel-eltron-heating-pump/366053/16).

## Configuration notes

If the heat pump does not respond, its CAN ID, this ESP's `0x680` identity, or
the 20 kbps bus rate may differ. Keep `internalResponse_id` and the `canbus`
CAN ID in sync. The WPM's read/write address bytes are configured as
`PumpCANread_id` and `PumpCANwrite_id`; the FEK read address is
`FekCANread_id`.

## Domestic hot water controls

**Hot water comfort setpoint** and **Hot water eco setpoint** expose numeric
controls in Home Assistant, matching the heating controls: 25–60 °C in 0.1 °C
steps, with controller-confirmed state. They refresh every 30 seconds and share
the heating controls' write lock and 90-second recent-reading requirement.
**Hot water write status** reports read-back match, mismatch, or timeout.
These use the existing hot water addresses (Comfort `0x0013`, Eco `0x0a06`
at `0x180`), with no automatic number writes on boot or restoration.
The new controls compile but still need verification on the heat pump after upload.

The legacy PV automation example and its input-text helper listeners have been
removed. Future Nord Pool spot-price automation can use these numeric controls
in degrees Celsius. No price-based automation is configured yet. The shared
write script handles one setting at a time; allow at least six seconds between
setting changes and check the write status.

## Safe heating controls

**Heating operating mode setting** is a writable dropdown for Emergency,
Standby, Automatic, Comfort, Eco, and Hot water only. It shares the temperature
controls' write lock, recent-read requirement, and read-back status. Comfort/Eco
read mappings are panel-confirmed; other mode labels come from the reference
table, and mode writes still need hardware testing. Start with Comfort/Eco.

This configuration no longer exposes a generic Home Assistant service or
button for arbitrary CAN frames. Writable parameters are explicitly
limited to named settings. Heating Comfort, Eco, and curve slope retain their
read-only sensors and now have separate guarded number controls with read-back
status. Readings refresh from manager 0x480 every 30 seconds. External heating
writes are ready for manual testing but have not yet been verified on hardware.

See [WPF10_HEATING_CONTROLS.md](WPF10_HEATING_CONTROLS.md) for the verified
WPF 10 mapping, safety behaviour, reference snapshots, and parameters still
awaiting model-specific verification.

## Telemetry fixes

Hot water temperature and Storage ACTUAL temperature now publish the same
controller reading (`0x000e`), accepting short and extended replies. The former
hot water handler expected the wrong framing and added an undocumented 3.9 °C
offset from the original installation; that offset is removed.

Daily energy counters and Compressor starts publish as soon as both counter
parts arrive, including valid zero values. Previously, publication waited for
the next polling cycle; compressor counts also required both parts to be
positive. Daily energy polling is every 15 minutes, including heating
consumption (previously six hours). These sensors are requested on boot and
by the Sensor update button. Partial pairs are cleared before a new poll.

The daily energy indices match the vendored Elster table (`0x091a/b`,
`0x091e/f`, `0x092a/b`, `0x092e/f`). Compressor indices `0x071c/d` are retained
from the original configuration. Although the reference table calls them test
objects, the live controller returned a combined count of 71,744 after upload
on 2026-09-09 (not yet compared with the physical panel). All six previously
missing readings appeared in Home Assistant: hot water 45.4 °C; daily hot water
consumption/production 2.458/4.931 kWh; daily heating consumption/production
2.140/10.530 kWh; and compressor starts 71,744. Logs confirmed the expected
firmware build. These observations verify reception and publication, not the
physical accuracy of the counters.

Run `python3 tests/test_can_replies.py` to exercise the actual C++ reply handlers
with short/extended temperature frames, reversed counter reply order, zero
counts, incomplete pairs, invalid values, and malformed/address-mismatched
frames. Run `esphome compile sensor_node.conf` to build the firmware.

## Home Assistant energy optimization

Version-controlled Home Assistant configuration is under `home-assistant/`.
The staged plan for coordinating Nord Pool pricing, heat-pump heating,
domestic hot water, and Tesla charging is in
[ENERGY_OPTIMIZATION_PLAN.md](ENERGY_OPTIMIZATION_PLAN.md).

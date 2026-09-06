This is an ESPHome configuration for an MCP2515 CAN-bus interface to a Stiebel
Eltron heat pump. Build and wiring background is in the original Home Assistant
[forum discussion](https://community.home-assistant.io/t/configured-my-esphome-with-mcp2515-can-bus-for-stiebel-eltron-heating-pump/366053/16).

## Configuration notes

If the heat pump does not respond, its CAN ID, this ESP's `0x680` identity, or
the 20 kbps bus rate may differ. Keep `internalResponse_id` and the `canbus`
CAN ID in sync. The WPM's read/write address bytes are configured as
`PumpCANread_id` and `PumpCANwrite_id`; the FEK read address is
`FekCANread_id`.

## Domestic hot water automation

The existing `input_text.ww_komfort_temp` and `input_text.ww_eco_temp` helpers
expect tenths of a degree Celsius: `550` means 55.0 °C. Their accepted range is
250–600. They remain compatible with the original PV automation.

## Safe heating controls

This configuration no longer exposes a generic Home Assistant service or
button for arbitrary CAN frames. Writable parameters are explicitly
limited to named settings. Heating Comfort, Eco, and curve slope retain their
read-only sensors and now have separate guarded number controls with read-back
status. Readings refresh from manager 0x480 every 30 seconds. External heating
writes are ready for manual testing but have not yet been verified on hardware.

See [WPF10_HEATING_CONTROLS.md](WPF10_HEATING_CONTROLS.md) for the verified
WPF 10 mapping, safety behaviour, reference snapshots, and parameters still
awaiting model-specific verification.

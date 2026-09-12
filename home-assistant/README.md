# Home Assistant configuration

This directory contains version-controlled Home Assistant configuration. It
does not contain Home Assistant backups, `.storage`, databases, logs, or
secrets.

## Enable packages

Copy `packages/` into the Home Assistant configuration directory and ensure
the main `configuration.yaml` contains:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Merge the `packages` line into an existing `homeassistant:` block rather than
adding a second `homeassistant:` key.

## Nord Pool UI-to-YAML migration

The existing Nord Pool entry was created through the UI. Do not enable the
YAML package while that entry is active, because both configurations would
create a price sensor.

1. Copy `packages/electricity_price.yaml` to `/config/packages/`.
2. Add the packages include shown above, but do not restart yet.
3. In **Settings > Devices & services > Integrations > Nord Pool**, delete the
   UI-created Nord Pool integration entry. Disabling only its entity is not
   sufficient.
4. Run **Developer tools > YAML > Check configuration**.
5. Restart Home Assistant.
6. Confirm that the new sensor has 96 entries in `raw_today`, uses `EUR/kWh`,
   reports `additional_costs_current_hour` as `0.07728`, and tracks the old
   sensor's price.

The generated entity ID may differ from the existing
`sensor.nordpool_kwh_fi_eur_3_10_0255`. Update dashboards or automations only
after checking the new entity in Home Assistant. Renaming the old entity to a
stable ID such as `sensor.nordpool_price` before migration can make the switch
easier, but Home Assistant may retain the entity registry record until the old
integration is removed.


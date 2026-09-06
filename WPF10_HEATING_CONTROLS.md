# WPF 10 heating controls

## Operating-mode read verification

The panel test in `mode-test.log` confirmed `PROGRAMMSCHALTER` at manager
`0x480`, Elster index `0x0112`. The WPF changed `0x0400` (Eco) to `0x0300`
(Comfort) and back; the panel-originated frame and subsequent read replies
agreed. The current firmware exposes this as the read-only **Heating operating
mode** text sensor and polls it with the three heating values every 30 seconds.

Comfort and Eco are confirmed on this WPF 10. The remaining display labels are
inferred from the matching generic Elster table and official WPM mode enum:
`0x0000` Emergency, `0x0100` Standby, `0x0200` Automatic, and `0x0500` Hot
water only. Unknown values are published with their raw hexadecimal code. Mode
writes are not exposed. The temporary two-second probe and wildcard logger
have been removed.

## Current firmware: guarded editable settings for manual testing

The three existing read-only sensors remain. New numbers are Heating comfort
setpoint (10–30 °C, step 0.1), Heating eco setpoint (10–30 °C, step 0.1), and
Heating curve slope setting (0–3, step 0.01). Temperature bounds are a
conservative application policy; actual controller limits remain unverified.

All writes target manager 0x480 (90 00); reads use 91 00. A shared single-mode
script allows only indices 0005, 0008 and 010e, rejects non-finite/off-step/out-of-
range input, and requires a reading within 90 seconds. Concurrent requests are
ignored by the script with an ESPHome warning. No boot/restored value is written.
After one write it waits 750 ms, requests the same index, then compares the raw
reply; timeout is five seconds after the read. Heating write status reports
verification, mismatch or timeout. No automatic write retry or rollback occurs.
The number states come only from controller responses. Regular polling pauses
during the write script; it resumes afterward.

External write acceptance is still untested. First upload, wait for values,
then set Comfort 20.0 → 20.1, verify the status and physical panel, and restore
20.0. Wait at least six seconds between commands. Only then test Eco and slope
separately, restoring each original value. Read-back confirms reported state,
not power-cycle persistence. The sections below are historical.

## Current firmware: read-only Home Assistant sensors

The panel experiment in `heating-test.log`, confirmed by the user, established
Comfort `0x0005` (20.0 → 20.1 → 20.0 °C), Eco `0x0008`
(18.0 → 18.1 → 18.0 °C), and slope `0x010e` (0.75 → 0.76 → 0.75).
All three tracked panel changes in replies from manager `0x480`.

The current firmware exposes these as template **sensors**, reads them on boot
and every 30 seconds, and accepts only seven-byte replies from `0x480` addressed
to this ESP node. Temperature scaling is /10; slope is /100. The Sensor update
button also refreshes all three. No initial zero is published before a reply.
Unsupported raw values 0x8000/0xffff are ignored.

The heating writer, number entity, write diagnostics, and temporary raw traffic
logger/probes have been removed. Existing domestic hot water controls remain.
External heating writes have not been tested. The sections below describe
superseded implementation and research history, not current capabilities.

## Diagnostic build — correction to the assessment below

The earlier claim that `HEIZKURVE` is confirmed writable on WPF 10 was
incorrect. It appears under the upstream **Probe signals** heading, addressed
to manager `0x480`; our initial implementation addressed `0x180`. The original
assessment below is historical and must not be used as write authorization.
The Home Assistant slope set action is now disabled pending verification.

This diagnostic build logs every standard CAN data frame with tag
`heating_can` (sender ID, raw bytes, and candidate index/value decoding).
`heating_probe` logs read requests for `0x0008`, `0x0005`, and `0x010e` at
both `0x180` and `0x480`: one request per second, each pair every six seconds.
These probes do not write. Existing domestic hot water automation is unchanged.
The displayed slope still reflects the original `0x180` reading and should
not be treated as verified; use the raw logs for this experiment.

Capture logs before starting, record original panel values, then make the
smallest panel change to Eco, wait 30 seconds, restore it and wait 30 seconds.
Repeat for Comfort, then heating curve. Record each value and change time.
If the three changes are made together, keep their times and values distinct
and wait 30 seconds before restoring. Individual changes are easier to match.
Panel traffic and read responses establish candidates, not external write support.
Remove the temporary high-volume logger and frequent probes after diagnosis.

This document records the evidence threshold used for writable CAN controls.
The CAN protocol is reverse engineered, so a matching Elster index alone is not
sufficient evidence that a particular WPF 10 firmware accepts writes to it.

## Protocol used by this configuration

The WPM at CAN member `0x180` is addressed through the existing seven-byte
Elster framing in `sensor_node.conf`:

* reads use destination bytes `31 00`; writes use `30 00`;
* an index below `0x0100` is byte 2, followed by the 16-bit value in bytes 3–4;
* an extended index is `fa`, index high, index low, followed by the 16-bit
  value in bytes 5–6;
* replies from CAN ID `0x180` are accepted only when their first two bytes
  identify this ESP node (the configured `internalResponse_id`).

The new control retains that framing. It sends a write, waits 750 ms, requests
the same index, and exposes a diagnostic binary/text result after comparing the
raw value returned by the WPM. A missing reply leaves verification false.

## Implemented control

| Home Assistant entity | Elster index | WPF 10 evidence | Encoding and enforced range |
| --- | --- | --- | --- |
| Heating curve slope | `0x010e` (`HEIZKURVE`) | Included in `ha-stiebel-control`'s WPF 10 polling table without its unverified marker. Both reference Elster tables label it `et_cent_val`. | signed 16-bit value / 100; `0.00`–`3.00`, step `0.05` |

The `0.05` write step and range align with the official WPM 3/WPM 3i Modbus
table's heating-curve-rise parameter (register 1504, data type 7). The Modbus
manual confirms the meaning and permissible range, but does **not** claim that
its register address is the Elster CAN index.

## Known mappings intentionally not exposed yet

| Intended function | Candidate Elster index | Encoding | Why not exposed |
| --- | --- | --- | --- |
| HK1 comfort / room setpoint | `0x0005` (`RAUMSOLLTEMP_I`) | signed tenths °C | The WPF 10 request table labels it “unverified”. The official Modbus range is 5–30 °C, but it does not validate the CAN write mapping. |
| HK1 eco / reduced setpoint | `0x0008` (`RAUMSOLLTEMP_NACHT`) | signed tenths °C | Present in both generic Elster tables but not verified as writable for WPF 10. |
| Heating-curve level / offset | `0x0111` (`HZK_KURVENABSTAND`) | signed tenths | Generic-table mapping only; no WPF 10-specific read/write evidence found. |
| Heating operating mode | `0x0112` (`PROGRAMMSCHALTER`) | 16-bit enum; common values `0x0000` emergency, `0x0100` standby, `0x0200` automatic, `0x0300` comfort/day, `0x0400` eco/reduced, `0x0500` DHW | Generic mapping and enum only. No WPF 10-specific write confirmation. This is particularly unsuitable to guess because it can suspend heating. |

The upstream WPF 10 configuration likewise calls `RAUMSOLLTEMP_I` a probe
signal. `can2mqtt` states WPF 10E support and carries the generic index/type
table, which corroborates index names and scaling but not per-firmware write
acceptance. These candidates should first be tested as read-only against the
controller display, then one-at-a-time writes with immediate read-back while
present, before they are allow-listed here.

## Deliberate safety boundary

The former raw `pull_canmsg` API service and `Send CAN command` button have
been removed. Home Assistant can now write only the explicitly allow-listed
heating-curve entity; no UI/service accepts arbitrary CAN address, index, or
value. Existing domestic-hot-water input-text automation remains outside this
new heating-control allow-list and should be migrated separately if desired.

## Sources

* `references/source-snapshots/ha-stiebel-control` at upstream commit
  `e8929d085f5321f1d384f50c97ffe67b01f652ab`: `wpf10.yaml`,
  `signal_requests_wpf10.h`, and its Elster table/writable-entity code.
* `references/source-snapshots/can2mqtt` copied from upstream commit
  `0d6c1f0239aae0e7250e43ddc4173f07c7019906`: WPF 10E support statement and
  `infos/ElsterTable.inc`.
* [Stiebel Eltron Modbus TCP/IP commissioning manual](https://www.stiebel-eltron.com/content/dam/ste/cdbassets/current/commissioning_manual/commissioning_manual_doc-00081716.pdf), WPM 3/WPM 3i block 2, registers 1501–1511.
* [Elster CAN reverse-engineering discussion](https://www.haustechnikdialog.de/Forum/t/98046/Defekte-Stiebel-Eltron-WPF-7-mit-Waermepumpenmanager-WPM-II-wiederbeleben-und-einbauen).

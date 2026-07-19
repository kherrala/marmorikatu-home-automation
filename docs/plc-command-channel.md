# PLC Light-Command Channel

How light commands reach the WAGO PLC, and the provenance breadcrumb the
home-automation stack layers on top. Tested live 2026-07-19 (idx 5, physically
confirmed).

## `/set` accepts ONLY the bare payload

The command topic `marmorikatu/light/<idx>/set` takes the **bare string**
`true` or `false`. It is consumed by an out-of-repo **WBM Native-MQTT →
`PersistentVars.Controls[]` BOOL binding** on the controller (there is no
command-subscribe code in the `marmorikatu-plc` IEC project — see
`../marmorikatu-plc/MQTT.md`). That binding coerces the exact payload bytes to a
`BOOL`; it does **not** parse JSON.

- **An enriched payload like `{"on":true,"src":"optimizer"}` is REJECTED** — the
  light does not actuate. Do **not** enrich `/set`. (This was the tempting
  simplification; it was tried live and failed.)
- **~12–13 s actuation latency.** A published command takes about one PLC
  scan/publish cycle to take effect, and the `marmorikatu/lights` state topic
  broadcasts only every ~13 s. Never judge a command by an immediate retained
  read with a short sleep — it aliases (a fresh read can report the old state
  even while the light is physically on). Read state via InfluxDB `lights`
  `last()`, and keep any command-confirm / min-dwell window well above ~15 s.

## Provenance breadcrumb (side-channel, PLC-ignored)

To let the lights-optimizer tell WHO commanded a light — its own action vs. the
mobile app vs. voice/MCP vs. a physical wall press — every software controller
also publishes a breadcrumb **beside** its unchanged `/set`:

- Topic: `marmorikatu/light/<idx>/command`
- Payload: `{"on":bool,"src":"optimizer|mobile|mcp|voice","ts":<epoch>}`, QoS 1, retain=false.

The **PLC never subscribes** to this topic; it only reads `/set`.
`scripts/plc_mqtt_subscriber.py` records the breadcrumb as the `light_command`
InfluxDB measurement (tags `light_id`, `light_name`, `source`; field `is_on`).

**Wall switches emit no breadcrumb** (they are internal to the PLC). So the
optimizer infers a physical wall press by *elimination*: a `lights/is_on`
transition with no matching `light_command` within the correlation window
(`classify_origin`) is a wall press. Both a wall press and a mobile/voice
command count as a manual action the optimizer must never fight.

Publishers: `scripts/lights_optimizer.py` (`publish_command_breadcrumb`,
`src=optimizer`), `scripts/mcp_tools/lights.py` (`src=mcp`), and the mobile app
(`../marmorikatu-mobile`, `src=mobile`).

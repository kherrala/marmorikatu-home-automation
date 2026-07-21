# Lighting Policy — Specification by Room Group

The comfort-first specification the lights-optimizer implements, organised by
**room group**. For each group: the **family's need**, the **auto-ON approval
criteria** (all must hold to turn a light on), and the **auto-OFF approval
criteria** (all must hold to turn one off), plus guardrails.

Cross-cutting rules apply to **every** group and are not repeated below:

- **Manual-wins on the re-fight.** If the optimizer auto-turned a light ON and a
  human turns it OFF, it is *dismissed* and not re-enabled until the next day.
- **Min-dwell.** The optimizer never reverses its own command within 5 min.
- **Provenance.** Every decision records whether the current state came from a
  human (wall / mobile / voice) or the optimizer (`manual_locked`).
- **High-confidence culls fire regardless of who turned a light on.** Provenance
  does not veto daylight / overnight / away / duration-cap offs — it only
  prevents the auto-on re-fight. What protects a room from being turned off is
  its **category** simply not having that off-rule.
- **CO₂ is an auto-ON signal only** — it never turns a light off (it lags and
  reads low when people sit still). Turning a room off on "empty" requires a
  **real presence signal** (Zigbee mmWave/PIR via the Presence Service), which
  activates per room as sensors are installed. Until then, living spaces are
  simply held during awake hours.
- **Darkness** = astronomical sun elevation < 8°. **Whole-house-away** = no
  wall-switch / light activity for 3 h (activity heuristic). *BLE advertiser
  counting is deliberately NOT used for away by default* (`BLE_AWAY_ENABLED=0`):
  an always-on, MAC-rotating Samsung SmartTag in the basement (the bike) would
  keep the count above zero forever, and carried keychain tags stay quiet near
  their owner's phone — so raw BLE is not a reliable occupancy signal here. Real
  occupancy will come from the Zigbee Presence Service.

Household context: two remote-working adults + two children (8–9), home most of
the day; free time spent in the theater, downstairs living room, and dining
area; sauna most evenings; summer days on the front terrace (bright late into
the evening).

---

## 1. Living core — kitchen · dining · downstairs living room

**Lights:** 8 Keittiö katto, 40 Keittiö kattovalo, 19 Ruokailu, 54 Olohuone
kattovalo, 55 Olohuone kattovalo 2. *(Open-plan; the kitchen Ruuvi CO₂ sensor +
the living-room FP300 cover the area.)*

> **5 Olohuone LED** is a full room light too, but the household wants **only the
> kattovalo to auto-on**. It's category `secondary`: never auto-on (switched on
> deliberately), but still vacancy/overnight/away-off like the rest of the room.

- **Need:** where the family lives — must be lit when someone is here in the
  dark, and **must never go dark on an occupied room**.
- **Auto-ON:** it's dark **AND** the space reads occupied (kitchen CO₂ elevated,
  or real presence) **AND** not dismissed today.
- **Auto-OFF:** only **whole-house-away**, or **overnight** if the light was
  left on and forgotten (on since before 00:30, room not occupied). **No daytime
  off, no occupancy-off during awake hours.**
- **Guardrail:** a person sitting still with low CO₂ never triggers an off.

## 2. Window & decorative window lights

**Lights:** 18, 20, 23, 24, 30, 32, 41, 46 (`ikkuna` / `ikkunavalo`).

- **Need:** pretty in the dark; **pointless when the sun is up**.
- **Auto-ON:** none (manual — switched on deliberately in the evening).
- **Auto-OFF:** the sun is clearly up (past sunrise + 60 min, before sunset),
  **OR** overnight, **OR** whole-house-away. No occupancy-off.
- **Guardrail:** short manual grace — a window light is only ever on in the
  dark, so a prompt daylight-off is safe.

## 3. Accent LED strips

**Lights:** 2 Keittiö kaapisto ylä (mood, above cupboards), 7 Keittiö kaapisto
ala (task, under-cabinet). *These are the only true LED strips — the other LEDs
(5 Olohuone, 6 KHH, 3 YK aula) are full room lights, categorised with their
rooms below.*

- **Need:** mood lighting — a deliberate human choice.
- **Auto-ON:** none.
- **Auto-OFF:** overnight **OR** whole-house-away. Never occupancy-off; held
  through awake hours.

## 4. Circulation — halls · entry · staircases

**Lights:** 25 Aula rappuset, 26 YK aula katto, 3 YK aula LED, 35 Eteinen,
37 Tuulikaappi, 42 Portaikko.

- **Need:** on briefly for passage; frequently forgotten.
- **Auto-ON:** *deferred* — no reliable arrival signal until PIR is installed
  (then: motion + dark → on).
- **Auto-OFF:** duration cap (~25 min on) **OR** overnight **OR**
  whole-house-away. With PIR: short vacancy timeout.
- **Guardrail:** the manual grace covers "left it on for the evening" before the
  cap applies.

## 5. Utility & closets

**Lights:** 6 KHH LED (full room light), 31 Aikuiset vaatehuone, 36 Tuulikaappi
vaatehuone, 43 KHH vaatehuone, 53 Kellari varasto, 56 KHH katto 2, 61 Varasto,
39 Tekninen tila.

- **Need:** windowless, the #1 forgotten lights.
- **Auto-ON:** none.
- **Auto-OFF:** duration cap (~30 min) **OR** overnight **OR** whole-house-away.

## 6. Toilets & bathrooms

**Lights:** 29 KPH yläkerta katto, 34 KPH yläkerta peili, 44 WC alakerta katto,
45 WC alakerta peili, 52 WC kellari.

- **Need:** on for the visit; forgotten-prone; **night trips are normal**.
- **Auto-ON:** none (with PIR later: motion → on).
- **Auto-OFF:** duration cap (30 min) only. **No overnight-kill mid-use** — a
  night bathroom visit is never cut off.
- **Guardrail:** a fresh press resets the timer; a still shower (with PIR) uses a
  longer 15-min vacancy timeout.

## 7. Bedrooms (upstairs, sleeping)

**Lights:** 22 Seela katto, 28 Aarni katto, 33 Aikuiset katto. *(Bedroom window
lights 23/30/32 are in group 2; the adults' walk-in closet 31 is in group 5.)*

- **Need:** lit when in use, including **daytime naps** (so no daylight-off);
  shouldn't burn all night if forgotten.
- **Auto-ON:** none (deferred until per-room presence).
- **Auto-OFF:** overnight **OR** whole-house-away. **No daylight-off** (nap-safe).
- **Guardrail:** longer manual grace; a light switched on at night (kid awake) is
  protected.

## 8. Office — downstairs bedroom / workspace

**Lights:** 17 MH alakerta kattovalo. *(Window light 18 is in group 2.)*

- **Need:** a parent works here on video calls; the kitchen CO₂ sensor doesn't
  see this room — it **must never be turned off during work**.
- **Auto-ON:** none today (with presence later: dark work-morning → on).
- **Auto-OFF:** **only whole-house-away.** No daytime, occupancy, or overnight
  off. This is the light v1 kept wrongly killing.

## 9. Theater & billiard — basement

**Lights:** 49 Kellari etuosa, 50 Kellari takaosa, 51 Biljardipöytä.

- **Need:** the family watches movies / plays here for hours. Windowless, no
  presence signal yet — **must never auto-off during use**.
- **Auto-ON:** none.
- **Auto-OFF:** **only whole-house-away.** (With per-room presence later: a safe
  "room empty for 30 min" off.)
- **Guardrail:** effectively manual; theater lights are sacred.

## 10. Sauna complex — temperature-driven

**Lights:** 4 Saunan laude ledi, 1 Kylpyhuone alakerta, 38 Sauna siivousvalo.

- **Need:** used most evenings; driven by the sauna's own heat, not a clock.
- **Auto-ON:** the laude LED (4) comes on automatically at ≥ 55 °C sauna temp
  (evening löyly), off ≤ 50 °C (hysteresis).
- **Auto-OFF:** **post-session** — once the sauna peaked > 55 °C and has been
  < 40 °C for ≥ 30 min, the bathroom (1) + cleaning light (38) turn off.
- **Guardrail:** a wall-clock timeout never cuts a shower/bath short — a recent
  manual press is respected; the temperature drop is the "session over" signal.

## 11. Outdoor — porch · terrace · carport · storage

**Lights:** 47 Sisäänkäynti (front porch), 48 Ulkovalo terassi, 59 Autokatos,
60 Varasto ulkovalo.

- **Need:** on when dark and wanted outside; **off whenever the sun is up**. In
  the bright Finnish summer they're essentially never needed — evenings on the
  terrace stay light, and the darkness gate keeps them off automatically.
- **Auto-ON:** none at dusk (removed by request). The optimizer is the porch's
  sole controller: a Unifi front-door person-detection (webhook `light_request`
  signal → `light_override` hold) makes the optimizer light it for the detection
  window and turn it off after — never overriding a manual porch-on.
- **Auto-OFF:** the sun is up, **OR** after the porch off-hour / overnight.
  **No occupancy-off** — someone sitting on the terrace reads as "away" indoors
  and must never be plunged into darkness.
- **Guardrail:** terrace (48) and carport (59) are manual-on in the evening with
  daylight/overnight auto-off only.

---

## Approval-criteria summary

| Room group | Auto-ON when… | Auto-OFF when… |
|---|---|---|
| Living core | dark + occupied (CO₂/presence) | away · overnight-if-forgotten |
| Window | — | daylight · overnight · away |
| Accent LED | — | overnight · away |
| Circulation | (PIR: motion + dark) | duration cap · overnight · away |
| Utility/closet | — | duration cap · overnight · away |
| Toilet/bath | (PIR: motion) | duration cap (no overnight-kill) |
| Bedroom | — | overnight · away (no daylight-off) |
| Office | (presence: dark AM) | away only |
| Theater | — | away only |
| Sauna | laude ≥ 55 °C | post-session cooldown |
| Outdoor | porch: dark + evening | daylight · overnight (no occupancy-off) |

See [lights-optimizer.md](lights-optimizer.md) for the implementation, tunable
env vars, and the provenance/presence mechanics.

# Lighting policy by room

Sensor-controlled rooms share one policy: occupied + dim turns automatic lights
ON; confirmed vacancy turns lights OFF. Olohuone/Ruokailu may also switch OFF in
bright daylight while occupied, unless the user turned the light ON manually.
A manual OFF prevents automatic relighting until that room/zone becomes vacant.
A manual ON in a sensor-controlled room guarantees at least 10 minutes of light,
even with no detected movement. After that minimum, normal vacancy control
resumes; continued occupancy keeps the light ON. This also covers turning a light
back ON immediately after an incorrect automatic OFF.

No CO₂, BLE, activity-based away detection, overnight rule, or visit duration cap
competes with presence in a sensor-controlled room. Missing presence does not
count as vacancy and does not enable a fallback timer.

## Sensor-controlled rooms

| Room / lights | Automatic ON | Automatic OFF |
|---|---|---|
| Olohuone ceiling 54, Ruokailu 19 | Shared zone occupied + dim | Shared zone vacant, or calibrated daylight brightness for optimizer-lit lights |
| Kitchen ceilings 8/40 | Shared zone occupied + dim | Shared zone vacant |
| Olohuone LED 5 | Manual only | Shared zone vacant |
| Upstairs aula ceiling 26 and stairs 25 | Hall-up PIR occupied + dim | Hall-up vacant |
| Eteinen 35, Tuulikaappi 37 | Hall-down PIR occupied + dim | Hall-down vacant |
| KHH LED 6 | KHH PIR occupied + dim | KHH vacant |
| KHH ceiling 56 | Manual only | KHH vacant |
| Downstairs WC 44/45 | Own room occupied, at any brightness | Own room vacant |
| Upstairs bathroom 29/34 | Bathroom occupied + dim | Bathroom vacant |
| Bedrooms 22/28/33 | Own room occupied + dim, once sensors are installed | Own room vacant; no daylight shutoff |
| Office 17 | Occupied + dim, once its sensor is installed | Confirmed vacancy; no daylight shutoff |

A room mapped for a future sensor stays unknown until it has reliable data:
no automatic ON or vacancy OFF. Installing a sensor activates its mapped policy.

### Olohuone, Ruokailu and kitchen

The FP300 in Olohuone also covers Ruokailu; the kitchen has its own PIR. These two
sensors form one occupancy zone. Either occupied holds the zone; both must be
confidently vacant to end a visit. The downstairs hall PIR is excluded. This same
zone applies to Olohuone LED 5 even though it is manual-on.

The FP300's radar presence and PIR motion are combined. Both inputs must remain
clear for five minutes before the living room reports vacant. Kitchen PIR grace
is three minutes after its explicit false. The Presence Engine owns those timers.

Brightness uses the physical room's four-minute lux mean:

- Olohuone/Ruokailu: ON below 80 lux or during astronomical darkness; optimizer-lit
  ceiling/dining lights may turn OFF above 200 lux in daylight even while occupied.
- Kitchen: ON below 40 lux or during astronomical darkness. No measured daylight
  OFF threshold is enabled for the kitchen yet.

The 80/200 gap prevents an immediate ON after daylight OFF. A manual ON protects
against measured daylight OFF. Confirmed zone vacancy can switch it OFF only
after the 10-minute manual-ON minimum.
Olohuone LED 5 stays manual-on and has no brightness shutoff. Output 55 is
physically disconnected and excluded.

### Halls, KHH and bathrooms

Hall PIRs wait 90 seconds after explicit false before vacancy; KHH waits 180
seconds; downstairs WC and upstairs bathroom wait 900 seconds (15 minutes),
allowing someone to sit still. Re-detection cancels vacancy.
The device's own detection duration is additional. There is no maximum visit
length and no overnight shutoff while occupied.

KHH uses 40 lux for automatic LED ON; the old 12-lux limit blocked real morning
arrivals at 17–18 lux. Ceiling 56 and the separate wardrobe 43 remain manual-on.
Varasto 61 is the detached carport storage, not KHH, and has no KHH sensor link.

Windowless WC outputs 44/45 bypass the darkness gate. Upstairs bathroom lights
use the normal brightness gate. Bedroom and office lights do not get daylight
shutoff; they rely on their own occupancy when a sensor is installed.

## Basement: separate workspace, scheduled forgotten-light cutoffs

Kellari etuosa 49, Kellari takaosa 50 and Kellari varasto 53 are **manual ON,
20:00 forgotten-light OFF only**. Biljardipöytä 51 and WC kellari 52 keep the
later **00:30** cutoff. There are no basement
sensors. The user works here through the day and late evening: upstairs motion,
vacancy, daylight and duration caps must never control these lights. Even a stray
presence reading must not affect them. The basement WC is not sensor-controlled.

At each group's cutoff, switch OFF lights left ON before it. The OFF window
lasts until 06:00, but any light manually switched ON after its own cutoff is
protected for the rest of that night. The 20:00 group keeps that protection
across midnight. The next night can switch it OFF if still forgotten. This
clears lights the kids leave ON without repeatedly interrupting someone who
switches a light back ON to keep working.

## Other lights without room sensors

These lights are manual-on and keep only the explicit policies below. Their
rules never serve as fallbacks for a missing room-sensor reading.

| Lights | Automatic OFF |
|---|---|
| Window lights 18/20/23/24/30/32/41/46 | Daylight or forgotten overnight |
| Kitchen cabinet strips 2/7 | Forgotten overnight |
| Upstairs aula LED 3 | Forgotten overnight |
| Portaikko 42 | 25-minute duration cap or forgotten overnight |
| Closets/stores 31/36/43/61 | 30-minute duration cap or forgotten overnight |
| Terrace 48, carport 59, storage exterior 60 | Daylight or forgotten overnight |

Daylight OFF runs from sunrise + 60 minutes to sunset. Forgotten overnight OFF
runs 00:30–06:00 only for lights switched ON before 00:30. Switching a light ON
during this window protects it from the overnight rule; a separately configured
duration cap still applies. Basement lights use only the overnight rule described
above, with no daylight shutoff or duration cap.

## Porch and sauna

- **Porch 47:** Unifi person detection requests a timed hold through the webhook,
  which checks the request's darkness condition. The optimizer lights the porch
  and switches its own light OFF after the hold ends. A manual ON is protected at
  night; a manual OFF during detection is respected. Daylight OFF still applies
  when no detection hold is active. No dusk or fixed off-hour schedule.
- **Sauna laude LED 4:** ON at ≥55°C, OFF at ≤50°C, hold in between.
- **Post-sauna bathroom / cleaning / technical-room lights 1/38/39:** OFF after a
  sauna peak ≥55°C and at least 30 minutes continuously below 40°C. A recent
  switch-on gets 90 minutes of grace.

Normal room commands have a 30-second PLC round-trip guard. Sensor-room lights
also stay ON for at least 90 seconds so a new wall press can precede its presence
report without being immediately reversed. Manual OFF dismissals are in memory
and reset on restart.

See [lights-optimizer.md](lights-optimizer.md) for implementation, provenance,
configuration, and diagnostic reason codes, and [presence-setup.md](presence-setup.md)
for sensor setup and vacancy timing.

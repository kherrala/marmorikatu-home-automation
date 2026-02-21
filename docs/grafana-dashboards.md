# Grafana Dashboards

Eight provisioned dashboards for building automation data visualization.

## Dashboard Inventory

| File | UID | Title | Content |
|------|-----|-------|---------|
| `building_overview.json` | `wago-overview` | Temperature Overview | Home dashboard with canvas floorplan, all temperature sources |
| `hvac_dashboard.json` | `wago-hvac` | HVAC | Ventilation temps, heat recovery efficiency, freezing risk, power |
| `hvac_temp_histogram.json` | `hvac-temp-histogram` | HVAC lämpötilojen jakauma | HVAC temperature distribution histograms |
| `room_temp_histogram.json` | `room-temp-histogram` | Huonelämpötilojen jakauma | Room temperature distribution histograms |
| `lights_status.json` | `wago-lights` | Light Switch Status | Light switch on/off status by floor |
| `ruuvi_sensors.json` | `ruuvi-sensors` | Ruuvi Sensors | Ruuvi sensor data, air quality |
| `thermia_heatpump.json` | `thermia-heatpump` | Maalämpöpumppu | Heat pump temps, COP, power, runtimes |
| `energy_cost.json` | `energy-cost` | Energiakustannukset | Estimated consumption & costs by consumer |

All files in `grafana/provisioning/dashboards/`.

## Provisioning

Dashboards are provisioned from JSON files via `grafana/provisioning/dashboards/dashboards.yml`.
They cannot be saved from the Grafana UI. To modify:

1. Edit the JSON file directly
2. Run `docker compose restart grafana`
3. Verify in the browser

## Conventions

### Tags

All dashboards are tagged `building-automation` plus a topic-specific tag:

| Tag | Dashboards |
|-----|------------|
| `wago` | Temperature Overview |
| `hvac` | HVAC, HVAC lämpötilojen jakauma |
| `ruuvi` | Ruuvi Sensors |
| `thermia` | Maalämpöpumppu |
| `lights` | Light Switch Status |
| `energy` | Energiakustannukset |

### Cross-Dashboard Navigation

Each dashboard has navigation links to related dashboards using URL format:

```
/d/<uid>/<slug>?includeVars=true&keepTime=true
```

The `includeVars` and `keepTime` parameters preserve template variable selections
and the time range when navigating between dashboards.

### Flux Query Patterns

Queries use Grafana's built-in variables for time range integration:

```flux
from(bucket: "building_automation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "hvac")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
```

- `v.timeRangeStart` / `v.timeRangeStop` — Selected time range
- `v.windowPeriod` — Auto-calculated aggregation window

### Field Name Handling

InfluxDB field names use ASCII characters (e.g., `Ulkolampotila`). Finnish
characters are restored in Grafana using display name overrides:

| Field Name | Display Override |
|------------|-----------------|
| `Ulkolampotila` | Ulkolämpötila |
| `Tuloilma_ennen_lammitysta` | Tuloilma ennen lämmitystä |
| `Tuloilma_jalkeen_lammityksen` | Tuloilma jälkeen lämmityksen |
| `Jateilma` | Jäteilma |
| `Suhteellinen_kosteus` | Suhteellinen kosteus |

### Home Dashboard

The Temperature Overview (`wago-overview`) is configured as the Grafana home
page via the `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` environment variable.

## Grafana Configuration

### Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `GF_SECURITY_ADMIN_USER` | `admin` | Admin username |
| `GF_SECURITY_ADMIN_PASSWORD` | `admin` | Admin password |
| `GF_USERS_ALLOW_SIGN_UP` | `false` | Disable public registration |
| `GF_DATE_FORMATS_DEFAULT_TIMEZONE` | `Europe/Helsinki` | Finnish timezone |
| `GF_DATE_FORMATS_FULL_DATE` | `DD/MM/YYYY HH:mm:ss` | Finnish date format |
| `GF_DATE_FORMATS_INTERVAL_*` | Various | Interval-specific formats |
| `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` | `/etc/grafana/provisioning/dashboards/building_overview.json` | Home dashboard |

### Volumes

| Mount | Purpose |
|-------|---------|
| `grafana-data:/var/lib/grafana` | Persistent storage (users, preferences) |
| `./grafana/provisioning:/etc/grafana/provisioning` | Dashboard and datasource provisioning |
| `./floorplan:/usr/share/grafana/public/build/img/floorplan:ro` | Floorplan images for Canvas panel |

### InfluxDB Datasource

The datasource is provisioned via `grafana/provisioning/datasources/influxdb.yml`,
configured with:
- UID: `influxdb`
- Query language: Flux
- Organization: `wago`
- Bucket: `building_automation`

### Canvas Panel Notes

The Temperature Overview dashboard uses a Grafana Canvas panel with a building
floorplan background image:

- Background image paths: Canvas prepends `/public/` automatically — use
  `img/floorplan/...` not `/public/img/floorplan/...`
- Canvas metric-value elements need wide-format data (pivot). Use
  `|> group() |> pivot()` to merge into one table.
- Element positions are absolute pixels relative to panel viewport. The image
  with `contain` fit is centered — positions must account for horizontal offset.

## Dashboard Details

### Temperature Overview (`wago-overview`)

Home dashboard with a canvas floorplan showing real-time temperatures from
multiple sources (WAGO rooms, Ruuvi sensors). Provides at-a-glance overview
of indoor climate across all floors.

### HVAC (`wago-hvac`)

Ventilation system monitoring with panels for:
- Outdoor and exhaust air temperatures
- Supply air temperatures and setpoints
- Heat recovery efficiency (sensible + enthalpy) — see [heat-recovery-efficiency.md](heat-recovery-efficiency.md)
- Recovered heat power, heating coil power, waste heat
- Freezing probability gauge
- Power and energy consumption
- Relative humidity and dew point

### Ruuvi Sensors (`ruuvi-sensors`)

All Ruuvi sensor data including:
- Temperature and humidity from all 7 sensors
- Atmospheric pressure
- Air quality (CO2, PM, VOC, NOx) from Keittiö sensor
- Battery voltage monitoring
- Signal strength (RSSI)

### Maalämpöpumppu (`thermia-heatpump`)

Heat pump monitoring with panels for:
- Current temperatures (stat panels)
- Heating circuit and brine circuit temperatures (timeseries)
- Hot water temperature with start/stop setpoints
- Indoor temperature with target
- Component on/off status (state timeline)
- Temperature differentials and estimated thermal power — see [heatpump-efficiency.md](heatpump-efficiency.md)
- COP (coefficient of performance)
- Runtime counters (compressor, heaters, cooling)
- Alarm states

### Light Switch Status (`wago-lights`)

Light switch on/off status organized by floor, with state timeline showing
when lights were turned on and off.

### Energiakustannukset (`energy-cost`)

Estimated electricity consumption and cost analysis. Since there is no working
power meter, consumption is estimated from component status data:

- **Heat pump**: Compressor power interpolated from supply temperature
  (1.77–2.27 kW) plus auxiliary heaters (3 kW + 6 kW exact)
- **Lighting**: Count of active switches × assumed wattage (configurable, default 10W)
- **Sauna**: 6 kW when sauna temperature is rising (positive derivative)
- **HVAC fans**: Fixed assumed wattage when running (configurable, default 300W)

Cost = consumption × (spot price + 0.49 margin + 6.09 transfer) c/kWh.

Dashboard variables:
- `interval`: Aggregation period (1h / 1d / 1w / 1mo)
- `watt_per_light`: Assumed wattage per light switch (5–20W)
- `watt_fan`: Assumed HVAC fan wattage (200–500W)

Panels: summary stats (total kWh, total €, avg price, pie chart), stacked bar
charts (consumption and cost by consumer per interval), electricity price
timeseries, and per-consumer breakdown table.

### Histogram Dashboards

Two histogram dashboards showing temperature distribution over the selected
time range:
- **HVAC lämpötilojen jakauma**: Distribution of ventilation system temperatures
- **Huonelämpötilojen jakauma**: Distribution of room temperatures across all rooms

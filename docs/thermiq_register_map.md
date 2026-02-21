# ThermIQ-ROOM2 Register Map

Complete register mapping for ThermIQ-ROOM2 firmware 2.68, based on the
[thermiq_mqtt-ha](https://github.com/ThermIQ/thermiq_mqtt-ha) Home Assistant integration
and verified against live MQTT data from a Thermia ground-source heat pump.

MQTT payload uses decimal register keys (`d0`..`d127`). The ThermIQ upstream code uses
hexadecimal notation (`rXX`); conversion: `r0a` = `d10`, `r10` = `d16`, `r32` = `d50`.

## Sample Reading

```
Time: 2026-02-17 21:38:09 GMT  RSSI: -74 dBm  FW: ThermIQ-room2 2.68
Outdoor: -15°C  Indoor: 20.5°C (INDR_T)  Mode: Heating
```

## Temperatures (d0-d15, d23-d24)

| Reg | Hex | Value | Unit | Name | Description |
|-----|-----|-------|------|------|-------------|
| d0 | r00 | -15 | °C | outdoor_t | Outdoor temp |
| d1 | r01 | 20 | °C | indoor_t | Indoor temp (integer part) |
| d2 | r02 | 0 | ×0.1°C | indoor_dec_t | Indoor temp (decimal part) |
| d3 | r03 | 21 | °C | indoor_target_t | Indoor target (integer part) |
| d4 | r04 | 0 | ×0.1°C | indoor_target_dec_t | Indoor target (decimal part) |
| d5 | r05 | 59 | °C | supplyline_t | Supply line temp |
| d6 | r06 | 55 | °C | returnline_t | Return line temp |
| d7 | r07 | 53 | °C | boiler_t | Hot water temp |
| d8 | r08 | -4 | °C | brine_out_t | Brine out temp |
| d9 | r09 | -1 | °C | brine_in_t | Brine in temp |
| d10 | r0a | -40 | °C | cooling_t | Cooling temp |
| d11 | r0b | 0 | °C | supply_shunt_t | Supply line temp, shunt |
| d14 | r0e | 32 | °C | supplyline_target_t | Supply line target temp |
| d15 | r0f | 51 | °C | supplyline_shunt_target_t | Supply line target temp, shunt |
| d23 | r17 | 111 | °C | pressurepipe_t | Pressurepipe (compressor discharge) temp |
| d24 | r18 | 0 | °C | hgw_water_t | Hot water supply line temp (not installed) |

The firmware also provides `INDR_T` (20.5°C) as a pre-calculated indoor temperature
with higher precision than d1+d2×0.1.

## Bitfield: Component Status (d16)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 1 | brine_pump_on | Brine pump |
| 1 | 1 | compressor_on | Compressor |
| 2 | 1 | supply_pump_on | Flow line pump |
| 3 | 1 | hotwaterproduction_on | Hot water production |
| 4 | 0 | aux2_heating_on | Auxiliary 2 |
| 5 | 0 | shunt1_n | Shunt minus |
| 6 | 0 | shunt1_p | Shunt plus |
| 7 | 0 | aux1_heating_on | Auxiliary 1 |

## Bitfield: Aux Heaters (d13)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 0 | boiler_3kw_on | 3 kW auxiliary heater |
| 1 | 0 | boiler_6kw_on | 6 kW auxiliary heater |

## Bitfield: Status 2 (d17)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 0 | shunt2_n | Shunt 2 minus |
| 1 | 0 | shunt2_p | Shunt 2 plus |
| 2 | 0 | shunt_cooling_n | Cooling shunt minus |
| 3 | 0 | shunt_cooling_p | Cooling shunt plus |
| 4 | 0 | active_cooling_on | Active cooling |
| 5 | 0 | passive_cooling_on | Passive cooling |
| 6 | 0 | alarm_indication_on | Alarm indication |

## Bitfield: Alarms 1 — Pressure/Flow (d19)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 0 | highpressure_alm | High pressure pressostat |
| 1 | 0 | lowpressure_alm | Low pressure pressostat |
| 2 | 0 | motorbreaker_alm | Motor circuit breaker |
| 3 | 0 | brine_flow_alm | Low brine flow |
| 4 | 0 | brine_temperature_alm | Low brine temperature |

## Bitfield: Alarms 2 — Sensors (d20)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 0 | outdoor_sensor_alm | Outdoor temp sensor |
| 1 | 0 | supplyline_sensor_alm | Supply line temp sensor |
| 2 | 0 | returnline_sensor_alm | Return line temp sensor |
| 3 | 0 | boiler_sensor_alm | Hot water temp sensor |
| 4 | 1 | indoor_sensor_alm | Indoor temp sensor (always on — no wired sensor) |
| 5 | 0 | phase_order_alm | Incorrect 3-phase order |
| 6 | 0 | overheating_alm | Overheating |

Note: `indoor_sensor_alm` is permanently active because this installation uses the
ThermIQ-ROOM2 wireless sensor instead of a wired indoor sensor. Filtered out in dashboard.

## Bitfield: Installed Options (d98)

| Bit | Value | Name | Description |
|-----|-------|------|-------------|
| 0 | 1 | opt_phasemeassure_installed | Phase measurement |
| 1 | 0 | opt_2_installed | Option 2 |
| 2 | 0 | opt_hgw_installed | HGW (hot gas water heater) |
| 3 | 0 | opt_4_installed | Option 4 |
| 4 | 0 | opt_5_installed | Option 5 |
| 5 | 0 | opt_6_installed | Option 6 |
| 6 | 0 | opt_optimum_installed | Optimum control |
| 7 | 0 | opt_flowguard_installed | Flow guard |

## Performance Registers

| Reg | Hex | Value | Unit | Name | Description |
|-----|-----|-------|------|------|-------------|
| d12 | r0c | 0 | A | current_consumed_a | Electrical current (always 0 on this unit) |
| d18 | r12 | 0 | % | pwm_out_period | PWM output |
| d21 | r15 | 64 | — | demand1 | DEMAND1 signal |
| d22 | r16 | 136 | — | demand2 | DEMAND2 signal (128 = neutral) |
| d25 | r19 | -454 | C×min | integral1 | Integral A1 (cumulative temp deficit) |
| d26 | r1a | 2 | — | integral1_a_step | Integral A-limit step |
| d27 | r1b | 0 | ×10s | defrost_time | Defrost duration |
| d28 | r1c | 0 | min | time_to_start_min | Minimum time to start |
| d30 | r1e | 0 | % | supply_pump_speed | Flow line pump speed (fixed-speed: always 0) |
| d31 | r1f | 0 | % | brine_pump_speed | Brine pump speed (fixed-speed: always 0) |

### Integral explanation

The integral (d25) represents how far behind heat delivery is from demand, as a
cumulative temperature×time deficit (°C×min). When the integral exceeds limit A1 (d73),
auxiliary heating step 1 activates. When it exceeds A2 (d79×10), step 2 activates.

In this sample: integral = -454 C×min, A1 limit = 100, A2 limit = 990 (99×10).

## Settings (d50-d103)

| Reg | Hex | Value | Unit | Name | Description |
|-----|-----|-------|------|------|-------------|
| d50 | r32 | 21 | °C | indoor_requested_t | Indoor target setpoint |
| d51 | r33 | 1 | — | main_mode | Mode (0=Off, 1=Heating, 2=Cooling, 3=Auto) |
| d52 | r34 | 26 | — | integral1_curve_slope | Heating curve slope |
| d53 | r35 | 28 | °C | integral1_curve_min | Heating curve minimum |
| d54 | r36 | 47 | °C | integral1_curve_max | Heating curve maximum |
| d55 | r37 | 0 | °C | integral1_curve_p5 | Curve adjustment at +5°C outdoor |
| d56 | r38 | 0 | °C | integral1_curve_0 | Curve adjustment at 0°C outdoor |
| d57 | r39 | 0 | °C | integral1_curve_n5 | Curve adjustment at -5°C outdoor |
| d58 | r3a | 18 | °C | heating_stop_t | Stop heating above this outdoor temp |
| d59 | r3b | 1 | °C | reduction_t | Temperature reduction |
| d60 | r3c | 2 | — | room_factor | Room factor |
| d61 | r3d | 40 | — | integral2_curve_slope | Curve 2 slope |
| d62 | r3e | 10 | °C | integral2_curve_min | Curve 2 minimum |
| d63 | r3f | 55 | °C | integral2_curve_max | Curve 2 maximum |
| d64 | r40 | 20 | °C | integral2_curve_target | Curve 2 target |
| d65 | r41 | 2 | °C | integral2_curve_actual | Curve 2 actual |
| d66 | r42 | 20 | °C | outdoor_stop_t | Outdoor stop temp |
| d67 | r43 | 140 | °C | pressure_pipe_limit_t | Pressurepipe temp limit |
| d68 | r44 | 43 | °C | hotwater_start_t | Hot water start temp |
| d69 | r45 | 30 | min | hotwater_runtime | Hot water operating time |
| d70 | r46 | 10 | min | heatpump_runtime | Heat pump operating time |
| d71 | r47 | 31 | days | legionella_interval | Legionella interval |
| d72 | r48 | 60 | °C | legionella_stop_t | Legionella stop temp |
| d73 | r49 | 100 | C×min | integral_limit_a1 | Integral limit A1 |
| d74 | r4a | 6 | °C | integral_hysteresis_a1 | Hysteresis A1 |
| d75 | r4b | 43 | °C | returnline_max_t | Return line max temp limit |
| d76 | r4c | 10 | min | start_interval_min | Minimum start interval |
| d77 | r4d | -15 | °C | brine_min_t | Brine temp minimum limit |
| d78 | r4e | 18 | °C | cooling_target_t | Cooling target temp |
| d79 | r4f | 99 | ×10 C×min | integral_limit_a2 | Integral limit A2 (effective: 990) |
| d80 | r50 | 20 | °C | integral_hysteresis_a2 | Hysteresis A2 |
| d81 | r51 | 2 | steps | elect_boiler_steps_max | Max electric boiler steps |
| d82 | r52 | 20 | A | current_consumption_max_a | Max current limit |
| d83 | r53 | 60 | s | shunt_time | Shunt operating time |
| d84 | r54 | 55 | °C | hotwater_stop_t | Hot water stop temp |
| d87 | r57 | 9 | — | language | Display language |
| d91 | r5b | 0 | °C | calibration_outdoor | Calibration outdoor sensor |
| d92 | r5c | 0 | °C | calibration_supply | Calibration supplyline sensor |
| d93 | r5d | 1 | °C | calibration_return | Calibration returnline sensor |
| d94 | r5e | 0 | °C | calibration_hotwater | Calibration hotwater sensor |
| d95 | r5f | 0 | °C | calibration_brine_out | Calibration brine out sensor |
| d96 | r60 | 1 | °C | calibration_brine_in | Calibration brine in sensor |
| d97 | r61 | 0 | — | heating_system_type | Heating system type |
| d99 | r63 | 60 | min | internal_logging_t | Internal logging interval |
| d100 | r64 | 3 | ×10s | brine_runout_t | Brine pump run-out duration |
| d101 | r65 | 3 | ×10s | brine_run_in_t | Brine pump run-in duration |
| d102 | r66 | 0 | — | legionella_run_on | Legionella peak heating enable |
| d103 | r67 | 1 | h | legionella_run_length | Legionella peak heating duration |

## Runtime Counters (d104-d115)

| Reg | Hex | Value | Unit | Name | Description |
|-----|-----|-------|------|------|-------------|
| d104 | r68 | 18517 | h | compressor_runtime_h | Compressor runtime |
| d105 | r69 | 56904 | — | msd1_dvp | DVP_MSD1 (internal counter) |
| d106 | r6a | 734 | h | boiler_3kw_runtime_h | 3 kW heater runtime |
| d107 | r6b | 18946 | — | msd1_dts | DTS_MSD1 (internal counter) |
| d108 | r6c | 1866 | h | hotwater_runtime_h | Hot water production runtime |
| d109 | r6d | 7 | — | msd1_dvv | DVV_MSD1 (internal counter) |
| d110 | r6e | 0 | h | passive_cooling_runtime_h | Passive cooling runtime |
| d111 | r6f | 0 | — | msd1_dpas | DPAS_MSD1 (internal counter) |
| d112 | r70 | 0 | h | active_cooling_runtime_h | Active cooling runtime |
| d113 | r71 | 27904 | — | msd1_dact | DACT_MSD1 (internal counter) |
| d114 | r72 | 109 | h | boiler_6kw_runtime_h | 6 kW heater runtime |
| d115 | r73 | 0 | — | msd1_dts2 | DTS2_MSD1 (internal counter) |

## Other Registers

| Reg | Hex | Value | Name | Description |
|-----|-----|-------|------|-------------|
| d29 | r1d | 12 | sw_version | Firmware version |
| d32 | r20 | 0 | status3 | STATUS3 |
| d85 | r55 | 0 | manual_test_mode | Manual test mode |
| d86 | r56 | 0 | status7_larmoff | DT_LARMOFF |
| d88 | r58 | 0 | status8_servfas | SERVFAS |
| d116 | r74 | 0 | graph_display_offset | GrafCounterOffSet |
| d125 | — | 2 | (unmapped) | Possibly hardware/firmware version |
| d126 | — | 2 | (unmapped) | Possibly hardware/firmware version |
| d127 | — | 2 | (unmapped) | Possibly hardware/firmware version |

Registers d33-d49 and d117-d124 are reserved/unused (always 0).

## Installation-Specific Notes

- **Pump speeds (d30, d31)**: Always 0 — this unit has fixed-speed (on/off) pumps
- **Electrical current (d12)**: Always 0 — current transformer not reporting
- **HGW temp (d24)**: Always 0 — hot gas water heater option not installed (d98 bit2=0)
- **Indoor sensor alarm (d20 bit4)**: Permanently active — uses wireless ThermIQ-ROOM2 sensor instead of wired sensor; filtered from dashboard
- **INDR_T field**: More precise indoor temperature (20.5°C) than register calculation (d1+d2×0.1 = 20.0°C); provided directly by ThermIQ firmware

## Non-Register MQTT Fields

| Field | Value | Description |
|-------|-------|-------------|
| Client_Name | ThermIQ_4022D8F0EC20 | Device MAC-based identifier |
| app_info | ThermIQ-room2 2.68 | Firmware version string |
| reason | T | Message trigger (T=timer, C=change) |
| rssi | -74 | WiFi signal strength (dBm) |
| INDR_T | 20.5 | Pre-calculated indoor temp (°C) |
| EVU | 0 | EVU block (utility power restriction) |
| MISMATCH | 0 | Register mismatch counter |
| time | 2026-02-17 21:38:09 GMT | Timestamp string |
| timestamp | 1771364289 | Unix timestamp |

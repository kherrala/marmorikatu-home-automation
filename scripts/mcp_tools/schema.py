"""Data schema documentation for the building automation system."""

SCHEMA = {
    "measurements": {
        "hvac": {
            "description": "HVAC system data from WAGO controller (logged every 2 hours)",
            "fields": {
                "Ulkolampotila": {"unit": "°C", "description": "Outdoor temperature"},
                "Tuloilma_ennen_lammitysta": {"unit": "°C", "description": "Supply air after heat recovery, before heating coil"},
                "Tuloilma_jalkeen_lammityksen": {"unit": "°C", "description": "Supply air after heating coil"},
                "Tuloilma_jalkeen_jaahdytyksen": {"unit": "°C", "description": "Supply air after cooling (summer)"},
                "Tuloilma_asetusarvo": {"unit": "°C", "description": "Supply air setpoint (target temperature)"},
                "Jateilma": {"unit": "°C", "description": "Exhaust air after heat recovery unit"},
                "Suhteellinen_kosteus": {"unit": "%", "description": "Relative humidity (exhaust side)"},
                "Kastepiste": {"unit": "°C", "description": "Dew point temperature"},
                "RH_lampotila": {"unit": "°C", "description": "RH sensor temperature"},
                "Lampopumppu_teho": {"unit": "kW", "description": "Heat pump power consumption"},
                "Lisavastus_teho": {"unit": "kW", "description": "Auxiliary heater power consumption"},
                "Lampopumppu_energia": {"unit": "kWh", "description": "Heat pump cumulative energy"},
                "Lisavastus_energia": {"unit": "kWh", "description": "Auxiliary heater cumulative energy"},
                "U1_jannite": {"unit": "V", "description": "Phase 1 voltage"},
                "U2_jannite": {"unit": "V", "description": "Phase 2 voltage"},
                "U3_jannite": {"unit": "V", "description": "Phase 3 voltage"},
                "Toimilaite_ohjaus": {"unit": "%", "description": "Heating valve actuator position"},
                "Toimilaite_asetusarvo": {"unit": "%", "description": "Heating valve setpoint"},
                "Toimilaite_pakotus": {"unit": "-", "description": "Heating valve override status"},
            },
            "tags": {
                "sensor_group": ["ivk_temp", "humidity", "power", "energy", "voltage", "actuator"]
            }
        },
        "rooms": {
            "description": "Room temperature data from WAGO controller (logged hourly)",
            "fields": {
                "MH_Seela": {"unit": "°C", "description": "Bedroom - Seela"},
                "MH_Aarni": {"unit": "°C", "description": "Bedroom - Aarni"},
                "MH_aikuiset": {"unit": "°C", "description": "Bedroom - Adults"},
                "MH_alakerta": {"unit": "°C", "description": "Bedroom - Downstairs guest room"},
                "Ylakerran_aula": {"unit": "°C", "description": "Upstairs hallway"},
                "Keittio": {"unit": "°C", "description": "Kitchen"},
                "Eteinen": {"unit": "°C", "description": "Entrance hall"},
                "Kellari": {"unit": "°C", "description": "Basement main area"},
                "Kellari_eteinen": {"unit": "°C", "description": "Basement entrance"},
                "MH_Seela_PID": {"unit": "%", "description": "Seela room heating demand (0-100%)"},
                "MH_Aarni_PID": {"unit": "%", "description": "Aarni room heating demand (0-100%)"},
                "MH_aikuiset_PID": {"unit": "%", "description": "Adults room heating demand (0-100%)"},
                "MH_alakerta_PID": {"unit": "%", "description": "Downstairs room heating demand (0-100%)"},
                "Ylakerran_aula_PID": {"unit": "%", "description": "Upstairs hallway heating demand (0-100%)"},
                "Keittio_PID": {"unit": "%", "description": "Kitchen heating demand (0-100%)"},
                "Eteinen_PID": {"unit": "%", "description": "Entrance heating demand (0-100%)"},
                "Kellari_PID": {"unit": "%", "description": "Basement heating demand (0-100%)"},
                "Kellari_eteinen_PID": {"unit": "%", "description": "Basement entrance heating demand (0-100%)"},
            },
            "tags": {
                "room_type": ["bedroom", "common", "basement", "pid", "energy"]
            }
        },
        "ruuvi": {
            "description": "Ruuvi Bluetooth sensor data via MQTT (near real-time)",
            "fields": {
                "temperature": {"unit": "°C", "description": "Temperature"},
                "humidity": {"unit": "%", "description": "Relative humidity"},
                "pressure": {"unit": "hPa", "description": "Atmospheric pressure"},
                "battery": {"unit": "mV", "description": "Battery voltage"},
                "tx_power": {"unit": "dBm", "description": "TX power"},
                "movement_counter": {"unit": "count", "description": "Movement counter"},
                "acceleration_x": {"unit": "mg", "description": "X-axis acceleration"},
                "acceleration_y": {"unit": "mg", "description": "Y-axis acceleration"},
                "acceleration_z": {"unit": "mg", "description": "Z-axis acceleration"},
                "co2": {"unit": "ppm", "description": "CO₂ concentration (format 225 only)"},
                "pm2_5": {"unit": "µg/m³", "description": "PM2.5 particulate matter (format 225 only)"},
                "voc": {"unit": "index", "description": "VOC index (format 225 only)"},
                "nox": {"unit": "index", "description": "NOx index (format 225 only)"},
                "luminosity": {"unit": "lux", "description": "Luminosity (format 225 only)"},
                "sound_avg": {"unit": "dBA", "description": "Average sound level (format 225 only)"},
                "sound_peak": {"unit": "dBA", "description": "Peak sound level (format 225 only)"},
            },
            "tags": {
                "sensor_name": ["Olohuone", "Keittiö", "Ulkolämpötila", "Sauna", "Kellari", "Autotalli"]
            }
        },
        "thermia": {
            "description": "Thermia ground-source heat pump data via ThermIQ-ROOM2 MQTT (~30s)",
            "fields": {
                "outdoor_temp": {"unit": "°C", "description": "Outdoor temperature (heat pump sensor)"},
                "indoor_temp": {"unit": "°C", "description": "Indoor temperature (room sensor)"},
                "supply_temp": {"unit": "°C", "description": "Heating supply water temperature"},
                "return_temp": {"unit": "°C", "description": "Heating return water temperature"},
                "hotwater_temp": {"unit": "°C", "description": "Hot water tank temperature"},
                "brine_in_temp": {"unit": "°C", "description": "Brine return from ground (warmer)"},
                "brine_out_temp": {"unit": "°C", "description": "Brine to ground (colder)"},
                "compressor": {"unit": "0/1", "description": "Compressor running status"},
                "aux_heater_3kw": {"unit": "0/1", "description": "3kW auxiliary heater status"},
                "aux_heater_6kw": {"unit": "0/1", "description": "6kW auxiliary heater status"},
                "hotwater_production": {"unit": "0/1", "description": "Hot water production active"},
                "heating_allowed": {"unit": "0/1", "description": "Heating allowed status"},
                "runtime_compressor": {"unit": "h", "description": "Compressor runtime counter"},
                "runtime_3kw": {"unit": "h", "description": "3kW heater runtime counter"},
                "runtime_6kw": {"unit": "h", "description": "6kW heater runtime counter"},
                "runtime_hotwater": {"unit": "h", "description": "Hot water production runtime counter"},
                "heatcurve_set": {"unit": "°C", "description": "Heat curve setpoint"},
                "hotwater_start_temp": {"unit": "°C", "description": "Hot water production start threshold"},
                "hotwater_stop_temp": {"unit": "°C", "description": "Hot water production stop threshold"},
                "brine_min_t": {"unit": "°C", "description": "Brine minimum temperature limit"},
            },
            "tags": {
                "data_type": ["temperature", "status", "alarm", "performance", "runtime", "setting"]
            }
        },
        "lights": {
            "description": "Light switch status from HTTP API polling (every 5 minutes)",
            "fields": {
                "is_on": {"unit": "0/1", "description": "Light on/off status"},
            },
            "tags": {
                "name": "Light switch name",
                "floor": ["1st floor", "2nd floor", "basement"]
            }
        },
    }
}

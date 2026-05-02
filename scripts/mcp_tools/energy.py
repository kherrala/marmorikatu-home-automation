"""Energy tools: consumption, electricity prices, energy cost, heating status."""

import json
import logging
import traceback
from datetime import datetime, timezone, timedelta

from mcp.types import Tool, TextContent

from .config import INFLUXDB_BUCKET
from .influxdb import execute_flux_query

log = logging.getLogger("mcp-server")

TOOLS = [
    Tool(
        name="get_energy_consumption",
        description="""Get estimated energy consumption breakdown by component.

Estimates consumption (kWh) for: heat pump (compressor + aux heaters),
sauna, lighting, and HVAC fan. Uses component status data from InfluxDB
with the same calculation as the Energy Cost dashboard.""",
        inputSchema={
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": "Time range (e.g., '-24h', '-7d', '-30d')",
                    "default": "-7d"
                }
            },
            "required": []
        }
    ),
    Tool(
        name="get_electricity_prices",
        description="""Get current and upcoming electricity spot prices (Nord Pool Finland).

Returns the current hour's price, today's price range (min/max/average),
hourly price schedule for today and tomorrow (if available), and highlights
the cheapest and most expensive hours. Prices are in c/kWh including tax.""",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    Tool(
        name="get_heating_status",
        description="""Get the current heating optimizer status.

Returns the current price tier (CHEAP/NORMAL/EXPENSIVE/PRE_HEAT), heat pump
setpoint, EVU mode (energy utility lockout) status, auxiliary heater status,
effective target temperature, and current electricity price. Shows how the
optimizer is adjusting heating based on electricity prices.""",
        inputSchema={
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": "Time range for status history (e.g., '-6h', '-24h'). Default returns only latest status.",
                    "default": "-1h"
                }
            },
            "required": []
        }
    ),
    Tool(
        name="get_energy_cost",
        description="""Estimate electricity consumption and cost breakdown by component.

Calculates estimated consumption (kWh) and cost (EUR) for: heat pump
(compressor + aux heaters), sauna, lighting, and HVAC fan. Uses component
status data from InfluxDB combined with spot electricity prices. Cost model:
spot price + 0.49 c/kWh margin + 6.09 c/kWh transfer fee.""",
        inputSchema={
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": "Time range (e.g., '-24h', '-7d', '-1d')",
                    "default": "-24h"
                }
            },
            "required": []
        }
    ),
]


def _query_hp_consumption(time_range):
    """Heat-pump and aux-heater consumption from the OR-WE-517 meters.

    Reads the cumulative `Total_Active_Energy` (kWh) from the `hvac`
    measurement (sensor_group=energy, meter=heatpump|extra) at the start
    and end of the range and returns the delta. Replaces the previous
    compressor-on-time × wattage estimate, which only worked when the
    heat-pump module reported on/off statuses but never reflected actual
    draw (e.g., variable-speed compressor).
    """
    def _delta(meter):
        flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "hvac" and r.sensor_group == "energy")
  |> filter(fn: (r) => r.meter == "{meter}")
  |> filter(fn: (r) => r._field == "Total_Active_Energy")
  |> spread()
"""
        rows = execute_flux_query(flux)
        if not rows:
            return 0.0
        v = rows[0].get("_value")
        return float(v) if v is not None else 0.0

    return _delta("heatpump"), _delta("extra")


def _query_lighting_kwh(time_range, watt_per_light=10):
    flux_lights = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "lights" and r._field == "is_on")
  |> toFloat()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum()
  |> group()
"""
    light_data = execute_flux_query(flux_lights)
    return sum((r.get("_value", 0) or 0) * watt_per_light / 1000.0 for r in light_data)


def _query_sauna_kwh(time_range):
    flux_sauna = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and r.sensor_name == "Sauna")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with _value: if r._value > 50.0 then 1.0 else 0.0}}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
    sauna_data = execute_flux_query(flux_sauna)
    return sum((r.get("_value", 0) or 0) * 6.0 for r in sauna_data)


def _query_fan_kwh(time_range, watt_fan=300):
    flux_fan = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "ruuvi" and (r.sensor_name == "Keittio" or r.sensor_name == "Keittiö"))
  |> filter(fn: (r) => r._field == "pressure")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
"""
    fan_data = execute_flux_query(flux_fan)
    return len(fan_data) * watt_fan / 1000.0


async def handle_get_energy_consumption(arguments):
    try:
        time_range = arguments.get("time_range", "-7d")

        hp_comp_kwh, hp_aux_kwh = _query_hp_consumption(time_range)
        light_kwh = _query_lighting_kwh(time_range)
        sauna_kwh = _query_sauna_kwh(time_range)
        fan_kwh = _query_fan_kwh(time_range)

        total_kwh = hp_comp_kwh + hp_aux_kwh + light_kwh + sauna_kwh + fan_kwh

        result = {
            "time_range": time_range,
            "heat_pump_compressor_kwh": round(hp_comp_kwh, 2),
            "heat_pump_aux_heaters_kwh": round(hp_aux_kwh, 2),
            "lighting_kwh": round(light_kwh, 2),
            "sauna_kwh": round(sauna_kwh, 2),
            "hvac_fan_kwh": round(fan_kwh, 2),
            "total_kwh": round(total_kwh, 2),
            "note": "Estimates based on component status data and assumed wattages",
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_electricity_prices(arguments):
    try:
        now_utc = datetime.now(timezone.utc)

        flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: -24h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> group()
  |> sort(columns: ["_time"])
"""
        results = execute_flux_query(flux)

        if not results:
            return [TextContent(type="text", text="Sähkön hintatietoja ei ole saatavilla.")]

        prices = []
        for r in results:
            prices.append({
                "time": r["_time"],
                "price_c_kwh": round(r.get("_value", 0), 2),
            })

        current_price = None
        for p in prices:
            pt = datetime.fromisoformat(p["time"])
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=timezone.utc)
            if pt <= now_utc:
                current_price = p
            else:
                break

        eet_offset = timedelta(hours=2)
        today_date = (now_utc + eet_offset).date()
        tomorrow_date = today_date + timedelta(days=1)

        today_prices = []
        tomorrow_prices = []
        for p in prices:
            pt = datetime.fromisoformat(p["time"])
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=timezone.utc)
            d = (pt + eet_offset).date()
            if d == today_date:
                today_prices.append(p)
            elif d == tomorrow_date:
                tomorrow_prices.append(p)

        def price_stats(price_list):
            vals = [p["price_c_kwh"] for p in price_list]
            if not vals:
                return None
            cheapest = min(price_list, key=lambda p: p["price_c_kwh"])
            most_expensive = max(price_list, key=lambda p: p["price_c_kwh"])
            return {
                "min_c_kwh": min(vals),
                "max_c_kwh": max(vals),
                "avg_c_kwh": round(sum(vals) / len(vals), 2),
                "cheapest_hour": cheapest["time"],
                "most_expensive_hour": most_expensive["time"],
                "hours_count": len(vals),
            }

        result = {
            "current_price_c_kwh": current_price["price_c_kwh"] if current_price else None,
            "current_hour": current_price["time"] if current_price else None,
            "today": price_stats(today_prices),
            "today_prices": today_prices,
            "tomorrow_available": len(tomorrow_prices) > 0,
        }
        if tomorrow_prices:
            result["tomorrow"] = price_stats(tomorrow_prices)
            result["tomorrow_prices"] = tomorrow_prices

        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_heating_status(arguments):
    try:
        time_range = arguments.get("time_range", "-1h")

        flux = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "heating_optimizer")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
"""
        results = execute_flux_query(flux)

        if not results:
            return [TextContent(type="text", text="Lämmitysoptimoinnin tilatietoja ei ole saatavilla.")]

        latest = results[0]

        tier = latest.get("tier", "UNKNOWN")
        setpoint = latest.get("setpoint")
        evu_active = latest.get("evu_active")
        boiler_steps = latest.get("boiler_steps")
        effective_target = latest.get("effective_target")
        price = latest.get("price")
        outdoor_temp = latest.get("outdoor_temp")

        boiler_label = {0: "OFF", 1: "3kW", 2: "3+6kW"}.get(
            int(boiler_steps) if boiler_steps is not None else -1, str(boiler_steps)
        )

        tier_descriptions = {
            "CHEAP": "Halpa sähkö — normaali lämmitys",
            "NORMAL": "Normaali hinta — normaali lämmitys",
            "EXPENSIVE": "Kallis sähkö — EVU-tila päällä, lämmitystä rajoitettu",
            "PRE_HEAT": "Esilämmitys — nostettu lämpötila ennen kallista jaksoa",
        }

        result = {
            "timestamp": latest.get("_time"),
            "tier": tier,
            "tier_description": tier_descriptions.get(tier, tier),
            "setpoint_c": setpoint,
            "effective_target_c": effective_target,
            "evu_active": bool(int(evu_active)) if evu_active is not None else None,
            "boiler_steps": boiler_label,
            "current_price_c_kwh": round(price, 2) if price is not None else None,
            "outdoor_temp_c": round(outdoor_temp, 1) if outdoor_temp is not None else None,
        }

        if len(results) > 1:
            history = []
            for r in results[:24]:
                history.append({
                    "time": r.get("_time"),
                    "tier": r.get("tier"),
                    "setpoint": r.get("setpoint"),
                    "effective_target": r.get("effective_target"),
                    "price": round(r["price"], 2) if r.get("price") is not None else None,
                })
            result["history"] = history

        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def handle_get_energy_cost(arguments):
    try:
        time_range = arguments.get("time_range", "-24h")
        MARGIN = 0.49
        TRANSFER = 6.09

        hp_comp_kwh, hp_aux_kwh = _query_hp_consumption(time_range)
        hp_kwh = hp_comp_kwh + hp_aux_kwh
        light_kwh = _query_lighting_kwh(time_range)
        sauna_kwh = _query_sauna_kwh(time_range)
        fan_kwh = _query_fan_kwh(time_range)

        total_kwh = hp_kwh + light_kwh + fan_kwh + sauna_kwh

        flux_price = f"""
from(bucket: "{INFLUXDB_BUCKET}")
  |> range(start: {time_range})
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "price_with_tax")
  |> mean()
"""
        price_data = execute_flux_query(flux_price)
        avg_price = price_data[0].get("_value", 5.0) if price_data else 5.0

        total_price_c_kwh = avg_price + MARGIN + TRANSFER
        total_cost_eur = total_kwh * total_price_c_kwh / 100.0

        result = {
            "time_range": time_range,
            "consumption_kwh": {
                "heat_pump": round(hp_kwh, 2),
                "lighting": round(light_kwh, 2),
                "sauna": round(sauna_kwh, 2),
                "hvac_fan": round(fan_kwh, 2),
                "total": round(total_kwh, 2),
            },
            "cost": {
                "avg_spot_price_c_kwh": round(avg_price, 2),
                "margin_c_kwh": MARGIN,
                "transfer_c_kwh": TRANSFER,
                "total_price_c_kwh": round(total_price_c_kwh, 2),
                "estimated_total_eur": round(total_cost_eur, 2),
            },
            "note": "Estimates based on component status data and assumed wattages",
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False, default=str))]
    except Exception as e:
        log.error("get_energy_cost error: %s\n%s", e, traceback.format_exc())
        return [TextContent(type="text", text=f"Error: {str(e)}")]


HANDLERS = {
    "get_energy_consumption": handle_get_energy_consumption,
    "get_electricity_prices": handle_get_electricity_prices,
    "get_heating_status": handle_get_heating_status,
    "get_energy_cost": handle_get_energy_cost,
}

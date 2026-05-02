# MCP Server for Claude Desktop

An MCP (Model Context Protocol) server that enables natural language queries
about building automation data from Claude Desktop.

## Quick Start

1. Start the MCP server along with other services:
   ```bash
   docker compose up -d
   ```

2. The MCP server will be available at: `http://localhost:3001/mcp`

3. Configure Claude Desktop (Settings → Developer → MCP Servers → Add):
   - **Name**: Building Automation
   - **URL**: `http://localhost:3001/mcp`

4. Restart Claude Desktop if needed

Alternatively, add to Claude Desktop MCP configuration file:

```json
{
  "mcpServers": {
    "building-automation": {
      "url": "http://localhost:3001/mcp"
    }
  }
}
```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `http://localhost:3001/mcp` | MCP streamable-HTTP endpoint for Claude Desktop |
| `http://localhost:3001/health` | Health check endpoint |

### Verify Server is Running

```bash
# Check health
curl http://localhost:3001/health

# View logs
docker compose logs -f mcp
```

## Available Tools

| Tool | Description |
|------|-------------|
| `describe_schema` | Get complete data model with all measurements, fields, and units |
| `list_measurements` | List available measurements (hvac, rooms, ruuvi, thermia) |
| `describe_measurement` | Get details about a specific measurement |
| `query_data` | Execute custom Flux queries (results limited to 100 rows) |
| `get_latest` | Get most recent values for specified fields |
| `get_statistics` | Get min/max/mean/count for a field over time |
| `get_time_range` | Get data availability for a measurement |
| `get_heat_recovery_efficiency` | Calculate HRU efficiency with summary stats |
| `get_energy_consumption` | Get energy consumption summary |
| `get_room_temperatures` | Get all room temps and heating demand |
| `get_air_quality` | Get CO2, PM2.5, VOC, NOx from kitchen sensor |
| `get_freezing_probability` | Heat exchanger freezing risk (0–95%) |
| `compare_indoor_outdoor` | Compare indoor vs outdoor temperatures |
| `get_thermia_status` | Get current heat pump status (temps, components, alarms) |
| `get_thermia_temperatures` | Get heat pump temperature time series |

## Example Queries in Claude Desktop

Once connected, you can ask Claude questions like:

- "What's the current outdoor temperature?"
- "Show me the heat recovery efficiency for the last week"
- "What's the air quality in the kitchen?"
- "Compare indoor and outdoor temperatures over the last 24 hours"
- "How much energy has the heat pump consumed this month?"
- "List all room temperatures and heating demand"
- "Run a Flux query to get the last 24 hours of humidity data"
- "What's the current heat pump status?"
- "Show me the brine circuit temperatures for the last week"

## Implementation

- **Source**: `scripts/mcp_server.py`
- **Transport**: Streamable HTTP via Starlette + uvicorn (replaces the
  earlier SSE transport, which lost session state on every server restart
  and made Claude Desktop's built-in client disconnect permanently)
- **Protocol**: MCP SDK (`mcp.server`, `mcp.server.streamable_http_manager`)
- **Data source**: InfluxDB Flux queries against `building_automation` bucket
- **Schema**: Built-in schema documentation with field descriptions and units,
  served via the `describe_schema` tool

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_URL` | `http://influxdb:8086` | InfluxDB connection URL |
| `INFLUXDB_TOKEN` | — | API authentication token |
| `INFLUXDB_ORG` | `wago` | Organization name |
| `INFLUXDB_BUCKET` | `building_automation` | Target bucket |
| `MCP_HOST` | `0.0.0.0` | Server bind address |
| `MCP_PORT` | `3001` | Server port |

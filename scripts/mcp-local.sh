#!/bin/bash
# MCP Server wrapper for Claude Desktop (local Python execution)
#
# This script runs the MCP server directly with Python, without Docker.
# Useful if Docker is not available or for development.
#
# Prerequisites:
# 1. Python 3.10+ with venv
# 2. InfluxDB must be accessible at localhost:8086
#
# Usage in Claude Desktop config (~/.config/claude/claude_desktop_config.json):
# {
#   "mcpServers": {
#     "building-automation": {
#       "command": "/path/to/wago-csv-explorer/scripts/mcp-local.sh"
#     }
#   }
# }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Create/activate virtual environment if needed
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r scripts/requirements.txt
else
    source venv/bin/activate
fi

# Set environment variables for local InfluxDB
export INFLUXDB_URL="http://localhost:8086"
export INFLUXDB_TOKEN="wago-secret-token"
export INFLUXDB_ORG="wago"
export INFLUXDB_BUCKET="building_automation"

# Run the MCP server
exec python scripts/mcp_server.py

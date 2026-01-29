#!/bin/bash
# MCP Server wrapper for Claude Desktop
#
# This script runs the MCP server in a Docker container with stdio transport.
# Claude Desktop will spawn this script and communicate via stdin/stdout.
#
# Prerequisites:
# 1. Docker must be running
# 2. The wago-csv-explorer containers must be running (at least influxdb)
# 3. Build the MCP image: docker compose build mcp
#
# Usage in Claude Desktop config (~/.config/claude/claude_desktop_config.json):
# {
#   "mcpServers": {
#     "building-automation": {
#       "command": "/path/to/wago-csv-explorer/scripts/mcp-claude-desktop.sh"
#     }
#   }
# }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Run the MCP container interactively, connecting to the existing network
exec docker run --rm -i \
    --network wago-csv-explorer_default \
    -e INFLUXDB_URL=http://wago-influxdb:8086 \
    -e INFLUXDB_TOKEN=wago-secret-token \
    -e INFLUXDB_ORG=wago \
    -e INFLUXDB_BUCKET=building_automation \
    wago-csv-explorer-mcp

#!/bin/bash
set -e

echo "========================================"
echo "WAGO Building Automation Data Explorer"
echo "========================================"

# Start Docker containers
echo "Starting InfluxDB and Grafana..."
docker compose up -d

# Wait for InfluxDB to be ready
echo "Waiting for InfluxDB to be ready..."
until curl -sf http://localhost:8086/ping > /dev/null 2>&1; do
    echo "  Waiting..."
    sleep 2
done
echo "InfluxDB is ready!"

# Set up Python virtual environment if not exists
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment and install dependencies
echo "Installing Python dependencies..."
source venv/bin/activate
pip install -q -r scripts/requirements.txt

# Run the data importer
echo ""
echo "Importing CSV data into InfluxDB..."
python scripts/import_data.py

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Access the services:"
echo "  - Grafana:  http://localhost:3000  (admin/admin)"
echo "  - InfluxDB: http://localhost:8086  (admin/adminpassword)"
echo ""
echo "To stop: docker compose down"
echo ""

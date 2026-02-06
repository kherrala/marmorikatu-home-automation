#!/bin/bash
# Generate self-signed certificates for MCP server HTTPS proxy
#
# Usage: ./scripts/generate-certs.sh
#
# Creates certificates in ./certs/ directory

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CERT_DIR="$PROJECT_DIR/certs"

# Create certs directory
mkdir -p "$CERT_DIR"

# Certificate details
DAYS=365
COMMON_NAME="localhost"
SUBJECT="/C=FI/ST=Uusimaa/L=Helsinki/O=Building Automation/OU=MCP Server/CN=$COMMON_NAME"

echo "Generating self-signed certificates..."
echo "  Directory: $CERT_DIR"
echo "  Valid for: $DAYS days"
echo "  Common Name: $COMMON_NAME"

# Generate private key and certificate
openssl req -x509 -nodes -days $DAYS -newkey rsa:2048 \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "$SUBJECT" \
    -addext "subjectAltName=DNS:localhost,DNS:mcp,DNS:wago-mcp-proxy,IP:127.0.0.1"

# Set permissions
chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"

echo ""
echo "Certificates generated successfully!"
echo "  Certificate: $CERT_DIR/server.crt"
echo "  Private Key: $CERT_DIR/server.key"
echo ""
echo "To trust the certificate on macOS:"
echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain $CERT_DIR/server.crt"
echo ""
echo "To trust the certificate on Linux:"
echo "  sudo cp $CERT_DIR/server.crt /usr/local/share/ca-certificates/mcp-server.crt"
echo "  sudo update-ca-certificates"

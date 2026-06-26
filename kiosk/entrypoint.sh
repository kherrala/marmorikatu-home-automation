#!/bin/sh
# Copy the nginx config template (no variable substitution needed).
cp /etc/nginx/templates/default.conf.template /etc/nginx/conf.d/default.conf

# Generate TLS certificate only once — persisted in the kiosk-ssl volume.
# Subsequent container rebuilds skip this block entirely.
if [ ! -f /etc/nginx/ssl/kiosk.crt ]; then
  echo "Generating self-signed TLS certificate (first boot)..."
  mkdir -p /etc/nginx/ssl
  openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/kiosk.key \
    -out    /etc/nginx/ssl/kiosk.crt \
    -subj   "/CN=kiosk.local" \
    -addext "subjectAltName=IP:192.168.1.160,DNS:localhost"
  openssl x509 -in /etc/nginx/ssl/kiosk.crt -outform DER -out /etc/nginx/ssl/kiosk.cer
  echo "Certificate generated."
fi

# Always refresh the downloadable .cer from the (possibly pre-existing) volume cert.
mkdir -p /usr/share/nginx/cert
cp /etc/nginx/ssl/kiosk.cer /usr/share/nginx/cert/kiosk.cer

# Generate version stamp at container start (changes on every restart → triggers auto-reload)
date +%s > /usr/share/nginx/html/version.txt

# Runtime-selectable face/presence detector backend (faceapi | pico | motion).
# Lets us A/B detectors on the wall iPad by changing KIOSK_DETECTOR in
# docker-compose.yml and restarting — no rebuild. A ?detector= URL query
# overrides this per-load for quick trials.
printf "window.__KIOSK_CONFIG={detector:'%s'};\n" "${KIOSK_DETECTOR:-faceapi}" \
  > /usr/share/nginx/html/config.js

exec nginx -g 'daemon off;'

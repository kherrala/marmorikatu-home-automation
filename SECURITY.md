# Security Policy

## Important Notice

This project is designed for **local network deployment**. It includes default credentials and tokens intended for development and home use. If you deploy this system:

- Change all default passwords and tokens (`wago-secret-token`, Grafana admin, InfluxDB admin)
- Do not expose services directly to the internet
- Keep SSH keys and `.env` files out of version control (already in `.gitignore`)
- Review MQTT broker access controls

## Reporting a Vulnerability

If you discover a security issue, please open a GitHub issue. Since this is a home automation project with no user-facing public services, responsible disclosure via issues is appropriate.

# Contributing

Thanks for your interest in contributing to this project.

## Getting Started

1. Fork the repository
2. Clone your fork and create a feature branch
3. Start the development environment with `docker compose up -d`
4. Make your changes
5. Test locally by restarting the affected service (`docker compose up --build -d <service>`)
6. Open a pull request against `master`

## Development Workflow

### Grafana Dashboards

Edit dashboard JSON files in `grafana/provisioning/dashboards/` directly. After changes:

```bash
docker compose restart grafana
```

### Python Services

Each service has its own Dockerfile (`Dockerfile.<service>`). To rebuild and restart a service:

```bash
docker compose up --build -d <service>
```

### Conventions

- Dashboard UIDs follow the pattern `wago-*`, `ruuvi-*`, `thermia-*`, etc.
- Flux queries use `v.timeRangeStart`, `v.timeRangeStop`, `v.windowPeriod` for Grafana integration
- Field names use ASCII with display name overrides for Finnish characters
- Python services use minimal dependencies and write directly to InfluxDB

## Reporting Issues

Open an issue describing:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Relevant logs (`docker compose logs <service>`)

## Code of Conduct

Be respectful and constructive. This is a personal project shared for the benefit of others working on similar home automation setups.

"""Lightweight liveness heartbeat shared by the long-running services.

Each service calls ``touch_health()`` on a successful tick / message, which
updates the mtime of a small health file. A Docker ``HEALTHCHECK`` watches that
file's age, so a container that is *running but stuck* (every tick throwing, a
hung MQTT connection, no data flowing) shows up as ``(unhealthy)`` in
``docker compose ps`` instead of a misleading ``Up``.

This was added after a midsummer sun-calc crash silently froze the
lights-optimizer loop for weeks while the container still reported ``Up``.

The matching healthcheck (kept identical across every Dockerfile) is::

    HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
      CMD python -c "import os,time,sys; f=os.environ.get('HEALTH_FILE','/tmp/service_healthy'); a=(time.time()-os.path.getmtime(f)) if os.path.exists(f) else 1e9; sys.exit(0 if a<float(os.environ.get('HEALTH_MAX_AGE','300')) else 1)"

HEALTH_MAX_AGE (seconds) is tuned per service in docker-compose.yml when the
tick interval is longer than the 300 s default (e.g. the heating optimizer,
which sleeps until the next hourly price boundary).
"""
import logging
import os
import time

log = logging.getLogger(__name__)

HEALTH_FILE = os.environ.get("HEALTH_FILE", "/tmp/service_healthy")


def touch_health(path: str = HEALTH_FILE) -> None:
    """Best-effort update of the health-file mtime. Never raises — a failure to
    write the heartbeat must not take down the service it is meant to watch."""
    try:
        with open(path, "w") as fh:
            fh.write(str(int(time.time())))
    except OSError as e:
        log.warning("could not write health file %s: %s", path, e)

#!/usr/bin/env python3
"""
Unifi Protect webhook bridge.

Listens for HTTP POSTs from the Unifi Protect alarm manager and dispatches
configurable actions (kiosk announcement, MQTT publish, …) based on the
alarm name and source camera. Keeps the rules in a separate JSON file so
new triggers (person at front door → porch light on, etc.) are config
edits rather than code edits.

Inbound POST body looks like (truncated):

    {
      "alarm": {
        "name": "Etupihalla ihminen",
        "sources": [{"device": "28704E1DFCCD", "type": "include"}],
        "triggers": [{"key": "person", "device": "28704E1DFCCD", ...}],
        "thumbnail": "data:image/jpeg;base64,/9j/4A...",
        "eventLocalLink": "https://192.168.1.1/protect/events/event/<id>"
      },
      "timestamp": 1779015456676
    }

Rules file format (see rules.example.json):

    {
      "rules": [
        {
          "match": {
            "alarm_name": "Etupihalla ihminen",
            "device": "28704E1DFCCD",
            "trigger_key": "person"
          },
          "cooldown_s": 60,
          "actions": [
            {
              "type": "announce",
              "text": "Etupihalla on ihminen.",
              "kind": "unifi_person_front",
              "priority": 1,
              "key": "unifi_person_front",
              "include_image": true,
              "image_duration_s": 300
            },
            {
              "type": "mqtt_publish",
              "topic": "marmorikatu/light/47/set",
              "payload": "true"
            }
          ]
        }
      ]
    }

A rule matches when every key under `match` matches the inbound payload
(missing keys = wildcard). Multiple rules can fire for the same event.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import paho.mqtt.publish as mqtt_publish

# ── Configuration ────────────────────────────────────────────────────────────

LISTEN_HOST   = os.environ.get("UNIFI_WEBHOOK_HOST", "0.0.0.0")
LISTEN_PORT   = int(os.environ.get("UNIFI_WEBHOOK_PORT", "5645"))

BRIDGE_PUSH_URL = os.environ.get(
    "BRIDGE_PUSH_URL", "http://claude-bridge:3002/announcements/push"
)
BRIDGE_PUSH_TOKEN = os.environ.get("ANNOUNCE_PUSH_TOKEN", "")
BRIDGE_PUSH_TIMEOUT_S = float(os.environ.get("ANNOUNCE_PUSH_TIMEOUT", "5"))

MQTT_BROKER = os.environ.get("MQTT_BROKER", "freenas.kherrala.fi")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "1883"))

# InfluxDB — only used by the `mqtt_pulse` action to skip publishing ON when
# the target light is already on. Optional: if any of these are missing or
# the query fails, the pulse proceeds anyway (over-firing ON is safer than
# leaving a dark walkway).
INFLUXDB_URL    = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN  = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG", "wago")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "building_automation")
INFLUX_TIMEOUT_S = float(os.environ.get("UNIFI_WEBHOOK_INFLUX_TIMEOUT", "3"))
# How recently must the `is_on=true` sample have been written to count as
# "currently on"? The plc subscriber writes every ~13s; 60s tolerates a
# slow tick without false-skipping when the relay is genuinely already on.
PULSE_ALREADY_ON_FRESH_S = float(os.environ.get("UNIFI_WEBHOOK_PULSE_FRESH_S", "60"))

RULES_PATH  = os.environ.get("UNIFI_WEBHOOK_RULES", "/config/rules.json")

# Optional shared-secret check. When set, requests must carry either
# header `X-Webhook-Token: <token>` or query string `?token=<token>`.
WEBHOOK_TOKEN = os.environ.get("UNIFI_WEBHOOK_TOKEN", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("unifi-webhook")


# ── Rules ────────────────────────────────────────────────────────────────────

@dataclass
class Rule:
    match: dict[str, Any]
    actions: list[dict[str, Any]]
    cooldown_s: float = 0.0
    last_fired_at: float = field(default=0.0)

    def matches(self, ctx: dict[str, Any]) -> bool:
        for key, want in self.match.items():
            got = ctx.get(key)
            if isinstance(want, list):
                if got not in want:
                    return False
            else:
                if got != want:
                    return False
        return True


_rules: list[Rule] = []
_rules_lock = threading.Lock()
_rules_mtime: float = 0.0


def _load_rules() -> None:
    global _rules, _rules_mtime
    try:
        st = os.stat(RULES_PATH)
    except FileNotFoundError:
        log.warning("rules file %s not found — no rules loaded", RULES_PATH)
        with _rules_lock:
            _rules = []
            _rules_mtime = 0.0
        return
    if st.st_mtime == _rules_mtime:
        return
    try:
        with open(RULES_PATH, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        log.error("failed to load rules %s: %s", RULES_PATH, e)
        return
    new_rules: list[Rule] = []
    for entry in doc.get("rules", []):
        try:
            new_rules.append(Rule(
                match=dict(entry.get("match") or {}),
                actions=list(entry.get("actions") or []),
                cooldown_s=float(entry.get("cooldown_s") or 0.0),
            ))
        except (TypeError, ValueError) as e:
            log.warning("skipping malformed rule %r: %s", entry, e)
    with _rules_lock:
        _rules = new_rules
        _rules_mtime = st.st_mtime
    log.info("loaded %d rules from %s", len(new_rules), RULES_PATH)


# ── Action handlers ──────────────────────────────────────────────────────────

def _push_announcement(action: dict[str, Any], ctx: dict[str, Any]) -> None:
    text = (action.get("text") or "").strip()
    if not text:
        log.warning("announce action missing text: %r", action)
        return
    payload: dict[str, Any] = {
        "text":     text,
        "kind":     action.get("kind", "unifi_webhook"),
        "priority": int(action.get("priority", 1)),
        "key":      action.get("key", action.get("kind", "unifi_webhook")),
        "ts":       ctx.get("ts") or time.time(),
    }
    if action.get("include_image") and ctx.get("thumbnail"):
        payload["image"] = ctx["thumbnail"]
        dur = action.get("image_duration_s")
        if dur is not None:
            try:
                payload["image_duration_s"] = float(dur)
            except (TypeError, ValueError):
                pass
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if BRIDGE_PUSH_TOKEN:
        headers["X-Announce-Token"] = BRIDGE_PUSH_TOKEN
    req = urllib.request.Request(BRIDGE_PUSH_URL, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=BRIDGE_PUSH_TIMEOUT_S) as resp:
            resp.read()
        log.info("announced %s: %s", payload["kind"], text)
    except (urllib.error.URLError, OSError) as e:
        log.warning("announce push failed: %s", e)


def _publish_mqtt(action: dict[str, Any], ctx: dict[str, Any]) -> None:
    topic = action.get("topic")
    if not topic:
        log.warning("mqtt_publish missing topic: %r", action)
        return
    payload = action.get("payload", "")
    try:
        qos    = int(action.get("qos", 1))
        retain = bool(action.get("retain", False))
        mqtt_publish.single(
            topic=topic,
            payload=str(payload),
            qos=qos,
            retain=retain,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            client_id=f"unifi-webhook-{int(time.time() * 1000)}",
        )
        log.info("mqtt publish %s → %s (qos=%d retain=%s)", topic, payload, qos, retain)
    except Exception as e:
        log.warning("mqtt publish %s failed: %s", topic, e)


def _light_currently_on(light_id: int) -> bool | None:
    """Return True/False if InfluxDB has a fresh `lights.is_on` reading,
    None if the query failed or the data is too stale to trust."""
    if not INFLUXDB_TOKEN:
        return None
    flux = (
        f'from(bucket: "{INFLUXDB_BUCKET}")\n'
        f'  |> range(start: -10m)\n'
        f'  |> filter(fn: (r) => r._measurement == "lights" '
        f'and r._field == "is_on" and r.light_id == "{light_id}")\n'
        f'  |> last()'
    )
    url = f"{INFLUXDB_URL.rstrip('/')}/api/v2/query?org={INFLUXDB_ORG}"
    req = urllib.request.Request(
        url,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {INFLUXDB_TOKEN}",
            "Content-Type":  "application/vnd.flux",
            "Accept":        "application/csv",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=INFLUX_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        log.warning("influx query for light %d failed: %s", light_id, e)
        return None
    # CSV columns: ,result,table,_start,_stop,_time,_value,_field,_measurement,light_id,...
    import csv, io
    reader = csv.reader(io.StringIO(text))
    header: list[str] = []
    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        if not header:
            header = row
            continue
        try:
            t_idx = header.index("_time")
            v_idx = header.index("_value")
        except ValueError:
            return None
        try:
            from datetime import datetime as _dt
            sample_ts = _dt.fromisoformat(row[t_idx].replace("Z", "+00:00")).timestamp()
        except (ValueError, IndexError):
            return None
        age_s = time.time() - sample_ts
        if age_s > PULSE_ALREADY_ON_FRESH_S:
            return None
        value = row[v_idx]
        try:
            return float(value) > 0.5
        except ValueError:
            return value.strip().lower() in ("true", "1", "t")
    return None


# Pending pulse OFF timers, keyed by topic so a fresh pulse on the same
# light cancels its predecessor and extends the on-window. Cleared by the
# timer's own callback after publishing OFF.
_pulse_timers: dict[str, threading.Timer] = {}
_pulse_lock = threading.Lock()


def _mqtt_send(topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> bool:
    try:
        mqtt_publish.single(
            topic=topic,
            payload=str(payload),
            qos=qos,
            retain=retain,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            client_id=f"unifi-webhook-{int(time.time() * 1000)}",
        )
        return True
    except Exception as e:
        log.warning("mqtt publish %s failed: %s", topic, e)
        return False


def _pulse_off(topic: str, payload: str) -> None:
    with _pulse_lock:
        _pulse_timers.pop(topic, None)
    if _mqtt_send(topic, payload):
        log.info("pulse OFF %s → %s", topic, payload)


def _publish_mqtt_pulse(action: dict[str, Any], ctx: dict[str, Any]) -> None:
    topic = action.get("topic")
    if not topic:
        log.warning("mqtt_pulse missing topic: %r", action)
        return
    on_payload  = str(action.get("on_payload",  "true"))
    off_payload = str(action.get("off_payload", "false"))
    try:
        duration_s = float(action.get("duration_s", 300))
    except (TypeError, ValueError):
        duration_s = 300.0
    duration_s = max(5.0, duration_s)
    skip_if_on = bool(action.get("skip_if_on", True))
    light_id   = action.get("light_id")

    if skip_if_on and light_id is not None:
        try:
            light_id_int = int(light_id)
        except (TypeError, ValueError):
            light_id_int = None
        if light_id_int is not None:
            state = _light_currently_on(light_id_int)
            if state is True:
                # Already on — extend the OFF timer if we own one (so the
                # pulse window doesn't expire mid-visit), but don't re-publish.
                with _pulse_lock:
                    existing = _pulse_timers.pop(topic, None)
                if existing is not None:
                    existing.cancel()
                    timer = threading.Timer(duration_s, _pulse_off, args=(topic, off_payload))
                    timer.daemon = True
                    with _pulse_lock:
                        _pulse_timers[topic] = timer
                    timer.start()
                    log.info("pulse %s: already on, extended OFF by %.0fs", topic, duration_s)
                else:
                    log.info("pulse %s: already on, not our pulse — leaving alone", topic)
                return
            # state is False or None — proceed.

    if not _mqtt_send(topic, on_payload):
        return
    log.info("pulse ON  %s → %s (OFF in %.0fs)", topic, on_payload, duration_s)

    # Replace any existing pending OFF for this topic so a fresh event
    # extends the on-window rather than cutting it short.
    with _pulse_lock:
        prev = _pulse_timers.pop(topic, None)
    if prev is not None:
        prev.cancel()
    timer = threading.Timer(duration_s, _pulse_off, args=(topic, off_payload))
    timer.daemon = True
    with _pulse_lock:
        _pulse_timers[topic] = timer
    timer.start()


ACTIONS = {
    "announce":     _push_announcement,
    "mqtt_publish": _publish_mqtt,
    "mqtt_pulse":   _publish_mqtt_pulse,
}


# ── Dispatch ─────────────────────────────────────────────────────────────────

def _extract_context(body: dict[str, Any]) -> dict[str, Any]:
    alarm = body.get("alarm") or {}
    triggers = alarm.get("triggers") or []
    first_trigger = triggers[0] if triggers else {}
    sources = alarm.get("sources") or []
    first_source = sources[0] if sources else {}
    return {
        "alarm_name":   alarm.get("name"),
        "device":       first_trigger.get("device") or first_source.get("device"),
        "trigger_key":  first_trigger.get("key"),
        "event_id":     first_trigger.get("eventId"),
        "event_link":   alarm.get("eventLocalLink"),
        "thumbnail":    alarm.get("thumbnail"),
        # Source-side timestamp lives on the trigger (ms epoch); the
        # envelope's top-level timestamp is when Protect dispatched the
        # webhook. Prefer the trigger time when present so dedup / age
        # checks downstream see the camera's view of the event.
        "ts":           (first_trigger.get("timestamp") or body.get("timestamp") or 0) / 1000.0,
    }


def _dispatch(body: dict[str, Any]) -> int:
    _load_rules()
    ctx = _extract_context(body)
    log.info(
        "event alarm=%r device=%s trigger=%s event_id=%s",
        ctx["alarm_name"], ctx["device"], ctx["trigger_key"], ctx["event_id"],
    )
    fired = 0
    now = time.monotonic()
    with _rules_lock:
        rules = list(_rules)
    for rule in rules:
        if not rule.matches(ctx):
            continue
        if rule.cooldown_s > 0 and (now - rule.last_fired_at) < rule.cooldown_s:
            log.info("skipping rule (cooldown): match=%s", rule.match)
            continue
        rule.last_fired_at = now
        for action in rule.actions:
            handler = ACTIONS.get(action.get("type", ""))
            if not handler:
                log.warning("unknown action type: %r", action)
                continue
            try:
                handler(action, ctx)
            except Exception as e:
                log.exception("action failed: %s — %s", action.get("type"), e)
        fired += 1
    if fired == 0:
        log.info("no matching rule for alarm=%r device=%s", ctx["alarm_name"], ctx["device"])
    return fired


# ── HTTP server ──────────────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    # Suppress the default per-request stderr log; we log inside _dispatch().
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def _auth_ok(self) -> bool:
        if not WEBHOOK_TOKEN:
            return True
        if self.headers.get("X-Webhook-Token", "") == WEBHOOK_TOKEN:
            return True
        # Allow ?token=… for systems that can't set custom headers.
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == "token" and v == WEBHOOK_TOKEN:
                        return True
        return False

    def do_GET(self) -> None:
        # Liveness probe — useful when wiring up Unifi or checking the
        # service from a browser. Doesn't expose anything sensitive.
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/healthz", "/"):
            body = b'{"ok":true,"service":"unifi-webhook"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if not self._auth_ok():
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 16 * 1024 * 1024:
            self.send_response(400)
            self.end_headers()
            return
        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("invalid POST body: %s", e)
            self.send_response(400)
            self.end_headers()
            return
        try:
            fired = _dispatch(body)
        except Exception as e:
            log.exception("dispatch error: %s", e)
            self.send_response(500)
            self.end_headers()
            return
        resp = json.dumps({"ok": True, "fired": fired}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


# ── Main ─────────────────────────────────────────────────────────────────────

_server: ThreadingHTTPServer | None = None


def _stop(signum, _frame):
    log.info("received %s, stopping", signal.Signals(signum).name)
    if _server is not None:
        threading.Thread(target=_server.shutdown, daemon=True).start()


def main() -> None:
    global _server
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    _load_rules()
    log.info("listening on %s:%d (rules=%s, bridge=%s)",
             LISTEN_HOST, LISTEN_PORT, RULES_PATH, BRIDGE_PUSH_URL)
    _server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), WebhookHandler)
    try:
        _server.serve_forever()
    finally:
        _server.server_close()
        log.info("shutdown complete")


if __name__ == "__main__":
    main()

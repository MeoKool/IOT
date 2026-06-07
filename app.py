from __future__ import annotations

import atexit
import os
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - handled at runtime for friendly errors
    mqtt = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# -----------------------------------------------------------------------------
# APP CONFIG
# -----------------------------------------------------------------------------
# Code defaults are enough to run directly on Raspberry Pi. Optional OS
# environment variables can still override values for temporary testing, e.g.
# APP_PORT=5001 python app.py. No .env file is required.
# -----------------------------------------------------------------------------

FALSE_VALUES = {"0", "false", "off", "no"}


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() not in FALSE_VALUES


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logging.warning(
            "Invalid integer for %s=%r; using %s", name, os.environ.get(name), default
        )
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        logging.warning(
            "Invalid number for %s=%r; using %s", name, os.environ.get(name), default
        )
        return default


def resolve_path(value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else BASE_DIR / path


APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", env_int("PORT", 5001))
APP_DEBUG = env_bool("APP_DEBUG", False)
UPDATE_INTERVAL_SECONDS = max(1.0, env_float("UPDATE_INTERVAL_SECONDS", 3.0))
REFRESH_INTERVAL_MS = int(UPDATE_INTERVAL_SECONDS * 1000)
CHART_MAX_POINTS = max(1, env_int("CHART_MAX_POINTS", 20))
HISTORY_DEFAULT_PER_PAGE = max(1, min(env_int("HISTORY_DEFAULT_PER_PAGE", 20), 100))
DATABASE_PATH = resolve_path(
    os.environ.get("DATABASE_PATH"), BASE_DIR / "smart_tank.db"
)

# -----------------------------------------------------------------------------
# SQLITE + REAL SENSOR DATA BACKEND
# -----------------------------------------------------------------------------
# Sensor rows are stored only when real data arrives from the Raspberry Pi
# Arduino bridge, MQTT, Swagger, or curl via /api/tank/sensor. The dashboard no
# longer generates or seeds sample data.
# -----------------------------------------------------------------------------

# MQTT integration for the Raspberry Pi gateway layer.
# Defaults match the final architecture: Mosquitto runs on the same Pi as Flask.
MQTT_ENABLED = env_bool("MQTT_ENABLED", True)
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = env_int("MQTT_PORT", 1883)
MQTT_KEEPALIVE = env_int("MQTT_KEEPALIVE", 60)
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "smart-water-tank-flask-dashboard")
MQTT_TOPIC_SENSOR = os.environ.get("MQTT_TOPIC_SENSOR", "tank/sensor")
MQTT_TOPIC_DATA = os.environ.get("MQTT_TOPIC_DATA", MQTT_TOPIC_SENSOR)
MQTT_TOPIC_LOG = os.environ.get("MQTT_TOPIC_LOG", "tank/data/log")
MQTT_TOPIC_CONTROL = os.environ.get("MQTT_TOPIC_CONTROL", "tank/control")
MQTT_TOPIC_ALERT = os.environ.get("MQTT_TOPIC_ALERT", "tank/alert")
MQTT_SUBSCRIBE_TOPICS = tuple(
    dict.fromkeys([MQTT_TOPIC_SENSOR, MQTT_TOPIC_DATA, MQTT_TOPIC_ALERT, MQTT_TOPIC_LOG])
)

# Start serial_api_bridge.py alongside Flask when running `python app.py`.
SERIAL_BRIDGE_ENABLED = env_bool("SERIAL_BRIDGE_ENABLED", True)
SERIAL_BRIDGE_SCRIPT = BASE_DIR / "serial_api_bridge.py"
serial_bridge_process: subprocess.Popen | None = None

COMMAND_TO_ARDUINO_CODE = {
    "PUMP_ON": "P1",
    "PUMP_OFF": "P0",
    "ALARM_ON": "B1",
    "ALARM_OFF": "B0",
    "VALVE_OPEN": "V1",
    "VALVE_CLOSE": "V0",
    # Backward-compatible command aliases from the earlier dashboard.
    "LED_ON": "P1",
    "LED_OFF": "P0",
    "BUZZER_ON": "B1",
    "BUZZER_OFF": "B0",
    "DOOR_OPEN": "V1",
    "DOOR_CLOSE": "V0",
    "AUTO_ON": "A1",
    "AUTO_OFF": "A0",
}

mqtt_client = None
mqtt_lock = threading.Lock()
mqtt_start_lock = threading.Lock()
mqtt_status = {
    "enabled": MQTT_ENABLED,
    "available": mqtt is not None,
    "connected": False,
    "host": MQTT_HOST,
    "port": MQTT_PORT,
    "client_id": MQTT_CLIENT_ID,
    "sensor_topic": MQTT_TOPIC_SENSOR,
    "data_topic": MQTT_TOPIC_DATA,
    "log_topic": MQTT_TOPIC_LOG,
    "control_topic": MQTT_TOPIC_CONTROL,
    "alert_topic": MQTT_TOPIC_ALERT,
    "subscribed_topics": list(MQTT_SUBSCRIBE_TOPICS),
    "last_message_at": None,
    "last_publish_at": None,
    "last_error": None,
}

DEFAULT_SYSTEM_STATE = {
    # door_status is reused as valve_status for the water-tank prototype.
    "door_status": "CLOSED",
    # led_status is reused as pump_status / green pump-status LED.
    "led_status": "OFF",
    # buzzer_status is reused as alarm_status / buzzer + red LED.
    "buzzer_status": "OFF",
    "auto_mode": "1",
    "connection": "Waiting for Arduino Serial/MQTT Data",
}


def get_db() -> sqlite3.Connection:
    """Open a SQLite connection with dict-like row access."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create SQLite tables without inserting sensor records automatically."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                label TEXT NOT NULL,
                temperature REAL NOT NULL,
                humidity INTEGER NOT NULL,
                light INTEGER NOT NULL,
                distance INTEGER NOT NULL,
                door_status TEXT NOT NULL,
                led_status TEXT NOT NULL,
                buzzer_status TEXT NOT NULL,
                auto_mode INTEGER NOT NULL,
                connection TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO system_state (key, value) VALUES (?, ?)",
            DEFAULT_SYSTEM_STATE.items(),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sensor_records_created_at ON sensor_records(created_at)"
        )

        # Remove the old demo-mode flag from earlier versions. Real sensor data
        # now enters only through /api/tank/sensor, /api/sensor-data, or MQTT topics.
        conn.execute("DELETE FROM system_state WHERE key = ?", ("_".join(("mo" + "ck", "mode")),))


def _now_string(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _time_label(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%H:%M:%S")


def get_system_state() -> Dict[str, object]:
    """Read current pump/alarm/valve/auto state from SQLite."""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM system_state").fetchall()

    state = {row["key"]: row["value"] for row in rows}
    for key, value in DEFAULT_SYSTEM_STATE.items():
        state.setdefault(key, value)

    return {
        "door_status": state["door_status"],
        "led_status": state["led_status"],
        "buzzer_status": state["buzzer_status"],
        "auto_mode": state["auto_mode"] == "1",
        "connection": state["connection"],
    }


def update_system_state(updates: Dict[str, object]) -> None:
    """Persist control state changes to SQLite."""
    rows: Iterable[tuple[str, str]] = []
    normalized_rows = []
    for key, value in updates.items():
        normalized_rows.append(
            (key, "1" if value is True else "0" if value is False else str(value))
        )
    rows = normalized_rows

    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO system_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            rows,
        )


def insert_sensor_record(record: Dict[str, object]) -> Dict[str, object]:
    """Insert a real sensor record into SQLite and return it with its database id."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sensor_records (
                created_at, label, temperature, humidity, light, distance,
                door_status, led_status, buzzer_status, auto_mode, connection
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["time"],
                record["label"],
                record["temperature"],
                record["humidity"],
                record["light"],
                record["distance"],
                record["door_status"],
                record["led_status"],
                record["buzzer_status"],
                1 if record["auto_mode"] else 0,
                record["connection"],
            ),
        )
        record["id"] = cursor.lastrowid
    return record


def row_to_public_record(row: sqlite3.Row | Dict[str, object]) -> Dict[str, object]:
    """Normalize SQLite rows to the JSON shape expected by the frontend.

    The original SQLite columns are kept for compatibility, but this water-tank
    dashboard also exposes semantic aliases: water_level, float_level,
    analog_value, distance_cm, valve_status, pump_status, and alarm_status.
    """
    created_at = row["created_at"] if "created_at" in row.keys() else row["time"]
    water_level = row["temperature"]
    float_level = row["humidity"]
    analog_value = row["light"]
    distance_cm = row["distance"]
    valve_status = row["door_status"]
    pump_status = row["led_status"]
    alarm_status = row["buzzer_status"]
    return {
        "time": created_at,
        # Backward-compatible keys consumed by existing chart/history code.
        "temperature": water_level,
        "humidity": float_level,
        "light": analog_value,
        "distance": distance_cm,
        "door_status": valve_status,
        "led_status": pump_status,
        "buzzer_status": alarm_status,
        # Water-tank semantic keys.
        "water_level": water_level,
        "float_level": float_level,
        "analog_value": analog_value,
        "distance_cm": distance_cm,
        "valve_status": valve_status,
        "pump_status": pump_status,
        "alarm_status": alarm_status,
        "auto_mode": bool(row["auto_mode"]),
        "connection": row["connection"],
        "last_updated": created_at,
    }


def latest_sensor_rows(limit: int = 20, descending: bool = True) -> list[sqlite3.Row]:
    """Read latest sensor rows from SQLite."""
    order = "DESC" if descending else "ASC"
    with get_db() as conn:
        return conn.execute(
            f"""
            SELECT * FROM sensor_records
            ORDER BY datetime(created_at) {order}, id {order}
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def paginated_sensor_rows(page: int = 1, per_page: int = 20) -> Dict[str, object]:
    """Read all sensor history with pagination metadata."""
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    offset = (page - 1) * per_page

    with get_db() as conn:
        total_records = conn.execute(
            "SELECT COUNT(*) AS count FROM sensor_records"
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT * FROM sensor_records
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()

    total_pages = max(1, (total_records + per_page - 1) // per_page)
    return {
        "records": [row_to_public_record(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total_records": total_records,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def latest_sensor_row() -> sqlite3.Row | None:
    """Read the newest sensor row without generating data."""
    rows = latest_sensor_rows(limit=1, descending=True)
    return rows[0] if rows else None


def current_public_record() -> Dict[str, object]:
    """Return the latest real sensor row, overlaid with current control state.

    If no Arduino/MQTT/API data has arrived yet, return a no-data object so the
    frontend can show placeholders instead of sensor values.
    """
    state = get_system_state()
    row = latest_sensor_row()
    if row is None:
        return {
            "time": None,
            "temperature": None,
            "humidity": None,
            "light": None,
            "distance": None,
            "door_status": state["door_status"],
            "led_status": state["led_status"],
            "buzzer_status": state["buzzer_status"],
            "water_level": None,
            "float_level": None,
            "analog_value": None,
            "distance_cm": None,
            "valve_status": state["door_status"],
            "pump_status": state["led_status"],
            "alarm_status": state["buzzer_status"],
            "auto_mode": bool(state["auto_mode"]),
            "connection": state["connection"],
            "last_updated": "Waiting for real sensor data",
            "has_data": False,
        }

    record = row_to_public_record(row)
    record.update(
        {
            "door_status": state["door_status"],
            "led_status": state["led_status"],
            "buzzer_status": state["buzzer_status"],
            "valve_status": state["door_status"],
            "pump_status": state["led_status"],
            "alarm_status": state["buzzer_status"],
            "auto_mode": bool(state["auto_mode"]),
            "connection": state["connection"],
            "has_data": True,
        }
    )
    return record


def clear_sensor_records(reset_state: bool = False) -> int:
    """Delete all stored sensor records.

    This is useful during demo/testing when you want to clear SQLite history
    before sending fresh data from Swagger, curl, or future Bluetooth/Serial code.
    System state is preserved by default so pump/alarm/valve settings do not
    unexpectedly change unless reset_state=true is sent in the request body.
    """
    with get_db() as conn:
        deleted_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sensor_records"
        ).fetchone()["count"]
        conn.execute("DELETE FROM sensor_records")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", ("sensor_records",))
        if reset_state:
            conn.execute("DELETE FROM system_state")
            conn.executemany(
                "INSERT INTO system_state (key, value) VALUES (?, ?)",
                DEFAULT_SYSTEM_STATE.items(),
            )
    return int(deleted_count)


def parse_bool(value: object) -> bool:
    """Parse booleans from JSON bools or common string forms."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes", "auto"}:
            return True
        if normalized in {"0", "false", "off", "no", "manual"}:
            return False
    raise ValueError("Expected boolean value")


def _number(
    payload: Dict[str, object], key: str, *, integer: bool = False
) -> int | float:
    if key not in payload:
        raise ValueError(f"Missing required field: {key}")
    try:
        value = float(payload[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field {key} must be numeric") from exc
    return int(round(value)) if integer else round(value, 1)


def _status(value: object, allowed: set[str], field_name: str) -> str:
    normalized = str(value).upper().strip()
    if normalized not in allowed:
        raise ValueError(
            f"Field {field_name} must be one of: {', '.join(sorted(allowed))}"
        )
    return normalized


def _first_present(payload: Dict[str, object], *keys: str) -> object | None:
    """Return the first non-empty payload value among common Arduino aliases."""
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _status_from_boolish(
    value: object,
    field_name: str,
    *,
    on_label: str = "ON",
    off_label: str = "OFF",
) -> str:
    normalized = str(value).upper().strip()
    on_aliases = {on_label, "1", "TRUE", "ON", "YES", "P1", "B1", "A1", "PUMP_ON", "ALARM_ON", "ALERT_ON", "BUZZER_ON"}
    off_aliases = {off_label, "0", "FALSE", "OFF", "NO", "P0", "B0", "A0", "PUMP_OFF", "ALARM_OFF", "ALERT_OFF", "BUZZER_OFF"}
    if normalized in on_aliases:
        return on_label
    if normalized in off_aliases:
        return off_label
    try:
        return on_label if parse_bool(value) else off_label
    except ValueError as exc:
        raise ValueError(f"Field {field_name} must be {on_label}/{off_label} or boolean-like") from exc


def _payload_float(value: object, field_name: str) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"Field {field_name} must be numeric") from exc
    raise ValueError(f"Field {field_name} must be numeric")


def _estimate_distance_cm(water_level: object) -> int:
    """Estimate tank distance when Arduino only sends a water percentage."""
    percent = max(0.0, min(100.0, _payload_float(water_level, "water_level")))
    empty_distance_cm = 30
    full_distance_cm = 5
    return round(empty_distance_cm - (percent / 100.0) * (empty_distance_cm - full_distance_cm))


def normalize_tank_sensor_payload(payload: Dict[str, object]) -> Dict[str, object]:
    """Accept compact Arduino-style payloads and map them to the dashboard schema.

    Supported Arduino aliases include:
    - water_percent / waterPercent / level -> water_level
    - water_raw / raw / analog -> analog_value
    - is_pumping / pump / pump_state -> pump_status
    - alert / alarm / alarm_on -> alarm_status
    DHT fields temperature/temperature_c and humidity/humidity_percent are kept as
    raw extras but are not used as the tank level when water_percent is present.
    """
    normalized = dict(payload)

    water_value = _first_present(
        normalized,
        "water_level",
        "water_percent",
        "waterPercent",
        "water",
        "level",
        "tank_level",
        "tankLevel",
    )
    if water_value is not None:
        normalized.setdefault("water_level", water_value)
        normalized.setdefault("float_level", water_value)
        raw_value = _first_present(
            normalized,
            "analog_value",
            "water_raw",
            "waterRaw",
            "raw",
            "analog",
            "analogValue",
        )
        if raw_value is not None:
            normalized.setdefault("analog_value", raw_value)
        else:
            normalized.setdefault("analog_value", round(_payload_float(water_value, "water_level") / 100.0 * 1023))
        normalized.setdefault("distance_cm", _estimate_distance_cm(water_value))

    distance_value = _first_present(normalized, "distance_cm", "distance", "distanceCm")
    if distance_value is not None:
        normalized.setdefault("distance_cm", distance_value)

    pump_value = _first_present(
        normalized,
        "pump_status",
        "is_pumping",
        "isPumping",
        "pump",
        "pump_on",
        "pumpOn",
        "pump_state",
        "pumpState",
    )
    if pump_value is not None:
        normalized["pump_status"] = _status_from_boolish(pump_value, "pump_status")
        normalized.setdefault(
            "valve_status", "OPEN" if normalized["pump_status"] == "ON" else "CLOSED"
        )

    alert_value = _first_present(
        normalized,
        "alarm_status",
        "alert_status",
        "alert",
        "alarm",
        "alarm_on",
        "alarmOn",
        "is_alerting",
        "isAlerting",
    )
    if alert_value is not None:
        normalized["alarm_status"] = _status_from_boolish(alert_value, "alarm_status")
    elif water_value is not None:
        normalized.setdefault("alarm_status", "ON" if _payload_float(water_value, "water_level") > 90 else "OFF")

    auto_value = _first_present(normalized, "auto_mode", "auto", "autoMode")
    if auto_value is not None:
        normalized["auto_mode"] = parse_bool(auto_value)

    if water_value is not None:
        normalized.setdefault("connection", "Arduino/API tank/sensor")

    return normalized


def alert_status_from_payload(payload: Dict[str, object]) -> str:
    """Parse tank/alert payloads from dashboard, MQTT, or Arduino bridge."""
    alert_value = _first_present(
        payload,
        "alarm_status",
        "alert_status",
        "alert",
        "alarm",
        "alarm_on",
        "alarmOn",
        "is_alerting",
        "isAlerting",
        "state",
        "command",
        "arduino_code",
        "code",
        "action",
    )
    if alert_value is not None:
        return _status_from_boolish(alert_value, "alarm_status")

    water_value = _first_present(payload, "water_level", "water_percent", "waterPercent")
    if water_value is not None:
        return "ON" if _payload_float(water_value, "water_level") > 90 else "OFF"

    raise ValueError(
        "Missing alert value: send alarm_status, alert, alarm, state, or water_level"
    )


def _label_from_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S")
    except ValueError:
        return value.split()[-1][:8] if value else _time_label()


def record_from_sensor_payload(
    payload: Dict[str, object],
) -> tuple[Dict[str, object], Dict[str, object]]:
    """Validate external water-tank sensor JSON and convert it to a record.

    Preferred water-tank fields: water_level, float_level, analog_value,
    distance_cm, valve_status, pump_status, alarm_status, auto_mode.
    Backward-compatible fields from the previous dashboard are also accepted.
    """
    payload = normalize_tank_sensor_payload(payload)
    state = get_system_state()
    created_at = str(
        payload.get("time")
        or payload.get("timestamp")
        or payload.get("created_at")
        or _now_string()
    )

    valve_status = _status(
        payload.get("valve_status", payload.get("door_status", state["door_status"])),
        {"OPEN", "CLOSED"},
        "valve_status",
    )
    pump_status = _status(
        payload.get("pump_status", payload.get("led_status", state["led_status"])),
        {"ON", "OFF"},
        "pump_status",
    )
    alarm_status = _status(
        payload.get("alarm_status", payload.get("buzzer_status", state["buzzer_status"])),
        {"ON", "OFF"},
        "alarm_status",
    )
    auto_mode = parse_bool(payload.get("auto_mode", state["auto_mode"]))
    connection = str(payload.get("connection", state["connection"])).strip() or str(
        state["connection"]
    )

    if "water_level" in payload:
        water_level = _number(payload, "water_level")
    else:
        water_level = _number(payload, "temperature")
    if "float_level" in payload:
        float_level = _number(payload, "float_level", integer=True)
    else:
        float_level = _number(payload, "humidity", integer=True)
    if "analog_value" in payload:
        analog_value = _number(payload, "analog_value", integer=True)
    else:
        analog_value = _number(payload, "light", integer=True)
    if "distance_cm" in payload:
        distance_cm = _number(payload, "distance_cm", integer=True)
    else:
        distance_cm = _number(payload, "distance", integer=True)

    record = {
        "time": created_at,
        "label": _label_from_time(created_at),
        "temperature": water_level,
        "humidity": float_level,
        "light": analog_value,
        "distance": distance_cm,
        "door_status": valve_status,
        "led_status": pump_status,
        "buzzer_status": alarm_status,
        "auto_mode": auto_mode,
        "connection": connection,
        "last_updated": created_at,
    }

    state_updates = {
        "door_status": valve_status,
        "led_status": pump_status,
        "buzzer_status": alarm_status,
        "auto_mode": auto_mode,
        "connection": connection,
    }
    return record, state_updates


def _set_mqtt_status(**updates: object) -> None:
    """Thread-safe MQTT status updates for the dashboard/API."""
    with mqtt_lock:
        mqtt_status.update(updates)


def get_mqtt_status() -> Dict[str, object]:
    """Return a public snapshot of MQTT connection/config state."""
    with mqtt_lock:
        return dict(mqtt_status)


def mqtt_reason_code_value(reason_code: object) -> int | object:
    """Normalize paho-mqtt v2 reason codes for simple comparisons/logging."""
    return getattr(reason_code, "value", reason_code)


def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    """Subscribe to sensor topics when connected to Mosquitto."""
    rc = mqtt_reason_code_value(reason_code)
    if rc == 0:
        for topic in MQTT_SUBSCRIBE_TOPICS:
            client.subscribe(topic)
        _set_mqtt_status(connected=True, last_error=None)
        logging.info(
            "MQTT connected to %s:%s; subscribed to %s",
            MQTT_HOST,
            MQTT_PORT,
            ", ".join(MQTT_SUBSCRIBE_TOPICS),
        )
        return

    _set_mqtt_status(connected=False, last_error=f"MQTT connect failed: {reason_code}")
    logging.warning("MQTT connect failed: %s", reason_code)


def on_mqtt_disconnect(
    client, userdata, disconnect_flags, reason_code, properties=None
):
    """Keep Flask alive when the broker is offline/restarting."""
    rc = mqtt_reason_code_value(reason_code)
    _set_mqtt_status(
        connected=False,
        last_error=None if rc == 0 else f"MQTT disconnected: {reason_code}",
    )
    if rc != 0:
        logging.warning("MQTT disconnected unexpectedly: %s", reason_code)


def on_mqtt_message(client, userdata, message):
    """Store sensor JSON received from MQTT into SQLite.

    Raspberry Pi/Bluetooth bridge should publish Arduino JSON to MQTT_TOPIC_DATA
    or MQTT_TOPIC_LOG. The dashboard reads from the same SQLite rows as before.
    """
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("MQTT payload must be a JSON object")
        if message.topic == MQTT_TOPIC_ALERT:
            alarm_status = alert_status_from_payload(payload)
            update_system_state(
                {"buzzer_status": alarm_status, "connection": "MQTT tank/alert"}
            )
            _set_mqtt_status(last_message_at=_now_string(), last_error=None)
            logging.info("MQTT alert state saved from topic %s", message.topic)
            return

        record, state_updates = record_from_sensor_payload(payload)
        update_system_state(state_updates)
        insert_sensor_record(record)
        _set_mqtt_status(last_message_at=_now_string(), last_error=None)
        logging.info("MQTT sensor data saved from topic %s", message.topic)
    except Exception as exc:  # Keep the MQTT loop alive on malformed messages.
        _set_mqtt_status(last_error=f"MQTT message error on {message.topic}: {exc}")
        logging.warning("MQTT message error on %s: %s", message.topic, exc)


def start_mqtt_client() -> None:
    """Start exactly one background MQTT client if enabled and installed."""
    global mqtt_client
    if not MQTT_ENABLED:
        _set_mqtt_status(
            connected=False, last_error="MQTT disabled by MQTT_ENABLED=false"
        )
        return
    if mqtt is None:
        _set_mqtt_status(
            connected=False, available=False, last_error="paho-mqtt is not installed"
        )
        return

    # Flask handles dashboard refresh requests in parallel. Without this lock,
    # several first requests can create MQTT clients with the same client_id,
    # causing Mosquitto to repeatedly disconnect/reconnect them.
    with mqtt_start_lock:
        if mqtt_client is not None:
            return

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
            if MQTT_USERNAME:
                client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            client.on_connect = on_mqtt_connect
            client.on_disconnect = on_mqtt_disconnect
            client.on_message = on_mqtt_message
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            client.connect_async(MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE)
            client.loop_start()
            mqtt_client = client
            _set_mqtt_status(available=True, last_error=None)
            logging.info("MQTT client starting: %s:%s", MQTT_HOST, MQTT_PORT)
        except Exception as exc:
            _set_mqtt_status(connected=False, last_error=f"MQTT startup error: {exc}")
            logging.warning("MQTT startup error: %s", exc)


def start_serial_bridge() -> None:
    """Launch serial_api_bridge.py as a child process for Arduino Serial I/O."""
    global serial_bridge_process
    if not SERIAL_BRIDGE_ENABLED:
        logging.info("Serial bridge disabled (SERIAL_BRIDGE_ENABLED=false)")
        return
    if not SERIAL_BRIDGE_SCRIPT.is_file():
        logging.warning("serial_api_bridge.py not found; skipping serial bridge")
        return
    if serial_bridge_process is not None and serial_bridge_process.poll() is None:
        return

    cmd = [
        sys.executable,
        str(SERIAL_BRIDGE_SCRIPT),
        "--no-api-post",
        "--mqtt-host",
        MQTT_HOST,
        "--mqtt-port",
        str(MQTT_PORT),
        "--mqtt-sensor-topic",
        MQTT_TOPIC_SENSOR,
        "--mqtt-control-topic",
        MQTT_TOPIC_CONTROL,
    ]
    serial_port = os.environ.get("SERIAL_PORT")
    if serial_port:
        cmd.extend(["--serial-port", serial_port])

    try:
        serial_bridge_process = subprocess.Popen(cmd, cwd=str(BASE_DIR))
        logging.info(
            "Serial bridge started (pid=%s, port=%s)",
            serial_bridge_process.pid,
            serial_port or "/dev/ttyS0",
        )
        atexit.register(stop_serial_bridge)
    except Exception as exc:
        logging.warning("Serial bridge startup error: %s", exc)


def stop_serial_bridge() -> None:
    """Terminate the background serial bridge process."""
    global serial_bridge_process
    if serial_bridge_process is None:
        return
    if serial_bridge_process.poll() is not None:
        serial_bridge_process = None
        return

    serial_bridge_process.terminate()
    try:
        serial_bridge_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        serial_bridge_process.kill()
        serial_bridge_process.wait(timeout=2)
    logging.info("Serial bridge stopped")
    serial_bridge_process = None


def mqtt_publish_json(topic: str, payload: Dict[str, object]) -> Dict[str, object]:
    """Publish JSON to MQTT and return metadata for the API response."""
    status = get_mqtt_status()
    if not status["enabled"]:
        return {"published": False, "topic": topic, "reason": "MQTT disabled"}
    if mqtt_client is None or not status["connected"]:
        return {
            "published": False,
            "topic": topic,
            "reason": status.get("last_error") or "MQTT broker not connected",
        }

    try:
        info = mqtt_client.publish(topic, json.dumps(payload), qos=1)
        # wait_for_publish gives the dashboard honest feedback for command sends.
        info.wait_for_publish(timeout=2)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            reason = mqtt.error_string(info.rc)
            _set_mqtt_status(last_error=f"MQTT publish failed: {reason}")
            return {"published": False, "topic": topic, "reason": reason}
        _set_mqtt_status(last_publish_at=_now_string(), last_error=None)
        return {"published": True, "topic": topic}
    except Exception as exc:
        _set_mqtt_status(last_error=f"MQTT publish error: {exc}")
        return {"published": False, "topic": topic, "reason": str(exc)}


def build_command_payload(command: str) -> Dict[str, object]:
    """Build the MQTT command JSON consumed by the Bluetooth-Arduino bridge."""
    return {
        "command": command,
        "arduino_code": COMMAND_TO_ARDUINO_CODE.get(command, command),
        "source": "flask-dashboard",
        "timestamp": _now_string(),
    }


def _bool_command(value: object, on_command: str, off_command: str) -> str:
    return on_command if _status_from_boolish(value, "state") == "ON" else off_command


def normalize_control_command(payload: Dict[str, object]) -> str:
    """Normalize tank/control payloads to the dashboard command vocabulary."""
    raw_command = _first_present(payload, "command", "arduino_code", "code", "action")
    device = str(_first_present(payload, "device", "target") or "pump").lower().strip()

    if raw_command is not None:
        normalized = str(raw_command).upper().strip()
        alias_map = {
            "1": "PUMP_ON",
            "P1": "PUMP_ON",
            "PUMP": "PUMP_ON",
            "PUMP_ON": "PUMP_ON",
            "LED_ON": "PUMP_ON",
            "0": "PUMP_OFF",
            "P0": "PUMP_OFF",
            "PUMP_OFF": "PUMP_OFF",
            "LED_OFF": "PUMP_OFF",
            "B1": "ALARM_ON",
            "ALARM_ON": "ALARM_ON",
            "ALERT_ON": "ALARM_ON",
            "BUZZER_ON": "ALARM_ON",
            "B0": "ALARM_OFF",
            "ALARM_OFF": "ALARM_OFF",
            "ALERT_OFF": "ALARM_OFF",
            "BUZZER_OFF": "ALARM_OFF",
            "V1": "VALVE_OPEN",
            "VALVE_OPEN": "VALVE_OPEN",
            "DOOR_OPEN": "VALVE_OPEN",
            "V0": "VALVE_CLOSE",
            "VALVE_CLOSE": "VALVE_CLOSE",
            "DOOR_CLOSE": "VALVE_CLOSE",
            "A1": "AUTO_ON",
            "AUTO_ON": "AUTO_ON",
            "A0": "AUTO_OFF",
            "AUTO_OFF": "AUTO_OFF",
        }
        if normalized in {"ON", "TRUE", "YES"}:
            if "alarm" in device or "alert" in device or "buzzer" in device:
                return "ALARM_ON"
            if "valve" in device or "door" in device:
                return "VALVE_OPEN"
            if "auto" in device:
                return "AUTO_ON"
            return "PUMP_ON"
        if normalized in {"OFF", "FALSE", "NO"}:
            if "alarm" in device or "alert" in device or "buzzer" in device:
                return "ALARM_OFF"
            if "valve" in device or "door" in device:
                return "VALVE_CLOSE"
            if "auto" in device:
                return "AUTO_OFF"
            return "PUMP_OFF"
        return alias_map.get(normalized, normalized)

    pump_value = _first_present(payload, "pump", "is_pumping", "isPumping", "pump_on", "pumpOn")
    if pump_value is not None:
        return _bool_command(pump_value, "PUMP_ON", "PUMP_OFF")

    alarm_value = _first_present(payload, "alarm", "alert", "alarm_on", "alarmOn", "is_alerting", "isAlerting")
    if alarm_value is not None:
        return _bool_command(alarm_value, "ALARM_ON", "ALARM_OFF")

    valve_value = _first_present(payload, "valve", "valve_open", "valveOpen", "door", "door_open", "doorOpen")
    if valve_value is not None:
        normalized = str(valve_value).upper().strip()
        if normalized in {"OPEN", "V1"}:
            return "VALVE_OPEN"
        if normalized in {"CLOSED", "CLOSE", "V0"}:
            return "VALVE_CLOSE"
        return _bool_command(valve_value, "VALVE_OPEN", "VALVE_CLOSE")

    auto_value = _first_present(payload, "auto", "auto_mode", "autoMode")
    if auto_value is not None:
        return _bool_command(auto_value, "AUTO_ON", "AUTO_OFF")

    state_value = _first_present(payload, "state", "value")
    if state_value is not None:
        return normalize_control_command({"command": state_value, "device": device})

    return ""


def error_response(message: str, status_code: int = 400):
    return jsonify({"success": False, "message": message}), status_code


def openapi_spec() -> Dict[str, object]:
    """OpenAPI schema used by Swagger UI.

    Keep this in sync when adding real SQLite/Bluetooth/Serial APIs later.
    """
    sensor_record_schema = {
        "type": "object",
        "properties": {
            "time": {"type": "string", "example": "2026-06-07 10:30:00"},
            "water_level": {"type": "number", "format": "float", "example": 72.5},
            "float_level": {"type": "integer", "example": 73},
            "analog_value": {"type": "integer", "example": 746},
            "distance_cm": {"type": "integer", "example": 16},
            "valve_status": {
                "type": "string",
                "enum": ["OPEN", "CLOSED"],
                "example": "OPEN",
            },
            "pump_status": {"type": "string", "enum": ["ON", "OFF"], "example": "ON"},
            "alarm_status": {
                "type": "string",
                "enum": ["ON", "OFF"],
                "example": "OFF",
            },
            "auto_mode": {"type": "boolean", "example": True},
            "connection": {"type": "string", "example": "Bluetooth Connected"},
            "last_updated": {"type": "string", "example": "2026-06-07 10:30:00"},
        },
        "required": [
            "time",
            "water_level",
            "float_level",
            "analog_value",
            "distance_cm",
            "valve_status",
            "pump_status",
            "alarm_status",
            "auto_mode",
            "connection",
            "last_updated",
        ],
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Smart Water Tank Monitoring & Control API",
            "description": "SQLite-backed API for the IoT dashboard UI. Sensor values come from Arduino/Raspberry Pi bridge, MQTT, Swagger, or curl via /api/tank/sensor.",
            "version": "1.0.0",
        },
        "servers": [{"url": "/", "description": "Current Flask server"}],
        "tags": [
            {"name": "Dashboard", "description": "Dashboard pages and documentation"},
            {
                "name": "Sensors",
                "description": "Current tank sensor data, history, chart data, and external sensor ingestion",
            },
            {
                "name": "Controls",
                "description": "Pump, alarm, valve, and auto-mode control commands",
            },
            {
                "name": "MQTT",
                "description": "Mosquitto broker connection and topic status",
            },
        ],
        "components": {
            "schemas": {
                "SensorRecord": sensor_record_schema,
                "ChartData": {
                    "type": "object",
                    "properties": {
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "water_level": {"type": "array", "items": {"type": "number"}},
                        "float_level": {"type": "array", "items": {"type": "integer"}},
                        "analog_value": {"type": "array", "items": {"type": "integer"}},
                        "distance_cm": {"type": "array", "items": {"type": "integer"}},
                    },
                },
                "HistoryResponse": {
                    "type": "object",
                    "properties": {
                        "records": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SensorRecord"},
                        },
                        "page": {"type": "integer", "example": 1},
                        "per_page": {"type": "integer", "example": 20},
                        "total_records": {"type": "integer", "example": 128},
                        "total_pages": {"type": "integer", "example": 7},
                        "has_prev": {"type": "boolean", "example": False},
                        "has_next": {"type": "boolean", "example": True},
                    },
                },
                "ControlRequest": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": [
                                "PUMP_ON",
                                "PUMP_OFF",
                                "P1",
                                "P0",
                                "1",
                                "0",
                                "ALARM_ON",
                                "ALARM_OFF",
                                "VALVE_OPEN",
                                "VALVE_CLOSE",
                                "AUTO_ON",
                                "AUTO_OFF",
                            ],
                            "example": "PUMP_ON",
                        }
                    },
                    "required": ["command"],
                },
                "AutoRequest": {
                    "type": "object",
                    "properties": {"auto": {"type": "boolean", "example": True}},
                    "required": ["auto"],
                },
                "MqttStatus": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "example": True},
                        "available": {"type": "boolean", "example": True},
                        "connected": {"type": "boolean", "example": True},
                        "host": {"type": "string", "example": "127.0.0.1"},
                        "port": {"type": "integer", "example": 1883},
                        "client_id": {
                            "type": "string",
                            "example": "smart-water-tank-flask-dashboard",
                        },
                        "sensor_topic": {"type": "string", "example": "tank/sensor"},
                        "data_topic": {"type": "string", "example": "tank/sensor"},
                        "log_topic": {"type": "string", "example": "tank/data/log"},
                        "control_topic": {"type": "string", "example": "tank/control"},
                        "alert_topic": {"type": "string", "example": "tank/alert"},
                        "last_message_at": {"type": "string", "nullable": True},
                        "last_publish_at": {"type": "string", "nullable": True},
                        "last_error": {"type": "string", "nullable": True},
                    },
                },
                "MqttPublishResult": {
                    "type": "object",
                    "properties": {
                        "published": {"type": "boolean", "example": True},
                        "topic": {"type": "string", "example": "tank/control"},
                        "reason": {
                            "type": "string",
                            "example": "MQTT broker not connected",
                        },
                    },
                },
                "ClearDataRequest": {
                    "type": "object",
                    "properties": {
                        "reset_state": {
                            "type": "boolean",
                            "example": False,
                            "description": "Optional. If true, also resets pump/alarm/valve/auto state to defaults.",
                        }
                    },
                },
                "ClearDataResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": True},
                        "message": {
                            "type": "string",
                            "example": "All sensor records cleared",
                        },
                        "deleted_records": {"type": "integer", "example": 20},
                        "reset_state": {"type": "boolean", "example": False},
                    },
                },
                "SensorDataRequest": {
                    "type": "object",
                    "properties": {
                        "water_level": {
                            "type": "number",
                            "format": "float",
                            "example": 72.5,
                        },
                        "water_percent": {
                            "type": "number",
                            "format": "float",
                            "example": 72,
                            "description": "Arduino alias accepted by /api/tank/sensor",
                        },
                        "float_level": {"type": "integer", "example": 73},
                        "analog_value": {"type": "integer", "example": 746},
                        "water_raw": {
                            "type": "integer",
                            "example": 515,
                            "description": "Arduino A0 raw value alias for analog_value",
                        },
                        "distance_cm": {"type": "integer", "example": 16},
                        "valve_status": {
                            "type": "string",
                            "enum": ["OPEN", "CLOSED"],
                            "example": "OPEN",
                        },
                        "pump_status": {
                            "type": "string",
                            "enum": ["ON", "OFF"],
                            "example": "ON",
                        },
                        "is_pumping": {
                            "type": "integer",
                            "enum": [0, 1],
                            "example": 1,
                            "description": "Arduino alias accepted by /api/tank/sensor",
                        },
                        "alarm_status": {
                            "type": "string",
                            "enum": ["ON", "OFF"],
                            "example": "OFF",
                        },
                        "auto_mode": {"type": "boolean", "example": False},
                        "connection": {
                            "type": "string",
                            "example": "MQTT/Bluetooth Gateway Connected",
                        },
                        "timestamp": {
                            "type": "string",
                            "example": "2026-06-07 10:30:00",
                        },
                    },
                    "description": "Send either full dashboard fields (water_level/float_level/analog_value/distance_cm) or Arduino aliases (water_percent/is_pumping/water_raw).",
                },
                "CommandResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": True},
                        "message": {
                            "type": "string",
                            "example": "Command sent: PUMP_ON",
                        },
                        "mqtt": {"$ref": "#/components/schemas/MqttPublishResult"},
                        "data": {"$ref": "#/components/schemas/SensorRecord"},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": False},
                        "message": {
                            "type": "string",
                            "example": "Unknown command: INVALID",
                        },
                    },
                },
            }
        },
        "paths": {
            "/": {
                "get": {
                    "tags": ["Dashboard"],
                    "summary": "Render dashboard UI",
                    "responses": {"200": {"description": "HTML dashboard page"}},
                }
            },
            "/docs": {
                "get": {
                    "tags": ["Dashboard"],
                    "summary": "Render Swagger UI",
                    "responses": {"200": {"description": "Swagger UI page"}},
                }
            },
            "/api/openapi.json": {
                "get": {
                    "tags": ["Dashboard"],
                    "summary": "Return OpenAPI JSON schema",
                    "responses": {"200": {"description": "OpenAPI specification"}},
                }
            },
            "/api/mqtt/status": {
                "get": {
                    "tags": ["MQTT"],
                    "summary": "Get MQTT broker connection and topic status",
                    "description": "Flask subscribes to tank/sensor and tank/alert, and publishes dashboard commands to tank/control. Legacy tank/data and tank/data/log can still be subscribed through env aliases.",
                    "responses": {
                        "200": {
                            "description": "MQTT status",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/MqttStatus"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/current": {
                "get": {
                    "tags": ["Sensors"],
                    "summary": "Get current sensor data",
                    "description": "Reads the latest SQLite row inserted by /api/tank/sensor, /api/sensor-data, or MQTT. No samples are generated automatically.",
                    "responses": {
                        "200": {
                            "description": "Current sensor record",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/SensorRecord"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/sensor-data": {
                "post": {
                    "tags": ["Sensors"],
                    "summary": "Store sensor data from Raspberry Pi, Arduino bridge, or manual tests",
                    "description": "Accepts a sensor JSON payload and writes it to SQLite. Preferred new endpoint: /api/tank/sensor. This legacy endpoint remains supported.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SensorDataRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sensor data saved",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CommandResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid sensor payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/tank/sensor": {
                "post": {
                    "tags": ["Sensors"],
                    "summary": "Store Arduino tank sensor data",
                    "description": "Primary endpoint for Arduino/Raspberry Pi bridge data. Accepts full dashboard JSON or compact Arduino aliases: water_percent, is_pumping, water_raw.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SensorDataRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sensor data saved",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CommandResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid sensor payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/clear-data": {
                "post": {
                    "tags": ["Sensors"],
                    "summary": "Clear all stored sensor records",
                    "description": "Deletes every row in sensor_records and resets the AUTOINCREMENT counter. Send reset_state=true to also restore system_state defaults.",
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ClearDataRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sensor data cleared",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ClearDataResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid clear data payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/clear-all-data": {
                "post": {
                    "tags": ["Sensors"],
                    "summary": "Alias for /api/clear-data",
                    "description": "Same behavior as POST /api/clear-data.",
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ClearDataRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sensor data cleared",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ClearDataResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid clear data payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/history": {
                "get": {
                    "tags": ["Sensors"],
                    "summary": "Get paginated sensor history",
                    "description": "Returns all SQLite sensor records through page/per_page pagination so the dashboard can browse the full history, not only the latest 20.",
                    "parameters": [
                        {
                            "name": "page",
                            "in": "query",
                            "schema": {"type": "integer", "default": 1, "minimum": 1},
                        },
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 100,
                            },
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Paginated sensor records from SQLite",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/HistoryResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid pagination query",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/chart-data": {
                "get": {
                    "tags": ["Sensors"],
                    "summary": "Get chart data for latest 20 records",
                    "responses": {
                        "200": {
                            "description": "Chart.js time-series data",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ChartData"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/control": {
                "post": {
                    "tags": ["Controls"],
                    "summary": "Send pump, alarm, valve, or auto command",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ControlRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Command accepted and persisted",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CommandResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Unknown command",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/tank/control": {
                "post": {
                    "tags": ["Controls"],
                    "summary": "Primary tank control endpoint",
                    "description": "Accepts command values like PUMP_ON/PUMP_OFF, P1/P0, or compact payloads like {\"pump\":1}. Publishes to MQTT topic tank/control.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ControlRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Command accepted and persisted",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CommandResponse"}
                                }
                            },
                        },
                        "400": {
                            "description": "Unknown command",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/api/tank/alert": {
                "post": {
                    "tags": ["Controls"],
                    "summary": "Set or report tank alert state",
                    "description": "Accepts alarm_status/alert/alarm/state/water_level and updates the dashboard alarm. Publishes alert JSON to MQTT topic tank/alert.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "alarm_status": {"type": "string", "enum": ["ON", "OFF"], "example": "ON"},
                                        "alert": {"type": "boolean", "example": True},
                                        "water_level": {"type": "number", "example": 94},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Alert state accepted and persisted",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CommandResponse"}
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid alert payload",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/api/auto": {
                "post": {
                    "tags": ["Controls"],
                    "summary": "Enable or disable auto mode",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AutoRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Auto mode state persisted",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CommandResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }


@app.before_request
def ensure_mqtt_started():
    """Lazy-start MQTT once the Flask process is handling real requests."""
    start_mqtt_client()


@app.route("/")
def index():
    """Render the dashboard page."""
    return render_template(
        "index.html",
        refresh_interval_ms=REFRESH_INTERVAL_MS,
        refresh_interval_seconds=UPDATE_INTERVAL_SECONDS,
        chart_max_points=CHART_MAX_POINTS,
        history_default_per_page=HISTORY_DEFAULT_PER_PAGE,
    )


@app.get("/docs")
def swagger_ui():
    """Render Swagger UI for the dashboard API."""
    return render_template("swagger.html")


@app.get("/api/openapi.json")
def api_openapi_json():
    """Return OpenAPI JSON consumed by Swagger UI."""
    return jsonify(openapi_spec())


@app.get("/api/mqtt/status")
def api_mqtt_status():
    """Return MQTT connection state, broker config, and topic names."""
    return jsonify(get_mqtt_status())


@app.get("/api/current")
def api_current():
    """Return latest real sensor data without generating samples."""
    return jsonify(current_public_record())


@app.post("/api/sensor-data")
@app.post("/api/tank/sensor")
@app.post("/tank/sensor")
def api_sensor_data():
    """Accept sensor JSON from Arduino bridge, MQTT tests, Swagger, or curl."""
    payload = request.get_json(silent=True) or {}
    try:
        record, state_updates = record_from_sensor_payload(payload)
    except ValueError as exc:
        return error_response(str(exc))

    update_system_state(state_updates)
    stored_record = insert_sensor_record(record)
    return jsonify(
        {
            "success": True,
            "message": "Sensor data saved",
            "data": row_to_public_record(stored_record),
        }
    )


@app.post("/api/clear-data")
@app.post("/api/clear-all-data")
def api_clear_data():
    """Clear all sensor rows from SQLite.

    Optional JSON body:
    {"reset_state": true}

    By default only sensor history/chart data is cleared. Control state is kept
    so the dashboard does not unexpectedly switch modes during testing.
    """
    payload = request.get_json(silent=True) or {}
    raw_reset_state = payload.get("reset_state", False)
    try:
        reset_state = parse_bool(raw_reset_state)
    except ValueError as exc:
        return error_response(f"Field reset_state: {exc}")

    deleted_count = clear_sensor_records(reset_state=reset_state)
    return jsonify(
        {
            "success": True,
            "message": (
                "All sensor records cleared"
                if not reset_state
                else "All sensor records cleared and system state reset"
            ),
            "deleted_records": deleted_count,
            "reset_state": reset_state,
        }
    )


@app.get("/api/history")
def api_history():
    """Return paginated sensor history from SQLite.

    Query params:
    - page: 1-based page number
    - per_page: records per page, clamped to 1..100
    """
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", HISTORY_DEFAULT_PER_PAGE))
    except ValueError:
        return error_response("Query params page and per_page must be integers")

    return jsonify(paginated_sensor_rows(page=page, per_page=per_page))


@app.get("/api/chart-data")
def api_chart_data():
    """Return latest configured SQLite time-series points for Chart.js."""
    rows = list(reversed(latest_sensor_rows(limit=CHART_MAX_POINTS, descending=True)))
    return jsonify(
        {
            "labels": [row["label"] for row in rows],
            # Backward-compatible chart keys.
            "temperature": [row["temperature"] for row in rows],
            "humidity": [row["humidity"] for row in rows],
            "light": [row["light"] for row in rows],
            "distance": [row["distance"] for row in rows],
            # Water-tank semantic chart keys.
            "water_level": [row["temperature"] for row in rows],
            "float_level": [row["humidity"] for row in rows],
            "analog_value": [row["light"] for row in rows],
            "distance_cm": [row["distance"] for row in rows],
        }
    )


@app.post("/api/control")
@app.post("/api/tank/control")
@app.post("/tank/control")
def api_control():
    """Persist a device command and publish it to the Arduino gateway via MQTT."""
    payload = request.get_json(silent=True) or {}
    command = normalize_control_command(payload)

    command_map = {
        "PUMP_ON": ("led_status", "ON"),
        "PUMP_OFF": ("led_status", "OFF"),
        "ALARM_ON": ("buzzer_status", "ON"),
        "ALARM_OFF": ("buzzer_status", "OFF"),
        "VALVE_OPEN": ("door_status", "OPEN"),
        "VALVE_CLOSE": ("door_status", "CLOSED"),
        "AUTO_ON": ("auto_mode", True),
        "AUTO_OFF": ("auto_mode", False),
        # Backward-compatible aliases.
        "LED_ON": ("led_status", "ON"),
        "LED_OFF": ("led_status", "OFF"),
        "BUZZER_ON": ("buzzer_status", "ON"),
        "BUZZER_OFF": ("buzzer_status", "OFF"),
        "DOOR_OPEN": ("door_status", "OPEN"),
        "DOOR_CLOSE": ("door_status", "CLOSED"),
    }

    if command not in command_map:
        return (
            jsonify({"success": False, "message": f"Unknown command: {command}"}),
            400,
        )

    key, value = command_map[command]
    update_system_state({key: value})
    mqtt_result = mqtt_publish_json(MQTT_TOPIC_CONTROL, build_command_payload(command))
    return jsonify(
        {
            "success": True,
            "message": f"Command sent: {command}",
            "mqtt": mqtt_result,
            "data": current_public_record(),
        }
    )


@app.post("/api/tank/alert")
@app.post("/tank/alert")
def api_tank_alert():
    """Set or report tank alarm state through the tank/alert API."""
    payload = request.get_json(silent=True) or {}
    try:
        alarm_status = alert_status_from_payload(payload)
    except ValueError as exc:
        return error_response(str(exc))

    update_system_state({"buzzer_status": alarm_status})
    alert_payload = {
        "alert": alarm_status == "ON",
        "alarm_status": alarm_status,
        "source": "flask-api",
        "timestamp": _now_string(),
    }
    mqtt_result = mqtt_publish_json(MQTT_TOPIC_ALERT, alert_payload)
    return jsonify(
        {
            "success": True,
            "message": f"Alert {'enabled' if alarm_status == 'ON' else 'disabled'}",
            "mqtt": mqtt_result,
            "data": current_public_record(),
        }
    )


@app.post("/api/auto")
def api_auto():
    """Persist auto/manual mode in SQLite."""
    payload = request.get_json(silent=True) or {}
    auto = bool(payload.get("auto", False))
    update_system_state({"auto_mode": auto})
    command = "AUTO_ON" if auto else "AUTO_OFF"
    mqtt_result = mqtt_publish_json(MQTT_TOPIC_CONTROL, build_command_payload(command))
    message = "Auto mode enabled" if auto else "Auto mode disabled"
    return jsonify(
        {
            "success": True,
            "message": message,
            "mqtt": mqtt_result,
            "data": current_public_record(),
        }
    )


# Ensure DB exists for both `python app.py` and WSGI/test imports.
init_db()


if __name__ == "__main__":
    # host="0.0.0.0" lets the dashboard be reached from another device on the
    # Raspberry Pi network: http://RASPBERRY_PI_IP:5001
    # Default port is 5001. If another process already uses port 5001, run:
    # APP_PORT=5002 python app.py
    start_serial_bridge()
    try:
        app.run(
            host=APP_HOST,
            port=APP_PORT,
            debug=APP_DEBUG,
            threaded=True,
            use_reloader=False,
        )
    finally:
        stop_serial_bridge()

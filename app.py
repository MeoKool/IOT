from __future__ import annotations

import os
import json
import logging
import random
import sqlite3
import threading
from datetime import datetime, timedelta
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
    os.environ.get("DATABASE_PATH"), BASE_DIR / "smart_room.db"
)

# -----------------------------------------------------------------------------
# SQLITE + MOCK DATA BACKEND
# -----------------------------------------------------------------------------
# The dashboard now stores sensor history and control state in SQLite.
# For this UI phase, sensor values are still generated as mock data.
# Later, replace generate_sensor_record() with real Arduino JSON data received
# from Bluetooth (/dev/rfcomm0) or Serial backup. The frontend can stay the same.
# -----------------------------------------------------------------------------

# MQTT integration for the Raspberry Pi gateway layer.
# Defaults match the final architecture: Mosquitto runs on the same Pi as Flask.
MQTT_ENABLED = env_bool("MQTT_ENABLED", True)
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = env_int("MQTT_PORT", 1883)
MQTT_KEEPALIVE = env_int("MQTT_KEEPALIVE", 60)
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "smart-room-flask-dashboard")
MQTT_TOPIC_DATA = os.environ.get("MQTT_TOPIC_DATA", "room/data")
MQTT_TOPIC_LOG = os.environ.get("MQTT_TOPIC_LOG", "room/data/log")
MQTT_TOPIC_CONTROL = os.environ.get("MQTT_TOPIC_CONTROL", "room/control")
MQTT_SUBSCRIBE_TOPICS = tuple(dict.fromkeys([MQTT_TOPIC_DATA, MQTT_TOPIC_LOG]))

COMMAND_TO_ARDUINO_CODE = {
    "LED_ON": "L1",
    "LED_OFF": "L0",
    "BUZZER_ON": "B1",
    "BUZZER_OFF": "B0",
    "DOOR_OPEN": "D1",
    "DOOR_CLOSE": "D0",
    "AUTO_ON": "A1",
    "AUTO_OFF": "A0",
}

mqtt_client = None
mqtt_lock = threading.Lock()
mqtt_status = {
    "enabled": MQTT_ENABLED,
    "available": mqtt is not None,
    "connected": False,
    "host": MQTT_HOST,
    "port": MQTT_PORT,
    "client_id": MQTT_CLIENT_ID,
    "data_topic": MQTT_TOPIC_DATA,
    "log_topic": MQTT_TOPIC_LOG,
    "control_topic": MQTT_TOPIC_CONTROL,
    "subscribed_topics": list(MQTT_SUBSCRIBE_TOPICS),
    "last_message_at": None,
    "last_publish_at": None,
    "last_error": None,
}

DEFAULT_SYSTEM_STATE = {
    "door_status": "OPEN",
    "led_status": "ON",
    "buzzer_status": "OFF",
    "auto_mode": "1",
    "connection": "Bluetooth Connected",
    # MOCK_MODE=true means /api/current generates a fresh demo sample.
    # MOCK_MODE=false means /api/current only reads the latest SQLite row.
    "mock_mode": "1" if env_bool("MOCK_MODE", True) else "0",
}


def get_db() -> sqlite3.Connection:
    """Open a SQLite connection with dict-like row access."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create SQLite tables and seed startup mock records if needed."""
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

        record_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sensor_records"
        ).fetchone()["count"]

    if record_count < CHART_MAX_POINTS:
        seed_history(needed=CHART_MAX_POINTS - record_count)


def _now_string(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _time_label(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%H:%M:%S")


def get_system_state() -> Dict[str, object]:
    """Read current LED/buzzer/door/auto state from SQLite."""
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
        "mock_mode": state["mock_mode"] == "1",
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


def generate_sensor_record(dt: datetime | None = None) -> Dict[str, object]:
    """Generate one mock sensor record using current SQLite system state.

    Later replacement point:
    - Parse real Arduino JSON from Bluetooth/Serial.
    - Store that parsed payload with insert_sensor_record().
    """
    dt = dt or datetime.now()
    state = get_system_state()

    temperature = round(random.uniform(24.5, 31.5), 1)
    humidity = random.randint(54, 78)
    light = random.randint(180, 760)
    distance = random.randint(8, 55)

    return {
        "time": _now_string(dt),
        "label": _time_label(dt),
        "temperature": temperature,
        "humidity": humidity,
        "light": light,
        "distance": distance,
        "door_status": state["door_status"],
        "led_status": state["led_status"],
        "buzzer_status": state["buzzer_status"],
        "auto_mode": bool(state["auto_mode"]),
        "connection": state["connection"],
        "last_updated": _now_string(dt),
    }


def insert_sensor_record(record: Dict[str, object]) -> Dict[str, object]:
    """Insert a sensor record into SQLite and return it with its database id."""
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


def seed_history(needed: int = 20) -> None:
    """Seed startup data in SQLite so charts/history are not empty."""
    base = datetime.now() - timedelta(seconds=max(needed - 1, 0) * 3)
    for i in range(needed):
        insert_sensor_record(generate_sensor_record(base + timedelta(seconds=i * 3)))


def append_current_record() -> Dict[str, object]:
    """Generate and persist the newest mock sample."""
    return insert_sensor_record(generate_sensor_record())


def row_to_public_record(row: sqlite3.Row | Dict[str, object]) -> Dict[str, object]:
    """Normalize SQLite rows to the JSON shape expected by the frontend."""
    return {
        "time": row["created_at"] if "created_at" in row.keys() else row["time"],
        "temperature": row["temperature"],
        "humidity": row["humidity"],
        "light": row["light"],
        "distance": row["distance"],
        "door_status": row["door_status"],
        "led_status": row["led_status"],
        "buzzer_status": row["buzzer_status"],
        "auto_mode": bool(row["auto_mode"]),
        "connection": row["connection"],
        "last_updated": (
            row["created_at"] if "created_at" in row.keys() else row["last_updated"]
        ),
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
    """Read the newest sensor row without generating mock data."""
    rows = latest_sensor_rows(limit=1, descending=True)
    return rows[0] if rows else None


def clear_sensor_records(reset_state: bool = False) -> int:
    """Delete all stored sensor records.

    This is useful during demo/testing when you want to clear SQLite history
    before sending fresh data from Swagger, curl, or future Bluetooth/Serial code.
    System state is preserved by default so LED/buzzer/door/mock-mode settings do
    not unexpectedly change unless reset_state=true is sent in the request body.
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


def _label_from_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S")
    except ValueError:
        return value.split()[-1][:8] if value else _time_label()


def record_from_sensor_payload(
    payload: Dict[str, object],
) -> tuple[Dict[str, object], Dict[str, object]]:
    """Validate external sensor JSON and convert it to a SQLite record.

    This is the endpoint-ready shape for Raspberry Pi/Bluetooth ingestion.
    Required fields: temperature, humidity, light, distance.
    Optional fields: door_status, led_status, buzzer_status, auto_mode,
    connection, time/timestamp/created_at.
    """
    state = get_system_state()
    created_at = str(
        payload.get("time")
        or payload.get("timestamp")
        or payload.get("created_at")
        or _now_string()
    )

    door_status = _status(
        payload.get("door_status", state["door_status"]),
        {"OPEN", "CLOSED"},
        "door_status",
    )
    led_status = _status(
        payload.get("led_status", state["led_status"]), {"ON", "OFF"}, "led_status"
    )
    buzzer_status = _status(
        payload.get("buzzer_status", state["buzzer_status"]),
        {"ON", "OFF"},
        "buzzer_status",
    )
    auto_mode = parse_bool(payload.get("auto_mode", state["auto_mode"]))
    connection = str(payload.get("connection", state["connection"])).strip() or str(
        state["connection"]
    )

    record = {
        "time": created_at,
        "label": _label_from_time(created_at),
        "temperature": _number(payload, "temperature"),
        "humidity": _number(payload, "humidity", integer=True),
        "light": _number(payload, "light", integer=True),
        "distance": _number(payload, "distance", integer=True),
        "door_status": door_status,
        "led_status": led_status,
        "buzzer_status": buzzer_status,
        "auto_mode": auto_mode,
        "connection": connection,
        "last_updated": created_at,
    }

    state_updates = {
        "door_status": door_status,
        "led_status": led_status,
        "buzzer_status": buzzer_status,
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
            raise ValueError("MQTT sensor payload must be a JSON object")
        record, state_updates = record_from_sensor_payload(payload)
        # Real MQTT input means the dashboard should stop generating demo samples.
        state_updates["mock_mode"] = False
        update_system_state(state_updates)
        insert_sensor_record(record)
        _set_mqtt_status(last_message_at=_now_string(), last_error=None)
        logging.info("MQTT sensor data saved from topic %s", message.topic)
    except Exception as exc:  # Keep the MQTT loop alive on malformed messages.
        _set_mqtt_status(last_error=f"MQTT message error on {message.topic}: {exc}")
        logging.warning("MQTT message error on %s: %s", message.topic, exc)


def start_mqtt_client() -> None:
    """Start a background MQTT client if enabled and paho-mqtt is installed."""
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
            "temperature": {"type": "number", "format": "float", "example": 28.5},
            "humidity": {"type": "integer", "example": 70},
            "light": {"type": "integer", "example": 420},
            "distance": {"type": "integer", "example": 18},
            "door_status": {
                "type": "string",
                "enum": ["OPEN", "CLOSED"],
                "example": "OPEN",
            },
            "led_status": {"type": "string", "enum": ["ON", "OFF"], "example": "ON"},
            "buzzer_status": {
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
            "temperature",
            "humidity",
            "light",
            "distance",
            "door_status",
            "led_status",
            "buzzer_status",
            "auto_mode",
            "connection",
            "last_updated",
        ],
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Smart Room Monitoring & Control API",
            "description": "SQLite-backed mock API for the IoT dashboard UI. Sensor values are mock data for now; control states are persisted in SQLite.",
            "version": "1.0.0",
        },
        "servers": [{"url": "/", "description": "Current Flask server"}],
        "tags": [
            {"name": "Dashboard", "description": "Dashboard pages and documentation"},
            {
                "name": "Sensors",
                "description": "Current sensor data, history, chart data, and external sensor ingestion",
            },
            {
                "name": "Controls",
                "description": "Device and auto-mode control commands",
            },
            {
                "name": "MQTT",
                "description": "Mosquitto broker connection and topic status",
            },
            {
                "name": "Config",
                "description": "Runtime dashboard settings such as mock mode",
            },
        ],
        "components": {
            "schemas": {
                "SensorRecord": sensor_record_schema,
                "ChartData": {
                    "type": "object",
                    "properties": {
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "temperature": {"type": "array", "items": {"type": "number"}},
                        "humidity": {"type": "array", "items": {"type": "integer"}},
                        "light": {"type": "array", "items": {"type": "integer"}},
                        "distance": {"type": "array", "items": {"type": "integer"}},
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
                                "LED_ON",
                                "LED_OFF",
                                "BUZZER_ON",
                                "BUZZER_OFF",
                                "DOOR_OPEN",
                                "DOOR_CLOSE",
                            ],
                            "example": "LED_ON",
                        }
                    },
                    "required": ["command"],
                },
                "AutoRequest": {
                    "type": "object",
                    "properties": {"auto": {"type": "boolean", "example": True}},
                    "required": ["auto"],
                },
                "MockModeRequest": {
                    "type": "object",
                    "properties": {"mock_mode": {"type": "boolean", "example": False}},
                    "required": ["mock_mode"],
                },
                "MockModeResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": True},
                        "mock_mode": {"type": "boolean", "example": False},
                        "message": {"type": "string", "example": "Mock mode disabled"},
                    },
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
                            "example": "smart-room-flask-dashboard",
                        },
                        "data_topic": {"type": "string", "example": "room/data"},
                        "log_topic": {"type": "string", "example": "room/data/log"},
                        "control_topic": {"type": "string", "example": "room/control"},
                        "last_message_at": {"type": "string", "nullable": True},
                        "last_publish_at": {"type": "string", "nullable": True},
                        "last_error": {"type": "string", "nullable": True},
                    },
                },
                "MqttPublishResult": {
                    "type": "object",
                    "properties": {
                        "published": {"type": "boolean", "example": True},
                        "topic": {"type": "string", "example": "room/control"},
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
                            "description": "Optional. If true, also resets LED/buzzer/door/auto/mock-mode state to defaults.",
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
                        "temperature": {
                            "type": "number",
                            "format": "float",
                            "example": 28.5,
                        },
                        "humidity": {"type": "integer", "example": 70},
                        "light": {"type": "integer", "example": 420},
                        "distance": {"type": "integer", "example": 18},
                        "door_status": {
                            "type": "string",
                            "enum": ["OPEN", "CLOSED"],
                            "example": "OPEN",
                        },
                        "led_status": {
                            "type": "string",
                            "enum": ["ON", "OFF"],
                            "example": "ON",
                        },
                        "buzzer_status": {
                            "type": "string",
                            "enum": ["ON", "OFF"],
                            "example": "OFF",
                        },
                        "auto_mode": {"type": "boolean", "example": False},
                        "connection": {
                            "type": "string",
                            "example": "Bluetooth Connected",
                        },
                        "timestamp": {
                            "type": "string",
                            "example": "2026-06-07 10:30:00",
                        },
                    },
                    "required": ["temperature", "humidity", "light", "distance"],
                },
                "CommandResponse": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": True},
                        "message": {
                            "type": "string",
                            "example": "Command sent: LED_ON",
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
                    "description": "Flask subscribes to sensor topics and publishes control commands to the configured MQTT broker. Defaults: data room/data, log room/data/log, control room/control.",
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
                    "description": "If mock_mode is true, generates and stores one demo sample. If mock_mode is false, reads the latest SQLite row without generating data.",
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
                        "404": {
                            "description": "No sensor data available in real-data mode",
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
            "/api/sensor-data": {
                "post": {
                    "tags": ["Sensors"],
                    "summary": "Store sensor data from Raspberry Pi, Arduino bridge, or manual tests",
                    "description": "Accepts a sensor JSON payload and writes it to SQLite. This is the endpoint to call from future Bluetooth/Serial ingestion code.",
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
            "/api/mock-mode": {
                "get": {
                    "tags": ["Config"],
                    "summary": "Get mock mode state",
                    "responses": {
                        "200": {
                            "description": "Current mock mode state",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/MockModeResponse"
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["Config"],
                    "summary": "Enable or disable mock sensor generation",
                    "description": "When disabled, /api/current reads the latest SQLite row instead of generating a new sample.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/MockModeRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Mock mode updated",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/MockModeResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid mock mode payload",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                },
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
                    "summary": "Send LED, buzzer, or door command",
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
    """Return current sensor data.

    In mock mode this generates a fresh sample and stores it in SQLite.
    In real-data mode this only reads the latest SQLite row inserted by
    /api/sensor-data or a future Bluetooth/Serial ingestion process.
    """
    state = get_system_state()
    if state["mock_mode"]:
        record = append_current_record()
        return jsonify(row_to_public_record(record))

    row = latest_sensor_row()
    if row is None:
        return error_response(
            "No sensor data available. POST /api/sensor-data first.", 404
        )
    return jsonify(row_to_public_record(row))


@app.get("/api/mock-mode")
def api_get_mock_mode():
    """Return whether mock sample generation is enabled."""
    state = get_system_state()
    return jsonify(
        {
            "mock_mode": bool(state["mock_mode"]),
            "message": (
                "Mock mode is enabled"
                if state["mock_mode"]
                else "Mock mode is disabled"
            ),
        }
    )


@app.post("/api/mock-mode")
def api_set_mock_mode():
    """Enable/disable mock generation for /api/current."""
    payload = request.get_json(silent=True) or {}
    raw_value = payload.get("mock_mode", payload.get("enabled"))
    if raw_value is None:
        return error_response("Missing required field: mock_mode")
    try:
        mock_mode = parse_bool(raw_value)
    except ValueError as exc:
        return error_response(str(exc))

    update_system_state({"mock_mode": mock_mode})
    return jsonify(
        {
            "success": True,
            "mock_mode": mock_mode,
            "message": "Mock mode enabled" if mock_mode else "Mock mode disabled",
        }
    )


@app.post("/api/sensor-data")
def api_sensor_data():
    """Accept real or test sensor JSON and store it in SQLite.

    This endpoint is ready for Raspberry Pi/Bluetooth/Serial ingestion later.
    For now, you can test it from Swagger UI, curl, or Python.
    """
    payload = request.get_json(silent=True) or {}
    try:
        record, state_updates = record_from_sensor_payload(payload)
    except ValueError as exc:
        return error_response(str(exc))

    update_system_state({**state_updates, "mock_mode": False})
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

    By default only sensor history/chart data is cleared. Control/config state is
    kept so the dashboard does not unexpectedly switch modes during testing.
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
            "temperature": [row["temperature"] for row in rows],
            "humidity": [row["humidity"] for row in rows],
            "light": [row["light"] for row in rows],
            "distance": [row["distance"] for row in rows],
        }
    )


@app.post("/api/control")
def api_control():
    """Persist a mock device command and return the new SQLite-backed state.

    Later this is where Raspberry Pi code can forward commands to Arduino through
    Bluetooth or Serial backup after saving the command/state.
    """
    payload = request.get_json(silent=True) or {}
    command = str(payload.get("command", "")).upper().strip()

    command_map = {
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
            "data": row_to_public_record(append_current_record()),
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
            "data": row_to_public_record(append_current_record()),
        }
    )


# Ensure DB exists for both `python app.py` and WSGI/test imports.
init_db()


if __name__ == "__main__":
    # host="0.0.0.0" lets the dashboard be reached from another device on the
    # Raspberry Pi network: http://RASPBERRY_PI_IP:5001
    # Default port is 5001. If another process already uses port 5001, run:
    # APP_PORT=5002 python app.py
    app.run(
        host=APP_HOST, port=APP_PORT, debug=APP_DEBUG, threaded=True, use_reloader=False
    )

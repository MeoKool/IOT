#!/usr/bin/env python3
"""Bridge Arduino USB Serial data to the Flask water-tank API.

Arduino prints CSV lines like:
    temperature,humidity,water_percent,is_pumping
or:
    temperature,humidity,water_percent,is_pumping,water_raw

This script runs on the Raspberry Pi, reads those lines, POSTs JSON to
/api/sensor-data, and optionally listens to MQTT tank/control commands from the
Flask dashboard to write pump commands back to Arduino over Serial.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    import serial
except ImportError as exc:  # pragma: no cover - shown as friendly CLI error
    raise SystemExit(
        "Missing pyserial. Install with: python -m pip install pyserial"
    ) from exc

try:
    import paho.mqtt.client as mqtt
except ImportError:  # MQTT control bridge is optional.
    mqtt = None


DEFAULT_PORT_PATTERNS = (
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
    "/dev/serial/by-id/*",
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
)


@dataclass
class SensorReading:
    temperature: float
    humidity: float
    water_level: float
    is_pumping: bool
    analog_value: int
    distance_cm: int

    def to_api_payload(self) -> dict[str, Any]:
        alarm_on = self.water_level > 90
        return {
            "water_level": self.water_level,
            "float_level": round(self.water_level),
            "analog_value": self.analog_value,
            "distance_cm": self.distance_cm,
            "valve_status": "OPEN" if self.is_pumping else "CLOSED",
            "pump_status": "ON" if self.is_pumping else "OFF",
            "alarm_status": "ON" if alarm_on else "OFF",
            "auto_mode": True,
            "connection": "Arduino USB Serial via Raspberry Pi",
            # Keep raw DHT values too; the current Flask endpoint ignores unknown
            # keys, but logs/tools can use them later if needed.
            "temperature_c": self.temperature,
            "humidity_percent": self.humidity,
        }


def find_serial_port() -> str | None:
    for pattern in DEFAULT_PORT_PATTERNS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def parse_boolish(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "on", "yes"}:
        return True
    if normalized in {"0", "false", "off", "no"}:
        return False
    raise ValueError(f"invalid pump state {value!r}; expected 0/1")


def estimate_distance_cm(water_level: float) -> int:
    """Approximate tank distance because current Arduino sketch has no HC-SR04."""
    empty_distance_cm = 30
    full_distance_cm = 5
    water_level = max(0.0, min(100.0, water_level))
    return round(empty_distance_cm - (water_level / 100.0) * (empty_distance_cm - full_distance_cm))


def parse_arduino_line(line: str) -> SensorReading:
    parts = [part.strip() for part in line.strip().split(",")]
    if len(parts) not in {4, 5}:
        raise ValueError(
            "expected CSV: temperature,humidity,water_percent,is_pumping[,water_raw]"
        )

    temperature = float(parts[0])
    humidity = float(parts[1])
    water_level = max(0.0, min(100.0, float(parts[2])))
    is_pumping = parse_boolish(parts[3])
    analog_value = int(round(float(parts[4]))) if len(parts) == 5 else round(water_level / 100.0 * 1023)

    return SensorReading(
        temperature=round(temperature, 1),
        humidity=round(humidity, 1),
        water_level=round(water_level, 1),
        is_pumping=is_pumping,
        analog_value=max(0, min(1023, analog_value)),
        distance_cm=estimate_distance_cm(water_level),
    )


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else {"status": response.status}


def normalize_dashboard_command(payload: bytes) -> str | None:
    """Return a one-character Arduino command for the pasted sketch.

    Flask publishes JSON such as {"arduino_code":"P1"}. The current Arduino
    sketch understands '1' for pump on and '0' for pump off, so map P1/P0.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"arduino_code": text}

    code = str(data.get("arduino_code") or data.get("command") or "").upper().strip()
    if code in {"P1", "PUMP_ON", "LED_ON", "1"}:
        return "1"
    if code in {"P0", "PUMP_OFF", "LED_OFF", "0"}:
        return "0"
    return None


def start_mqtt_control_bridge(args: argparse.Namespace, ser: serial.Serial, serial_lock: threading.Lock) -> Any:
    if args.no_mqtt:
        print("MQTT control bridge disabled (--no-mqtt).", flush=True)
        return None
    if mqtt is None:
        print("paho-mqtt is not installed; sensor upload still works, MQTT control disabled.", flush=True)
        return None

    def on_connect(client, userdata, flags, reason_code, properties=None):
        rc = getattr(reason_code, "value", reason_code)
        if rc == 0:
            client.subscribe(args.mqtt_control_topic)
            print(f"MQTT connected; subscribed to {args.mqtt_control_topic}", flush=True)
        else:
            print(f"MQTT connect failed: {reason_code}", flush=True)

    def on_message(client, userdata, message):
        command = normalize_dashboard_command(message.payload)
        if command is None:
            print(
                f"MQTT ignored unsupported command on {message.topic}: "
                f"{message.payload!r}",
                flush=True,
            )
            return
        with serial_lock:
            ser.write(command.encode("ascii"))
            ser.flush()
        print(f"MQTT -> Arduino: {command}", flush=True)

    callback_api_version = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api_version is not None:
        client = mqtt.Client(callback_api_version.VERSION2, client_id=args.mqtt_client_id)
    else:
        client = mqtt.Client(client_id=args.mqtt_client_id)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(args.mqtt_host, args.mqtt_port, 60)
    client.loop_start()
    return client


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arduino Serial -> Flask API bridge")
    parser.add_argument("--serial-port", default=os.environ.get("SERIAL_PORT"), help="Arduino port, e.g. /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=int(os.environ.get("SERIAL_BAUD", "9600")))
    parser.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:5001/api/sensor-data"))
    parser.add_argument("--post-timeout", type=float, default=3.0)
    parser.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--mqtt-control-topic", default=os.environ.get("MQTT_TOPIC_CONTROL", "tank/control"))
    parser.add_argument("--mqtt-client-id", default="smart-water-tank-serial-api-bridge")
    parser.add_argument("--no-mqtt", action="store_true", help="Only upload sensor data; do not listen for dashboard commands")
    parser.add_argument("--demo-line", help="Parse and print one Arduino CSV line, then exit")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.demo_line:
        reading = parse_arduino_line(args.demo_line)
        print(json.dumps(reading.to_api_payload(), indent=2))
        return 0

    serial_port = args.serial_port or find_serial_port()
    if not serial_port:
        print(
            "No Arduino serial port found. Plug Arduino in or pass --serial-port /dev/ttyACM0",
            file=sys.stderr,
        )
        return 2

    serial_lock = threading.Lock()
    print(f"Opening Arduino serial {serial_port} @ {args.baud} baud", flush=True)
    with serial.Serial(serial_port, args.baud, timeout=1) as ser:
        # Let Arduino reset after serial open.
        time.sleep(2)
        ser.reset_input_buffer()
        mqtt_client = start_mqtt_control_bridge(args, ser, serial_lock)
        print(f"Uploading sensor data to {args.api_url}", flush=True)

        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    reading = parse_arduino_line(line)
                    payload = reading.to_api_payload()
                    response = post_json(args.api_url, payload, args.post_timeout)
                    ok = response.get("success", True)
                    print(
                        f"POST {'OK' if ok else 'FAIL'} water={payload['water_level']}% "
                        f"pump={payload['pump_status']}",
                        flush=True,
                    )
                except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    print(f"Skip line {line!r}: {exc}", flush=True)
        except KeyboardInterrupt:
            print("Stopping bridge.", flush=True)
        finally:
            if mqtt_client is not None:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RK3506B edge collector for the ZhiRun irrigation system.

The board reads Modbus RTU sensors through a USB-RS485 adapter, reports the
readings to the public server, and forwards queued pump/relay commands to an
ESP32 over USB serial.  It intentionally uses only the Python standard
library so it fits the Buildroot image on the RK3506B board.
"""
import argparse
import json
import os
import select
import socket
import subprocess
import termios
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULTS = {
    "ZHIRUN_SERVER": "http://8.145.49.45",
    "ZHIRUN_TOKEN": "",
    "ZHIRUN_DEVICE_ID": "rk3506b-01",
    "ZHIRUN_DEVICE_NAME": "RK3506B 水肥控制器",
    "ZHIRUN_RS485_PORT": "/dev/ttyUSB0",
    "ZHIRUN_RS485_BAUD": "4800",
    "ZHIRUN_MODBUS_TIMEOUT_S": "0.35",
    "ZHIRUN_ESP_SERIAL_PORT": "/dev/ttyS1",
    "ZHIRUN_ESP_SERIAL_BAUD": "115200",
    "ZHIRUN_POLL_INTERVAL_S": "0.5",
    "ZHIRUN_PUSH_INTERVAL_S": "5",
    "ZHIRUN_SOIL_ADDR": "2",
    "ZHIRUN_TH_ADDR": "1",
    "ZHIRUN_CO2_ADDR": "3",
    "ZHIRUN_LIGHT_ADDR": "5",
    "ZHIRUN_WIND_ADDR": "4",
    "ZHIRUN_WIND_REG": "0",
    "ZHIRUN_RAIN_ADDR": "6",
    "ZHIRUN_RAIN_REG": "0",
    "ZHIRUN_RAIN_FUNCTION": "3",
    "ZHIRUN_RAIN_SCALE": "0.1",
    "ZHIRUN_RAIN_MM_PER_TIP": "0.3",
}

SPEEDS = {2400: termios.B2400, 4800: termios.B4800, 9600: termios.B9600,
          19200: termios.B19200, 38400: termios.B38400, 115200: termios.B115200}


def load_config(path):
    values = dict(DEFAULTS)
    for candidate in (Path(__file__).resolve().parent.parent / ".env", Path(path)):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in values:
                values[key.strip()] = value.strip()
    for key in values:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def int_value(config, key):
    return int(config[key], 0)


def float_value(config, key):
    return float(config[key])


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class SerialPort:
    def __init__(self, path, baud):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.set_baud(baud)

    def set_baud(self, baud):
        if baud not in SPEEDS:
            raise ValueError("unsupported baud: %s" % baud)
        attrs = termios.tcgetattr(self.fd)
        attrs[0], attrs[1] = termios.IGNPAR, 0
        attrs[2], attrs[3] = termios.CS8 | termios.CREAD | termios.CLOCAL, 0
        attrs[4] = attrs[5] = SPEEDS[baud]
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def exchange(self, request, timeout):
        os.write(self.fd, request)
        result = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            wait = max(0.0, min(0.05, deadline - time.monotonic()))
            ready, _, _ = select.select([self.fd], [], [], wait)
            if ready:
                try:
                    result.extend(os.read(self.fd, 512))
                except BlockingIOError:
                    pass
        return bytes(result)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def serial_candidates(preferred="", excluded=()):
    paths = [preferred] if preferred else []
    for pattern in ("ttyUSB*", "ttyACM*"):
        paths.extend(str(path) for path in sorted(Path("/dev").glob(pattern)))
    excluded = {path for path in excluded if path}
    return [
        path for path in dict.fromkeys(paths)
        if path not in excluded and Path(path).exists()
    ]


def modbus_request(address, register, count, function):
    body = bytes((address, function, register >> 8, register & 255, count >> 8, count & 255))
    crc = crc16(body)
    return body + bytes((crc & 255, crc >> 8))


def parse_modbus_response(raw, address, count, function):
    expected = count * 2
    for start in range(max(0, len(raw) - expected - 5 + 1)):
        frame = raw[start:start + expected + 5]
        if frame[:3] != bytes((address, function, expected)):
            continue
        if crc16(frame[:-2]) != frame[-2] | frame[-1] << 8:
            continue
        return [(frame[3 + i] << 8) | frame[4 + i] for i in range(0, expected, 2)]
    return None


class Modbus:
    def __init__(self, config):
        self.preferred_path = config["ZHIRUN_RS485_PORT"]
        self.baud = int_value(config, "ZHIRUN_RS485_BAUD")
        self.timeout = float_value(config, "ZHIRUN_MODBUS_TIMEOUT_S")
        self.probes = [
            (int_value(config, "ZHIRUN_SOIL_ADDR"), 0, 7, 3),
            (int_value(config, "ZHIRUN_TH_ADDR"), 0, 2, 3),
            (int_value(config, "ZHIRUN_CO2_ADDR"), 0, 1, 3),
            (int_value(config, "ZHIRUN_LIGHT_ADDR"), 0, 2, 3),
        ]
        self.port = None
        self.other_path = lambda: None
        self.misses = 0

    def candidate_paths(self):
        return serial_candidates(self.preferred_path, (self.other_path(),))

    def identify(self, path):
        port = SerialPort(path, self.baud)
        try:
            for address, register, count, function in self.probes:
                raw = port.exchange(
                    modbus_request(address, register, count, function), self.timeout
                )
                if parse_modbus_response(raw, address, count, function) is not None:
                    self.port = port
                    self.misses = 0
                    print("USB role detected: rs485=%s" % path, flush=True)
                    return True
        except (OSError, termios.error):
            pass
        port.close()
        return False

    def ensure_port(self):
        if self.port and Path(self.port.path).exists() and self.port.path != self.other_path():
            return True
        self.close()
        for path in self.candidate_paths():
            try:
                if self.identify(path):
                    return True
            except (OSError, termios.error):
                continue
        return False

    def read(self, address, register, count, function=3):
        request = modbus_request(address, register, count, function)
        for attempt in range(2):
            try:
                if not self.ensure_port():
                    raise FileNotFoundError("no responding USB-RS485 adapter found")
                raw = self.port.exchange(request, self.timeout)
                values = parse_modbus_response(raw, address, count, function)
                if values is not None:
                    self.misses = 0
                    return values
                self.misses += 1
                if self.misses >= len(self.probes):
                    self.close()
                return None
            except (OSError, termios.error):
                self.close()
                if attempt:
                    raise
        return None

    def close(self):
        if self.port:
            print("USB role disconnected: rs485=%s" % self.port.path, flush=True)
            self.port.close()
            self.port = None


def signed16(value):
    return value - 65536 if value & 0x8000 else value


def read_sensors(config, bus):
    data = {}
    soil = bus.read(int_value(config, "ZHIRUN_SOIL_ADDR"), 0, 7)
    if soil:
        data.update({"soilMoist": soil[0] / 10.0, "soilTemp": signed16(soil[1]) / 10.0,
                     "soilEc": soil[2] / 1000.0, "soilPH": soil[3] / 10.0,
                     "n": soil[4], "p": soil[5], "k": soil[6]})
    climate = bus.read(int_value(config, "ZHIRUN_TH_ADDR"), 0, 2)
    if climate:
        data.update({"airHum": climate[0] / 10.0, "airTemp": signed16(climate[1]) / 10.0})
    co2 = bus.read(int_value(config, "ZHIRUN_CO2_ADDR"), 0, 1)
    if co2:
        data["co2"] = co2[0]
    light = bus.read(int_value(config, "ZHIRUN_LIGHT_ADDR"), 0, 2)
    if light:
        data["lux"] = (light[0] << 16) | light[1]
    wind_addr = config.get("ZHIRUN_WIND_ADDR", "").strip()
    if wind_addr:
        wind = bus.read(int(wind_addr, 0), int_value(config, "ZHIRUN_WIND_REG"), 1, function=4)
        if wind:
            data["windSpeed"] = wind[0] / 10.0
    rain_addr = config.get("ZHIRUN_RAIN_ADDR", "").strip()
    if rain_addr:
        rain = bus.read(
            int(rain_addr, 0),
            int_value(config, "ZHIRUN_RAIN_REG"),
            1,
            function=int_value(config, "ZHIRUN_RAIN_FUNCTION"),
        )
        if rain:
            data["rainMm"] = round(rain[0] * float_value(config, "ZHIRUN_RAIN_SCALE"), 2)
    return data


class Esp32Link:
    def __init__(self, config):
        self.preferred_path = config.get("ZHIRUN_ESP_SERIAL_PORT", "").strip()
        self.baud = int_value(config, "ZHIRUN_ESP_SERIAL_BAUD")
        self.port = None
        self.other_path = lambda: None
        self.state = {}
        self.rx = bytearray()
        self.next_probe = 0.0
        self.next_status = 0.0

    def candidate_paths(self):
        return serial_candidates(self.preferred_path, (self.other_path(),))

    def parse(self):
        while b"\n" in self.rx:
            line, _, self.rx = self.rx.partition(b"\n")
            marker = line.find(b"STATE ")
            if marker < 0:
                continue
            try:
                self.state = json.loads(line[marker + 6:].decode("utf-8", "replace"))
            except (ValueError, UnicodeError):
                continue

    def identify(self, path):
        port = SerialPort(path, self.baud)
        try:
            response = port.exchange(b"STATUS\n", 1.2)
        except (OSError, termios.error):
            port.close()
            return False
        if b"STATE " not in response:
            port.close()
            return False
        self.port = port
        self.rx.extend(response)
        self.parse()
        self.next_status = time.monotonic() + 2.0
        print("USB role detected: esp32=%s" % path, flush=True)
        return True

    def ensure_port(self):
        now = time.monotonic()
        if self.port and Path(self.port.path).exists() and self.port.path != self.other_path():
            return True
        self.close()
        if now < self.next_probe:
            return False
        self.next_probe = now + 2.0
        for path in self.candidate_paths():
            try:
                if self.identify(path):
                    return True
            except (OSError, termios.error):
                continue
        return False

    def send(self, value):
        if not self.ensure_port():
            return False
        try:
            os.write(self.port.fd, (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            return True
        except OSError:
            self.close()
            return False

    def poll(self):
        if not self.ensure_port():
            return
        try:
            now = time.monotonic()
            if now >= self.next_status:
                os.write(self.port.fd, b"STATUS\n")
                self.next_status = now + 2.0
            while True:
                chunk = os.read(self.port.fd, 1024)
                if not chunk:
                    break
                self.rx.extend(chunk)
        except BlockingIOError:
            pass
        except OSError:
            self.close()
            return
        self.parse()

    @property
    def connected(self):
        return bool(self.port and Path(self.port.path).exists())

    def snapshot(self):
        state = dict(self.state)
        state["serialConnected"] = self.connected
        state["serialPort"] = self.port.path if self.connected else None
        return state

    def close(self):
        if self.port:
            print("USB role disconnected: esp32=%s" % self.port.path, flush=True)
            self.port.close()
            self.port = None
        self.rx.clear()


def request_json(url, value=None, timeout=5):
    body = None
    headers = {"Accept": "application/json"}
    if value is not None:
        body = json.dumps(value, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def network_snapshot(server):
    result = {"networkType": None, "networkInterface": None, "networkIp": None,
              "networkGateway": None, "networkConnected": False,
              "wifiConnected": False, "wifiSsid": None}
    try:
        target = urlparse(server)
        hostname = target.hostname
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((hostname, target.port or 80))
            result["networkIp"] = probe.getsockname()[0]
            result["networkConnected"] = True
        route = subprocess.check_output(
            ["ip", "-4", "route", "get", hostname],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).split()
        if "dev" in route:
            interface = route[route.index("dev") + 1]
            result["networkInterface"] = interface
            result["networkType"] = "wifi" if interface.startswith(("wl", "wlan")) else "ethernet"
        if "via" in route:
            result["networkGateway"] = route[route.index("via") + 1]
        if result["networkType"] == "wifi":
            status = subprocess.check_output(
                ["wpa_cli", "-i", result["networkInterface"], "status"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            values = dict(line.split("=", 1) for line in status.splitlines() if "=" in line)
            result["wifiConnected"] = values.get("wpa_state") == "COMPLETED"
            result["wifiSsid"] = values.get("ssid")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return result


def wifi_scan_results(interface="wlan0"):
    """Scan nearby APs on the RK3506B and return frontend-ready records."""
    try:
        scan = subprocess.run(
            ["wpa_cli", "-i", interface, "scan"],
            capture_output=True, text=True, timeout=5,
        )
        if scan.returncode != 0 and "FAIL" in (scan.stdout + scan.stderr).upper():
            return []
        # Allow the driver time to populate scan_results, but do not block the
        # collector for the full UI polling window.
        time.sleep(2.0)
        result = subprocess.run(
            ["wpa_cli", "-i", interface, "scan_results"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    networks = []
    lines = result.stdout.splitlines()
    for line in lines[1:]:
        fields = line.split("\t", 4)
        if len(fields) < 5:
            continue
        bssid, frequency, signal, flags, ssid = fields
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            freq = int(frequency)
            rssi = int(signal)
        except ValueError:
            continue
        upper_flags = flags.upper()
        networks.append({
            "ssid": ssid,
            "bssid": bssid,
            "frequency": freq,
            "radio": "5 GHz" if freq >= 3000 else "2.4 GHz",
            "rssi": rssi,
            "auth": flags,
            "lock": any(value in upper_flags for value in ("WPA", "WEP")),
        })
    networks.sort(key=lambda item: item["rssi"], reverse=True)
    return networks


def main():
    parser = argparse.ArgumentParser(description="RK3506B ZhiRun edge collector")
    parser.add_argument("--config", default="/etc/zhirun-rk3506.env")
    args = parser.parse_args()
    config = load_config(args.config)
    server = config["ZHIRUN_SERVER"].rstrip("/")
    device_id = config["ZHIRUN_DEVICE_ID"]
    bus = None
    esp = None
    last_push = 0.0
    last_poll = 0.0
    last_error = ""
    try:
        bus = Modbus(config)
        esp = Esp32Link(config)
        bus.other_path = lambda: esp.port.path if esp.port else None
        esp.other_path = lambda: bus.port.path if bus.port else None
        while True:
            now = time.monotonic()
            esp.poll()
            if esp.connected and now - last_poll >= float_value(config, "ZHIRUN_POLL_INTERVAL_S"):
                last_poll = now
                try:
                    query = urlencode({"token": config["ZHIRUN_TOKEN"]})
                    path = "/api/devices/%s/valve/commands/next?%s" % (quote(device_id, safe=""), query)
                    response = request_json(server + path, timeout=3)
                    command = response.get("command") or {}
                    if command.get("action") == "network_scan":
                        networks = wifi_scan_results()
                        state = dict(esp.state)
                        state.update({
                            "wifiNetworks": networks,
                            "wifiScannedAt": int(time.time()),
                            "lastCommandId": command.get("id", ""),
                        })
                        state_url = server + "/api/devices/%s/valve/result" % quote(device_id, safe="")
                        request_json(state_url, {"token": config["ZHIRUN_TOKEN"], "state": state}, timeout=5)
                    elif command and not esp.send({"command": command}):
                        raise OSError("ESP32 serial link lost before command send")
                except (OSError, ValueError) as exc:
                    last_error = "command: %s" % exc
            if now - last_push >= float_value(config, "ZHIRUN_PUSH_INTERVAL_S"):
                last_push = now
                try:
                    payload = read_sensors(config, bus)
                    payload.update(network_snapshot(server))
                    payload.update({"esp32": esp.snapshot(), "collectorError": last_error})
                    if "rainMm" not in payload and "rainMm" in esp.state:
                        payload["rainMm"] = esp.state["rainMm"]
                        payload["rainTips"] = esp.state.get("rainTips")
                    envelope = {"token": config["ZHIRUN_TOKEN"], "device_id": device_id,
                                "device_name": config["ZHIRUN_DEVICE_NAME"],
                                "model": "RK3506B + ESP32 + USB-RS485",
                                "firmware_version": "rk3506-collector-1.0",
                                "capabilities": ["rs485", "relay", "valve_control", "esp32", "lvgl"],
                                "data_source": "rk3506", "payload": payload}
                    request_json(server + "/push", envelope, timeout=5)
                    if esp.state:
                        state_url = server + "/api/devices/%s/valve/result" % quote(device_id, safe="")
                        request_json(state_url, {"token": config["ZHIRUN_TOKEN"], "state": esp.state}, timeout=3)
                    last_error = ""
                except (OSError, ValueError) as exc:
                    last_error = "sensor: %s" % exc
            time.sleep(0.05)
    finally:
        if bus:
            bus.close()
        if esp:
            esp.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RK3506B edge collector for the ZhiRun irrigation system.

The board reads Modbus RTU sensors through a USB-RS485 adapter, reports the
readings to the public server, and forwards queued pump/relay commands to an
ESP32 over USB serial.  It intentionally uses only the Python standard
library so it fits the Buildroot image on the RK3506B board.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import select
import shutil
import socket
import ssl
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

CAMPUS_PROFILE = Path("/userdata/zhirun-campus.json")
SRUN_BASE = "https://login.imau.edu.cn"
SRUN_AC_ID = "6"
SRUN_BASE64_ALPHABET = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
STANDARD_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


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


def _srun_words(value, include_length):
    raw = value.encode("utf-8")
    words = [int.from_bytes(raw[index:index + 4].ljust(4, b"\0"), "little")
             for index in range(0, len(raw), 4)]
    if include_length:
        words.append(len(raw))
    return words


def _srun_xencode(value, token):
    """SRun's XXTEA-compatible encoder, returned as raw little-endian bytes."""
    if not value:
        return b""
    values = _srun_words(value, True)
    key = (_srun_words(token, False) + [0, 0, 0, 0])[:4]
    count = len(values) - 1
    z = values[count]
    total = 0
    rounds = 6 + 52 // (count + 1)
    while rounds:
        total = (total + 0x9E3779B9) & 0xFFFFFFFF
        e = (total >> 2) & 3
        for position in range(count):
            y = values[position + 1]
            mixed = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) \
                ^ ((total ^ y) + (key[(position & 3) ^ e] ^ z))
            values[position] = (values[position] + mixed) & 0xFFFFFFFF
            z = values[position]
        y = values[0]
        mixed = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) \
            ^ ((total ^ y) + (key[(count & 3) ^ e] ^ z))
        values[count] = (values[count] + mixed) & 0xFFFFFFFF
        z = values[count]
        rounds -= 1
    return b"".join(word.to_bytes(4, "little") for word in values)


def _srun_base64(raw):
    encoded = base64.b64encode(raw).decode("ascii")
    return encoded.translate(str.maketrans(STANDARD_BASE64_ALPHABET, SRUN_BASE64_ALPHABET))


def _jsonp(value):
    value = value.decode("utf-8", "replace").strip()
    if value.startswith("{"):
        return json.loads(value)
    start, end = value.find("("), value.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("invalid portal response")
    return json.loads(value[start + 1:end])


def _srun_request(path, params, timeout=12):
    callback = "zhirun_%d" % int(time.time() * 1000)
    values = dict(params)
    values.update({"callback": callback, "_": str(int(time.time() * 1000))})
    request = Request(
        SRUN_BASE + path + "?" + urlencode(values),
        headers={"Accept": "application/json", "User-Agent": "ZhiRun-RK3506B/1.0"},
    )
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        return _jsonp(response.read())


def srun_login(username, password, ip_address, ac_id=SRUN_AC_ID):
    challenge = _srun_request("/cgi-bin/get_challenge", {
        "username": username,
        "ip": ip_address,
    }).get("challenge")
    if not challenge:
        return {"ok": False, "message": "challenge_failed"}

    hmd5 = hmac.new(challenge.encode(), password.encode(), hashlib.md5).hexdigest()
    info_value = json.dumps({
        "username": username,
        "password": password,
        "ip": ip_address,
        "acid": str(ac_id),
        "enc_ver": "srun_bx1",
    }, ensure_ascii=False, separators=(",", ":"))
    info = "{SRBX1}" + _srun_base64(_srun_xencode(info_value, challenge))
    checksum_source = "".join((
        challenge, username,
        challenge, hmd5,
        challenge, str(ac_id),
        challenge, ip_address,
        challenge, "200",
        challenge, "1",
        challenge, info,
    ))
    result = _srun_request("/cgi-bin/srun_portal", {
        "action": "login",
        "username": username,
        "password": "{MD5}" + hmd5,
        "os": "Linux",
        "name": "Linux",
        "double_stack": "0",
        "chksum": hashlib.sha1(checksum_source.encode()).hexdigest(),
        "info": info,
        "ac_id": str(ac_id),
        "ip": ip_address,
        "n": "200",
        "type": "1",
    })
    ok = result.get("error") == "ok" or result.get("res") == "ok"
    message = result.get("suc_msg") or result.get("error_msg") or result.get("error") or "unknown"
    return {"ok": ok, "message": str(message)[:160]}


def wifi_status(interface="wlan0"):
    try:
        output = subprocess.check_output(
            ["wpa_cli", "-i", interface, "status"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def public_network_available(server):
    try:
        request = Request(server.rstrip("/") + "/data", headers={"Accept": "application/json"})
        with urlopen(request, timeout=4) as response:
            return response.status == 200 and "application/json" in response.headers.get("Content-Type", "")
    except (OSError, ValueError):
        return False


def renew_wifi_lease(interface="wlan0"):
    try:
        subprocess.run(["dhcpcd", "-k", interface], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=8, check=False)
        subprocess.run(["ip", "addr", "flush", "dev", interface, "scope", "global"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
        lease = subprocess.run(
            ["udhcpc", "-i", interface, "-n", "-q", "-t", "10", "-T", "2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    status = wifi_status(interface)
    return status.get("ip_address", "") if lease.returncode == 0 else ""


def _wpa(interface, *args):
    result = subprocess.run(
        ["wpa_cli", "-i", interface] + list(args),
        capture_output=True, text=True, timeout=6,
    )
    output = result.stdout.strip()
    if result.returncode or output.splitlines()[-1:] == ["FAIL"]:
        raise OSError("wpa_cli failed: %s" % " ".join(args[:2]))
    return output


def _current_network_id(interface="wlan0"):
    current = wifi_status(interface).get("ssid", "")
    if not current:
        return None
    for line in _wpa(interface, "list_networks").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == current and "CURRENT" in line:
            return fields[0]
    return None


def _persist_wifi_config():
    source = Path("/etc/wpa_supplicant.conf")
    destination = Path("/userdata/zhirun-wpa.conf")
    if source.exists():
        shutil.copyfile(str(source), str(destination))
        os.chmod(str(destination), 0o600)


def save_campus_profile(profile):
    temporary = CAMPUS_PROFILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(CAMPUS_PROFILE))


def load_campus_profile():
    try:
        value = json.loads(CAMPUS_PROFILE.read_text(encoding="utf-8"))
        if all(isinstance(value.get(key), str) and value.get(key)
               for key in ("ssid", "username", "password")):
            return value
    except (OSError, ValueError):
        pass
    return None


def configure_network(command, server, interface="wlan0"):
    ssid = str(command.get("ssid") or "").strip()
    password = str(command.get("password") or "")
    campus = bool(command.get("campus"))
    previous_status = wifi_status(interface)
    previous_ssid = previous_status.get("ssid", "")
    previous_id = _current_network_id(interface)
    candidate_id = None
    try:
        candidate_id = _wpa(interface, "add_network").splitlines()[-1]
        _wpa(interface, "set_network", candidate_id, "ssid",
             json.dumps(ssid, ensure_ascii=False))
        if password:
            _wpa(interface, "set_network", candidate_id, "psk",
                 json.dumps(password, ensure_ascii=False))
        else:
            _wpa(interface, "set_network", candidate_id, "key_mgmt", "NONE")
        _wpa(interface, "set_network", candidate_id, "scan_ssid", "1")
        _wpa(interface, "set_network", candidate_id, "priority", "20")
        _wpa(interface, "select_network", candidate_id)

        for _ in range(35):
            time.sleep(1)
            status = wifi_status(interface)
            if status.get("wpa_state") == "COMPLETED" and status.get("ssid") == ssid:
                break
        else:
            raise OSError("association_timeout")

        ip_address = renew_wifi_lease(interface)
        if not ip_address:
            raise OSError("dhcp_failed")

        portal_result = {"ok": True, "message": "not_required"}
        if campus:
            username = str(command.get("campus_username") or "").strip()
            campus_password = str(command.get("campus_password") or "")
            if not username or not campus_password:
                raise ValueError("campus_credentials_required")
            portal_result = srun_login(username, campus_password, ip_address,
                                       str(command.get("campus_ac_id") or SRUN_AC_ID))
            if not portal_result["ok"]:
                raise OSError("portal_%s" % portal_result["message"])
            save_campus_profile({
                "ssid": ssid,
                "username": username,
                "password": campus_password,
                "ac_id": str(command.get("campus_ac_id") or SRUN_AC_ID),
            })

        if not public_network_available(server):
            raise OSError("public_network_unavailable")
        if previous_id is not None and previous_id != candidate_id:
            if previous_ssid == ssid:
                _wpa(interface, "remove_network", previous_id)
            else:
                _wpa(interface, "set_network", previous_id, "priority", "10")
                _wpa(interface, "enable_network", previous_id)
        _wpa(interface, "save_config")
        _persist_wifi_config()
        return {
            "wifiConnected": True,
            "wifiSsid": ssid,
            "portalRequired": campus,
            "portalAuthenticated": True if campus else None,
            "portalStatus": "authenticated" if campus else "not_required",
            "portalMessage": portal_result["message"],
            "networkConfigStatus": "success",
            "lastCommandId": str(command.get("id") or ""),
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if candidate_id is not None:
            try:
                _wpa(interface, "remove_network", candidate_id)
            except OSError:
                pass
        if previous_id is not None:
            try:
                _wpa(interface, "select_network", previous_id)
                for _ in range(20):
                    time.sleep(1)
                    if wifi_status(interface).get("wpa_state") == "COMPLETED":
                        break
                renew_wifi_lease(interface)
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "portalRequired": campus,
            "portalAuthenticated": False if campus else None,
            "portalStatus": "failed" if campus else "not_required",
            "portalMessage": str(exc)[:160],
            "networkConfigStatus": "failed",
            "networkConfigSsid": ssid,
            "lastCommandId": str(command.get("id") or ""),
        }


def refresh_campus_session(server, interface="wlan0"):
    profile = load_campus_profile()
    status = wifi_status(interface)
    if not profile or status.get("ssid") != profile.get("ssid"):
        return {
            "portalRequired": False,
            "portalAuthenticated": None,
            "portalStatus": "not_required",
        }
    if public_network_available(server):
        return {
            "portalRequired": True,
            "portalAuthenticated": True,
            "portalStatus": "authenticated",
        }
    ip_address = status.get("ip_address") or renew_wifi_lease(interface)
    if not ip_address:
        return {
            "portalRequired": True,
            "portalAuthenticated": False,
            "portalStatus": "dhcp_failed",
        }
    try:
        result = srun_login(profile["username"], profile["password"], ip_address,
                            profile.get("ac_id", SRUN_AC_ID))
        return {
            "portalRequired": True,
            "portalAuthenticated": bool(result["ok"]),
            "portalStatus": "authenticated" if result["ok"] else "failed",
            "portalMessage": result["message"],
        }
    except (OSError, ValueError) as exc:
        return {
            "portalRequired": True,
            "portalAuthenticated": False,
            "portalStatus": "failed",
            "portalMessage": str(exc)[:160],
        }


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
    last_campus_check = 0.0
    last_error = ""
    network_state = {}
    try:
        bus = Modbus(config)
        esp = Esp32Link(config)
        bus.other_path = lambda: esp.port.path if esp.port else None
        esp.other_path = lambda: bus.port.path if bus.port else None
        while True:
            now = time.monotonic()
            esp.poll()
            if now - last_campus_check >= 30.0:
                last_campus_check = now
                network_state.update(refresh_campus_session(server))
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
                    elif command.get("action") == "network_config":
                        network_state = configure_network(command, server)
                        state = dict(esp.state)
                        state.update(network_state)
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
                    payload.update(network_state)
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
                        state = dict(esp.state)
                        state.update(network_state)
                        request_json(state_url, {"token": config["ZHIRUN_TOKEN"], "state": state}, timeout=3)
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

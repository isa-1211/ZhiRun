#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智润环境监测服务。

服务端保持纯标准库实现, 接收 RK3506B 的 /push 上报, 对外提供
实时数据页 /data、字段布局 /schema、设备状态 /config 与历史缓存。
"""
import io
import json
import os
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)


def load_local_env():
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_local_env()

PORT = int(os.environ.get("ZHIRUN_PORT", "10000"))
PUSH_TOKEN = os.environ.get("ZHIRUN_PUSH_TOKEN", "").strip()
STATE_FILE = os.environ.get("ZHIRUN_STATE_FILE", os.path.join(ROOT, ".zhirun_state.json"))
REALTIME_SOURCE = os.environ.get("ZHIRUN_REALTIME_SOURCE", "").strip().lower()
REALTIME_DEVICE_ID = os.environ.get("ZHIRUN_REALTIME_DEVICE_ID", "").strip()
WEATHER_FALLBACK_LATITUDE = float(os.environ.get("ZHIRUN_WEATHER_FALLBACK_LATITUDE", "40.82"))
WEATHER_FALLBACK_LONGITUDE = float(os.environ.get("ZHIRUN_WEATHER_FALLBACK_LONGITUDE", "111.65"))
FERTIGATION_URL = os.environ.get("ZHIRUN_FERTIGATION_URL", "http://127.0.0.1:10001").rstrip("/")
AUTO_MODEL_ENABLED = os.environ.get("ZHIRUN_AUTO_MODEL_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
AUTO_MODEL_EXECUTE = os.environ.get("ZHIRUN_AUTO_MODEL_EXECUTE", "1").strip().lower() in {"1", "true", "yes"}
AUTO_MODEL_HOUR = int(os.environ.get("ZHIRUN_AUTO_MODEL_HOUR", "12"))
AUTO_MODEL_MINUTE = int(os.environ.get("ZHIRUN_AUTO_MODEL_MINUTE", "0"))
AUTO_MODEL_N_CONCENTRATION = float(os.environ.get("ZHIRUN_AUTO_MODEL_N_G_L", "100"))
AUTO_MODEL_P_CONCENTRATION = float(os.environ.get("ZHIRUN_AUTO_MODEL_P_G_L", "80"))
AUTO_MODEL_K_CONCENTRATION = float(os.environ.get("ZHIRUN_AUTO_MODEL_K_G_L", "120"))
HISTORY_LIMIT = 720
RECORD_INTERVAL_SECONDS = 5 * 60
RECORDING_LIMIT = 105120  # Five-minute samples for one year.
LEGACY_DEVICE_ID = "legacy-default"

# ---- 固定字段布局 ------------------------------------------------------------
# 本设备是环境监测设备, 字段固定为下面 13 项, 不随设备上报动态增减:
# 无论设备某一帧发没发某个键, 这 13 格永远显示 (缺值显示 "--", 有值即实时更新)。
# group: headline=顶部大数字, sensor=传感器网格。digits=小数位。
FIELDS_LIST = [
    ("airTemp",   "空气温度",  "°C",      "headline", 1),
    ("airHum",    "空气湿度",  "%RH",     "headline", 1),
    ("co2",       "CO₂浓度",   "ppm",     "headline", 0),
    ("lux",       "光照强度",  "lux",     "headline", 0),
    ("soilMoist", "土壤水分",  "%",       "sensor",   1),
    ("soilTemp",  "土壤温度",  "°C",      "sensor",   1),
    ("soilPH",    "土壤 PH",   "",        "sensor",   2),
    ("soilEc",    "土壤 EC",   "dS/m",    "sensor",   2),
    ("windSpeed", "瞬时风速",  "m/s",     "sensor",   1),
    ("n",         "氮 N",      "mg/kg",   "sensor",   0),
    ("p",         "磷 P",      "mg/kg",   "sensor",   0),
    ("k",         "钾 K",      "mg/kg",   "sensor",   0),
    ("rainMm",    "雨量",      "mm/24h",  "sensor",   1),
]
# 固定 schema: 每台设备都用同一套 13 字段布局, 与设备上报的 fields 无关
FIXED_FIELDS = [
    {"key": k, "label": lab, "unit": u, "group": g, "digits": d}
    for (k, lab, u, g, d) in FIELDS_LIST
]
FIXED_KEYS = [f["key"] for f in FIXED_FIELDS]

# 翻斗原始计数只用于计算雨量, 不作为独立参数展示。
IGNORE_KEYS = {"rainTips"}


def resolve_fields(device, latest):
    """返回固定的 13 项环境字段布局。

    不再依赖设备上报的 fields, 也不再因某帧缺失而增删字段——布局恒定,
    每格的数值由 /data 提供 (有值实时刷新, 无值显示 --)。
    """
    return [dict(f) for f in FIXED_FIELDS]

_lock = threading.RLock()
_devices = {}
_latest_by_device = {}
_history_by_device = {}
_recordings_by_device = {}
_valve_by_device = {}
_valve_commands_by_device = {}
_network_attempt_by_device = {}
_next_valve_command_id = 1
_weather_cache = {"key": None, "updated_at": 0, "data": None}
_auto_model_state = {
    "enabled": AUTO_MODEL_ENABLED,
    "execute_enabled": AUTO_MODEL_EXECUTE,
    "schedule": f"{AUTO_MODEL_HOUR:02d}:{AUTO_MODEL_MINUTE:02d}",
    "last_run_at": 0,
    "last_status": "never",
    "last_error": None,
    "last_decision": None,
    "last_input_quality": None,
    "execution_queued": False,
}

# 落盘节流: 不再每帧 push 都同步写盘 (会把所有读请求堵在锁上)。
# 改为标记脏 + 后台线程每 _SAVE_INTERVAL 秒落一次盘, 重启前再强制 flush。
_save_dirty = False
_save_interval = 5.0


def now():
    return int(time.time())


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def safe_device_id(value):
    value = str(value or "").strip()
    if not value:
        return LEGACY_DEVICE_ID
    return value[:96]


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        devices = state.get("devices", {})
        allowed_ids = {
            device_id for device_id, metadata in devices.items()
            if isinstance(metadata, dict) and metadata.get("source") == "rk3506"
        }
        _devices.update({key: value for key, value in devices.items() if key in allowed_ids})
        _latest_by_device.update({key: value for key, value in state.get("latest", {}).items() if key in allowed_ids})
        _history_by_device.update({key: value for key, value in state.get("history", {}).items() if key in allowed_ids})
        _recordings_by_device.update({key: value for key, value in state.get("recordings", {}).items() if key in allowed_ids})
        if len(allowed_ids) != len(devices):
            mark_dirty()
    except Exception as exc:
        print("状态文件读取失败, 将从空状态启动:", exc, file=sys.stderr)


def mark_dirty():
    """标记状态已变更, 由后台线程节流落盘, 避免每帧同步写盘阻塞读请求。"""
    global _save_dirty
    _save_dirty = True


def save_state(force=False):
    """实际落盘。force=True 时无视脏标记立即写 (用于关停前 flush)。"""
    global _save_dirty
    if not force and not _save_dirty:
        return
    with _lock:
        state = {
            "devices": _devices,
            "latest": _latest_by_device,
            "history": _history_by_device,
            "recordings": _recordings_by_device,
        }
        _save_dirty = False
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print("状态文件写入失败:", exc, file=sys.stderr)
        _save_dirty = True  # 写失败, 留给下一轮重试


def _save_loop():
    while True:
        time.sleep(_save_interval)
        try:
            save_state()
        except Exception as exc:
            print("后台落盘异常:", exc, file=sys.stderr)


def _auto_model_once():
    """Run the daily model check using the latest device frame."""
    with _lock:
        device_id = current_device_id()
        latest = dict(_latest_by_device.get(device_id, {})) if device_id else {}
    payload = dict(latest)
    payload.update({
        "n_concentration_g_l": AUTO_MODEL_N_CONCENTRATION,
        "p_concentration_g_l": AUTO_MODEL_P_CONCENTRATION,
        "k_concentration_g_l": AUTO_MODEL_K_CONCENTRATION,
    })
    timestamp = now()
    try:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(FERTIGATION_URL + "/predict", data=raw,
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        decision = result.get("decision") if isinstance(result, dict) else None
        nested = result.get("result", {}) if isinstance(result, dict) else {}
        queued = False
        queue_error = None
        if AUTO_MODEL_EXECUTE and isinstance(decision, dict) and decision.get("execution_status") == "ready":
            job = nested.get("job", {}) if isinstance(nested, dict) else {}
            targets = job.get("targets_l", {}) if isinstance(job, dict) else {}
            params = {
                "n_target_l": round(float(targets.get("N", 0) or 0), 3),
                "p_target_l": round(float(targets.get("P", 0) or 0), 3),
                "k_target_l": round(float(targets.get("K", 0) or 0), 3),
                "outlet_run_s": round(float(job.get("outlet_run_s", 0) or 0), 3),
            }
            with _lock:
                state = _valve_by_device.get(device_id, {}) if device_id else {}
                if state.get("controllerSchema") != "four_relay_independent_flow_v1":
                    queue_error = "four_relay_firmware_required"
                elif not any(params.values()):
                    queue_error = "empty_fertigation_job"
                else:
                    queued = bool(queue_valve_command(device_id, "fertigation_start", params))
                    if not queued:
                        queue_error = "serial_device_offline"
        quality = decision.get("input_quality") if isinstance(decision, dict) else None
        with _lock:
            _auto_model_state.update({
                "last_run_at": timestamp,
                "last_status": "ok" if isinstance(result, dict) and result.get("ok", True) else "rejected",
                "last_error": queue_error,
                "last_decision": decision,
                "last_input_quality": quality,
                "execution_queued": queued,
                "device_id": device_id,
            })
    except Exception as exc:
        with _lock:
            _auto_model_state.update({
                "last_run_at": timestamp,
                "last_status": "error",
                "last_error": str(exc),
                "last_decision": None,
                "last_input_quality": None,
                "execution_queued": False,
                "device_id": device_id,
            })
        print("每日模型运行失败:", exc, file=sys.stderr)


def _auto_model_loop():
    last_run_day = None
    while True:
        try:
            local = time.localtime()
            day = (local.tm_year, local.tm_yday)
            minute = local.tm_hour * 60 + local.tm_min
            target = AUTO_MODEL_HOUR * 60 + AUTO_MODEL_MINUTE
            if AUTO_MODEL_ENABLED and target <= minute < target + 5 and day != last_run_day:
                _auto_model_once()
                last_run_day = day
        except Exception as exc:
            print("每日模型调度异常:", exc, file=sys.stderr)
        time.sleep(20)


def latest_age(record):
    timestamp = record.get("_ts", 0) if isinstance(record, dict) else 0
    return max(0, now() - int(timestamp)) if timestamp else 999999


def weather_forecast(latitude, longitude):
    """Fetch a short Open-Meteo forecast, caching one location for 10 minutes."""
    latitude = float(latitude)
    longitude = float(longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("invalid_coordinates")

    cache_key = (round(latitude, 3), round(longitude, 3))
    with _lock:
        if (_weather_cache["key"] == cache_key
                and now() - _weather_cache["updated_at"] < 600
                and _weather_cache["data"]):
            return dict(_weather_cache["data"])

    query = (
        "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "precipitation,weather_code,wind_speed_10m"
        "&hourly=temperature_2m,precipitation_probability,weather_code,wind_speed_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,"
        "sunrise,sunset&timezone=auto&forecast_days=7"
    ).format(lat=latitude, lon=longitude)
    request = Request(query, headers={"User-Agent": "ZhiRun-WeatherPanel/1.0"})
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        # Weather is auxiliary data. Keep the dashboard usable when the
        # external provider is briefly unavailable, using the last location
        # cache before falling back to an empty but valid response.
        with _lock:
            if _weather_cache["key"] == cache_key and _weather_cache["data"]:
                stale = dict(_weather_cache["data"])
                stale["stale"] = True
                return stale
        return {
            "ok": True,
            "stale": True,
            "latitude": latitude,
            "longitude": longitude,
            "current": {},
            "hourly": {},
            "daily": {},
            "timezone": "",
            "updated_at": now(),
        }

    result = {
        "ok": True,
        "latitude": latitude,
        "longitude": longitude,
        "current": payload.get("current", {}),
        "hourly": payload.get("hourly", {}),
        "daily": payload.get("daily", {}),
        "timezone": payload.get("timezone", ""),
        "updated_at": now(),
    }
    with _lock:
        _weather_cache.update({"key": cache_key, "updated_at": now(), "data": result})
    return result


DEVICE_ONLINE_TIMEOUT = 45


def device_online(device):
    return latest_age(device) <= DEVICE_ONLINE_TIMEOUT


def current_device_id(requested_device_id=None):
    """Return the device selected for the public realtime page."""
    if requested_device_id:
        return safe_device_id(requested_device_id)
    if REALTIME_DEVICE_ID:
        return safe_device_id(REALTIME_DEVICE_ID)
    # The RK3506B edge controller reports directly to /push. The public page
    # selects it unless a specific source/device is explicitly configured.
    allowed_sources = {REALTIME_SOURCE} if REALTIME_SOURCE else {"rk3506"}
    candidates = [
        device_id for device_id, device in _devices.items()
        if device.get("source") in allowed_sources
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda device_id: _latest_by_device.get(device_id, {}).get("_ts", 0))


def current_valve_device_id():
    """Prefer the RK3506B edge relay controller when it is online."""
    candidates = [
        device_id for device_id, device in _devices.items()
        if "valve_control" in (device.get("capabilities") or [])
        and device_online(_latest_by_device.get(device_id, {}))
    ]
    if candidates:
        rk3506_candidates = [
            device_id for device_id in candidates
            if _devices.get(device_id, {}).get("source") == "rk3506"
        ]
        if rk3506_candidates:
            return max(rk3506_candidates, key=lambda device_id: _latest_by_device.get(device_id, {}).get("_ts", 0))
        return max(candidates, key=lambda device_id: _latest_by_device.get(device_id, {}).get("_ts", 0))
    return current_device_id()


def valve_snapshot(device_id):
    state = dict(_valve_by_device.get(device_id, {}))
    latest = _latest_by_device.get(device_id, {})
    for key in ("wifiConnected", "wifiSsid"):
        if key in latest:
            state[key] = latest[key]
    state["device_id"] = device_id
    state["online"] = device_online(latest)
    attempt = _network_attempt_by_device.get(device_id)
    if attempt:
        elapsed = now() - attempt["started_at"]
        connected_ssid = state.get("wifiSsid") if state.get("wifiConnected") else ""
        if connected_ssid == attempt["ssid"]:
            state["networkAttempt"] = {"status": "success", "ssid": attempt["ssid"]}
            _network_attempt_by_device.pop(device_id, None)
        elif elapsed >= 90:
            state["networkAttempt"] = {"status": "failed", "ssid": attempt["ssid"]}
        else:
            state["networkAttempt"] = {"status": "connecting", "ssid": attempt["ssid"]}
    return state


def queue_valve_command(device_id, action, params):
    global _next_valve_command_id
    if not device_id or not device_online(_latest_by_device.get(device_id, {})):
        return None
    # Manual controls force manual ownership. Drop stale control commands so a
    # delayed close/mode/config cannot override the latest manual intent.
    if action in {"manual", "fertigation_start", "fertigation_stop"}:
        pending = _valve_commands_by_device.get(device_id, [])
        _valve_commands_by_device[device_id] = [
            item for item in pending
            if item.get("action") not in {
                "manual", "mode", "pump_test", "fertigation_start", "fertigation_stop"
            }
        ]
    elif action == "pump_test":
        # Keep independent N/P/K actions, but discard an older pending action
        # for the same pump so rapid start/stop clicks cannot apply out of order.
        pending = _valve_commands_by_device.get(device_id, [])
        pump = params.get("pump")
        _valve_commands_by_device[device_id] = [
            item for item in pending
            if item.get("action") != "pump_test" or item.get("pump") != pump
        ]
    command = {"id": str(_next_valve_command_id), "action": action}
    command.update(params)
    _next_valve_command_id += 1
    _valve_commands_by_device.setdefault(device_id, []).append(command)
    return command


def device_snapshot(device_id):
    device = dict(_devices.get(device_id, {}))
    latest = dict(_latest_by_device.get(device_id, {}))
    age = latest_age(latest)
    device["device_id"] = device_id
    device.pop("device_token", None)
    device["online"] = age <= DEVICE_ONLINE_TIMEOUT
    device["status"] = "online_local" if age <= DEVICE_ONLINE_TIMEOUT else "offline"
    device["last_seen"] = latest.get("_ts", device.get("last_seen", 0))
    device["age"] = age
    device["latest"] = latest
    # 解析出该设备当前应渲染的字段 (设备自报 > 字典 > 键名), 前端照单渲染
    device["fields"] = resolve_fields(device, latest)
    return device


def strip_auth(obj):
    value = dict(obj or {})
    value.pop("token", None)
    value.pop("device_token", None)
    value.pop("setup_code", None)
    return value


def authorized(obj, device_id=None):
    if not PUSH_TOKEN:
        return True
    supplied = str((obj or {}).get("token") or (obj or {}).get("device_token") or "")
    if supplied and supplied == PUSH_TOKEN:
        return True
    device = _devices.get(device_id or safe_device_id((obj or {}).get("device_id")), {})
    stored = device.get("device_token")
    return bool(stored and supplied == stored)


def recording_sample(data, recorded_at):
    sample = {
        key: data.get(key)
        for key in FIXED_KEYS
        if data.get(key) is not None
    }
    sample["_recorded_at"] = recorded_at
    sample["_data_ts"] = data.get("_ts", recorded_at)
    return sample


def recording_snapshot(device_id):
    recording = _recordings_by_device.get(device_id, {}) if device_id else {}
    return {
        "ok": True,
        "device_id": device_id,
        "active": bool(recording.get("active")),
        "started_at": int(recording.get("started_at", 0) or 0),
        "stopped_at": int(recording.get("stopped_at", 0) or 0),
        "next_sample_at": int(recording.get("next_sample_at", 0) or 0),
        "sample_count": len(recording.get("items", [])),
        "interval_seconds": RECORD_INTERVAL_SECONDS,
        "can_export": bool(recording.get("items")),
    }


def start_recording(device_id):
    timestamp = now()
    existing = _recordings_by_device.get(device_id, {})
    if existing.get("active"):
        return recording_snapshot(device_id)

    latest = _latest_by_device.get(device_id, {})
    items = [recording_sample(latest, timestamp)] if latest.get("_ts") else []
    _recordings_by_device[device_id] = {
        "active": True,
        "started_at": timestamp,
        "stopped_at": 0,
        "next_sample_at": timestamp + RECORD_INTERVAL_SECONDS if items else timestamp,
        "items": items,
    }
    mark_dirty()
    return recording_snapshot(device_id)


def stop_recording(device_id):
    recording = _recordings_by_device.get(device_id)
    if recording and recording.get("active"):
        recording["active"] = False
        recording["stopped_at"] = now()
        recording["next_sample_at"] = 0
        mark_dirty()
    return recording_snapshot(device_id)


def maybe_record_sample(device_id, data, timestamp):
    recording = _recordings_by_device.get(device_id)
    if not recording or not recording.get("active"):
        return
    next_sample_at = int(recording.get("next_sample_at", 0) or timestamp)
    if timestamp < next_sample_at:
        return

    items = recording.setdefault("items", [])
    items.append(recording_sample(data, timestamp))
    elapsed_intervals = (timestamp - next_sample_at) // RECORD_INTERVAL_SECONDS + 1
    next_sample_at += elapsed_intervals * RECORD_INTERVAL_SECONDS
    recording["next_sample_at"] = next_sample_at
    if len(items) >= RECORDING_LIMIT:
        recording["active"] = False
        recording["stopped_at"] = timestamp
        recording["next_sample_at"] = 0


def column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xml_text(value):
    value = "".join(
        char for char in str(value)
        if ord(char) >= 32 or char in "\t\n\r"
    )
    return escape(value)


def xlsx_cell(reference, value, style=None):
    style_attr = f' s="{style}"' if style is not None else ""
    if isinstance(value, bool):
        value = "是" if value else "否"
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"{style_attr}>'
        f'<is><t>{xml_text(value)}</t></is></c>'
    )


def recording_xlsx(device_id, recording):
    columns = [("记录时间", "_recorded_at", ""), ("数据上报时间", "_data_ts", "")]
    columns.extend((field[1], field[0], field[2]) for field in FIELDS_LIST)
    headers = [f"{label} ({unit})" if unit else label for label, _key, unit in columns]

    rows = []
    header_cells = [
        xlsx_cell(f"{column_name(index)}1", value, 1)
        for index, value in enumerate(headers, 1)
    ]
    rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for row_index, sample in enumerate(recording.get("items", []), 2):
        cells = []
        for column_index, (_label, key, _unit) in enumerate(columns, 1):
            value = sample.get(key)
            if key in {"_recorded_at", "_data_ts"} and value:
                value = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
            if value is not None:
                cells.append(xlsx_cell(f"{column_name(column_index)}{row_index}", value))
        rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    last_column = column_name(len(columns))
    last_row = max(1, len(rows))
    column_widths = '<col min="1" max="2" width="21" customWidth="1"/>' + (
        f'<col min="3" max="{len(columns)}" width="15" customWidth="1"/>'
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_column}{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{column_widths}</cols><sheetData>{"".join(rows)}</sheetData>'
        f'<autoFilter ref="A1:{last_column}{last_row}"/></worksheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="五分钟数据" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF238B45"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
        '</styleSheet>'
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", package_rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/styles.xml", styles)
        book.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def record_device(device_id, metadata, payload, source="local"):
    timestamp = now()
    clean_meta = strip_auth(metadata)
    device_token = (metadata or {}).get("device_token")
    with _lock:
        existing = dict(_devices.get(device_id, {}))
        existing.update({
            key: value for key, value in clean_meta.items()
            if key in {"device_name", "model", "firmware_version", "fw", "ip", "rssi", "network_type",
                       "capabilities", "server_url", "fields"} and value is not None
        })
        if device_token:
            existing["device_token"] = device_token
        existing["device_id"] = device_id
        existing["last_seen"] = timestamp
        existing["source"] = source
        _devices[device_id] = existing

        data = dict(payload or {})
        # 实时接口必须反映“这一帧”实际收到的内容。旧实现会把缺失/null 字段
        # 无限沿用上一帧，传感器暂时无新读数时页面仍显示旧值，造成实时刷新
        # 已卡死的假象。固定网格由 /schema 保持；缺值交给前端显示为 --。
        previous = _latest_by_device.get(device_id, {})
        data["_frameSeq"] = int(previous.get("_frameSeq", 0)) + 1
        data["_device_id"] = device_id
        data["_ts"] = timestamp
        _latest_by_device[device_id] = data
        history = _history_by_device.setdefault(device_id, [])
        history.append(data)
        if len(history) > HISTORY_LIMIT:
            del history[:-HISTORY_LIMIT]
        maybe_record_sample(device_id, data, timestamp)
        mark_dirty()  # 节流落盘: 不再每帧同步写盘, 由后台线程统一 flush


def normalize_push(obj, path_device_id=None):
    obj = dict(obj or {})
    envelope = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
    payload = dict(envelope or obj)
    device_id = safe_device_id(obj.get("device_id") or payload.pop("device_id", None) or path_device_id)
    # 旧固件把身份/鉴权/元数据平铺在顶层(无 payload 包裹); 这些不是传感器读数,
    # 从读数里剔除, 以免被当作可视字段渲染出来。
    for meta_key in ("token", "device_token", "device_id", "device_name", "model",
                     "firmware_version", "fw", "ip", "rssi", "capabilities",
                     "server_url", "fields", "payload", "data_source"):
        payload.pop(meta_key, None)
    metadata = {
        "device_id": device_id,
        "device_name": obj.get("device_name") or payload.pop("device_name", None),
        "model": obj.get("model"),
        "firmware_version": obj.get("firmware_version") or obj.get("fw"),
        "fw": obj.get("fw"),
        "ip": obj.get("ip"),
        "network_type": obj.get("network_type"),
        "rssi": obj.get("rssi"),
        "capabilities": obj.get("capabilities"),
        "server_url": obj.get("server_url"),
        "device_token": obj.get("device_token"),
        "fields": obj.get("fields") if isinstance(obj.get("fields"), list) else None,
    }
    return device_id, metadata, payload


load_state()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def send_json(self, code, value):
        body = json_bytes(value)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, body, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def proxy_fertigation(self, endpoint, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(FERTIGATION_URL + endpoint, data=data,
                          headers={"Content-Type": "application/json"} if data else {},
                          method="POST" if data else "GET")
        try:
            with urlopen(request, timeout=20) as response:
                self.send_json(response.status, json.loads(response.read().decode("utf-8")))
        except Exception as exc:
            self.send_json(502, {"ok": False, "error": "fertigation_service_unavailable", "message": str(exc)})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            try:
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self.send_text(404, "index.html 未找到")
            return

        if path == "/data":
            requested_device_id = query.get("device_id", [None])[0]
            with _lock:
                device_id = current_device_id(requested_device_id)
                if device_id:
                    data = dict(_latest_by_device.get(device_id, {}))
                    data["_source"] = _devices.get(device_id, {}).get("source", "unknown")
                else:
                    data = {"_ts": 0, "_age": 999999}
            data["_age"] = latest_age(data)
            with _lock:
                data["auto_model"] = dict(_auto_model_state)
            self.send_json(200, data)
            return

        if path == "/fertigation/auto/status":
            with _lock:
                self.send_json(200, dict(_auto_model_state))
            return

        if path in {"/recording/status", "/recording/export"}:
            requested_device_id = query.get("device_id", [None])[0]
            with _lock:
                device_id = current_device_id(requested_device_id)
                if path == "/recording/status":
                    self.send_json(200, recording_snapshot(device_id))
                    return
                recording = dict(_recordings_by_device.get(device_id, {})) if device_id else {}
                recording["items"] = [dict(item) for item in recording.get("items", [])]
            if not recording.get("items"):
                self.send_json(404, {"ok": False, "message": "no_recorded_data"})
                return
            body = recording_xlsx(device_id, recording)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            self.send_file(
                body,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"zhirun-data-{stamp}.xlsx",
            )
            return

        if path == "/weather":
            requested_device_id = query.get("device_id", [None])[0]
            with _lock:
                device_id = current_device_id(requested_device_id)
                latest = dict(_latest_by_device.get(device_id, {})) if device_id else {}
            latitude = latest.get("latitude")
            longitude = latest.get("longitude")
            location_source = "gnss"
            # GNSS 坐标只在获得有效定位的帧中上报。实时接口不应沿用旧值，
            # 但天气预报可以安全使用该设备最近一次有效坐标。
            if latitude is None or longitude is None:
                with _lock:
                    history = _history_by_device.get(device_id, []) if device_id else []
                    for item in reversed(history):
                        if item.get("latitude") is not None and item.get("longitude") is not None:
                            latitude = item["latitude"]
                            longitude = item["longitude"]
                            location_source = "gnss_history"
                            break
            if latitude is None or longitude is None:
                latitude = WEATHER_FALLBACK_LATITUDE
                longitude = WEATHER_FALLBACK_LONGITUDE
                location_source = "fallback"
            try:
                forecast = weather_forecast(latitude, longitude)
                forecast["location_source"] = location_source
                self.send_json(200, forecast)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.send_json(502, {"ok": False, "reason": "weather_unavailable", "message": str(exc)})
            return

        # 板载布局页 (frontend 版) 需要的 schema/config: 复用最新上报设备的字段定义。
        # 优先按 device_id 查询, 否则取最近上报的那台设备。
        if path in {"/schema", "/config"}:
            requested_device_id = query.get("device_id", [None])[0]
            with _lock:
                device_id = current_device_id(requested_device_id)
                if path == "/schema":
                    if device_id is None:
                        # Keep the dashboard useful before the first RK3506B
                        # frame arrives. Values remain empty, but the fixed
                        # schema lets the UI show which sensors are expected.
                        self.send_json(200, FIXED_FIELDS)
                        return
                    device = _devices.get(device_id, {})
                    latest = _latest_by_device.get(device_id, {})
                    # 只给板载页展示环境/传感器字段, 隐藏 ai 组
                    fields = [f for f in resolve_fields(device, latest) if f.get("group") != "ai"]
                    self.send_json(200, fields)
                    return
                # /config: 设备身份/在线状态, 给顶栏徽章用
                if device_id is None:
                    self.send_json(200, {"mode": "offline", "device_name": "环境实时监测"})
                    return
                device = _devices.get(device_id, {})
                latest = _latest_by_device.get(device_id, {})
                online = latest_age(latest) <= 15
                self.send_json(200, {
                    "mode": "normal" if online else "offline",
                    "device_id": device_id,
                    "device_name": device.get("device_name") or "环境实时监测",
                    "ssid": latest.get("wifiSsid") or device.get("ssid"),
                    "ip": latest.get("networkIp") or device.get("ip"),
                    "network_type": latest.get("networkType") or device.get("network_type"),
                    "network_interface": latest.get("networkInterface"),
                    "network_gateway": latest.get("networkGateway"),
                    "network_connected": bool(latest.get("networkConnected")),
                    "server": device.get("server_url"),
                })
                return

        if path == "/valve/config":
            with _lock:
                device_id = current_valve_device_id()
                if not device_id:
                    self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                    return
                self.send_json(200, valve_snapshot(device_id))
            return

        if path == "/fertigation/health":
            self.proxy_fertigation("/health")
            return

        if path == "/network/scan":
            with _lock:
                device_id = current_device_id()
                state = dict(_valve_by_device.get(device_id, {})) if device_id else {}
                networks = state.get("wifiNetworks", [])
                scanned_at = state.get("wifiScannedAt", 0)
            self.send_json(200, {"ok": True, "networks": networks if isinstance(networks, list) else [], "scanned_at": scanned_at})
            return

        if path.startswith("/api/devices/") and path.endswith("/valve/commands/next"):
            device_id = unquote(path.split("/")[3])
            if not authorized({"token": query.get("token", [""])[0]}, device_id):
                self.send_json(403, {"error": "bad_token"})
                return
            with _lock:
                commands = _valve_commands_by_device.get(device_id, [])
                command = commands.pop(0) if commands else None
            self.send_json(200, {"command": command})
            return

        if path == "/api/devices":
            with _lock:
                devices = [device_snapshot(device_id) for device_id in sorted(_devices)]
            devices.sort(key=lambda item: item.get("last_seen", 0), reverse=True)
            self.send_json(200, {"devices": devices, "count": len(devices), "mode": "local"})
            return

        if path.startswith("/api/devices/"):
            parts = path.split("/")
            if len(parts) >= 4:
                device_id = unquote(parts[3])
                suffix = "/".join(parts[4:])
                with _lock:
                    if device_id not in _devices and device_id not in _latest_by_device:
                        self.send_json(404, {"error": "device_not_found"})
                        return
                    if suffix == "data/latest":
                        data = dict(_latest_by_device.get(device_id, {}))
                        data["_age"] = latest_age(data)
                        self.send_json(200, data)
                        return
                    if suffix == "data/history":
                        try:
                            requested = int(query.get("limit", [120])[0])
                        except ValueError:
                            requested = 120
                        limit = min(max(requested, 1), HISTORY_LIMIT)
                        history = _history_by_device.get(device_id, [])[-limit:]
                        self.send_json(200, {"device_id": device_id, "items": history})
                        return
                    if suffix == "":
                        self.send_json(200, device_snapshot(device_id))
                        return

        self.send_text(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        obj = self.read_json()
        if obj is None:
            self.send_json(400, {"error": "bad_json"})
            return

        if path == "/fertigation/predict":
            # The model consumes the complete live environment. Start from the
            # selected device frame, then let explicit request values override
            # it for calibration or offline what-if calculations.
            with _lock:
                device_id = current_device_id(obj.get("device_id"))
                model_input = dict(_latest_by_device.get(device_id, {})) if device_id else {}
            model_input.update(obj)
            self.proxy_fertigation("/predict", model_input)
            return

        if path == "/outlet/test":
            manual_action = str(obj.get("action") or "").strip().lower()
            if manual_action not in {"open", "close"}:
                self.send_json(400, {"ok": False, "message": "invalid_outlet_test"})
                return
            run_seconds = 10.0
            if manual_action == "open":
                try:
                    run_seconds = min(180.0, max(1.0, float(obj.get("run_seconds", 10))))
                except (TypeError, ValueError):
                    self.send_json(400, {"ok": False, "message": "invalid_outlet_test"})
                    return
            with _lock:
                device_id = current_valve_device_id()
                state = _valve_by_device.get(device_id, {}) if device_id else {}
                if state.get("controllerSchema") != "four_relay_independent_flow_v1":
                    self.send_json(409, {"ok": False, "message": "four_relay_firmware_required"})
                    return
                command = queue_valve_command(device_id, "outlet_test", {
                    "manual_action": manual_action, "run_seconds": round(run_seconds, 1),
                })
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True,
                                 "command_id": command["id"], "message": "command_queued"})
            return

        if path in {"/fertigation/run", "/fertigation/stop"}:
            params = {}
            if path.endswith("/run"):
                limits = {
                    "n_target_l": 10000.0,
                    "p_target_l": 10000.0,
                    "k_target_l": 10000.0,
                    "outlet_run_s": 7200.0,
                }
                try:
                    for key, maximum in limits.items():
                        value = float(obj.get(key, 0))
                        if value < 0 or value > maximum:
                            raise ValueError(key)
                        params[key] = round(value, 3)
                except (TypeError, ValueError):
                    self.send_json(400, {"ok": False, "message": "invalid_fertigation_job"})
                    return
                if not any(params.values()):
                    self.send_json(400, {"ok": False, "message": "empty_fertigation_job"})
                    return
                action = "fertigation_start"
            else:
                action = "fertigation_stop"
            with _lock:
                device_id = current_valve_device_id()
                state = _valve_by_device.get(device_id, {}) if device_id else {}
                schema = state.get("controllerSchema")
                if action == "fertigation_start" and schema != "four_relay_independent_flow_v1":
                    self.send_json(409, {"ok": False, "message": "four_relay_firmware_required"})
                    return
                if action == "fertigation_stop" and schema != "four_relay_independent_flow_v1":
                    action = "manual"
                    params = {"manual_action": "close"}
                command = queue_valve_command(device_id, action, params)
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True,
                                 "command_id": command["id"], "message": "command_queued"})
            return

        if path in {"/recording/start", "/recording/stop"}:
            requested_device_id = obj.get("device_id")
            with _lock:
                device_id = current_device_id(requested_device_id)
                if not device_id or device_id not in _latest_by_device:
                    self.send_json(503, {"ok": False, "message": "no_device_data"})
                    return
                result = start_recording(device_id) if path.endswith("/start") else stop_recording(device_id)
            self.send_json(200, result)
            return

        if path == "/push":
            if not authorized(obj):
                self.send_text(403, "bad token")
                return
            device_id, metadata, payload = normalize_push(obj)
            source = str(obj.get("data_source") or "").strip().lower()
            if source != "rk3506":
                self.send_json(400, {"ok": False, "message": "unsupported_source"})
                return
            record_device(device_id, metadata, payload, source=source)
            self.send_json(200, {"ok": True, "device_id": device_id})
            return

        if path == "/pump/test":
            pump = str(obj.get("pump") or "").strip().lower()
            manual_action = str(obj.get("action") or "").strip().lower()
            if pump not in {"n", "p", "k"} or manual_action not in {"open", "close"}:
                self.send_json(400, {"ok": False, "message": "invalid_pump_test"})
                return
            with _lock:
                device_id = current_valve_device_id()
                state = _valve_by_device.get(device_id, {}) if device_id else {}
                if state.get("controllerSchema") not in {
                    "three_pump_test_rain_v1", "four_relay_independent_flow_v1"
                }:
                    self.send_json(409, {"ok": False, "message": "three_pump_firmware_required"})
                    return
                command = queue_valve_command(
                    device_id, "pump_test", {"pump": pump, "manual_action": manual_action}
                )
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True,
                                 "command_id": command["id"], "message": "command_queued"})
            return

        if path in {"/valve/config", "/valve/mode", "/valve/manual"}:
            with _lock:
                device_id = current_valve_device_id()
                if path == "/valve/config":
                    action = "config"
                    params = {key: obj[key] for key in (
                        "on_th", "off_th", "min_run_s", "max_run_s", "active_high",
                        "n_pulses_per_l", "p_pulses_per_l", "k_pulses_per_l",
                    ) if key in obj}
                elif path == "/valve/mode":
                    action, params = "mode", {"mode": obj.get("mode")}
                else:
                    action, params = "manual", {"manual_action": obj.get("action")}
                command = queue_valve_command(device_id, action, params)
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True, "command_id": command["id"], "message": "command_queued"})
            return

        # 远程配网：密码只保存在内存中的待执行命令里；设备取走命令后即从队列删除，
        # 不写状态文件、不返回给网页、不打印日志。
        if path == "/network/config":
            ssid = obj.get("ssid")
            password = obj.get("password")
            if not isinstance(ssid, str) or not ssid.strip() or len(ssid) > 64:
                self.send_json(400, {"ok": False, "message": "bad_ssid"})
                return
            if not isinstance(password, str) or len(password) > 128:
                self.send_json(400, {"ok": False, "message": "bad_password"})
                return
            with _lock:
                device_id = current_device_id()
                params = {"ssid": ssid.strip(), "password": password}
                for key, limit in (("radio", 8), ("auth", 32), ("bssid", 32)):
                    value = obj.get(key)
                    if isinstance(value, str) and len(value) <= limit:
                        params[key] = value
                name = obj.get("device_name")
                if isinstance(name, str) and name.strip():
                    params["device_name"] = name.strip()[:96]
                command = queue_valve_command(device_id, "network_config", params)
                if command:
                    # 只记录目标网络名和开始时间，密码始终仅存在待执行命令内存中。
                    _network_attempt_by_device[device_id] = {"ssid": ssid.strip(), "started_at": now()}
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True, "command_id": command["id"], "message": "network_config_queued"})
            return

        if path == "/network/scan":
            with _lock:
                device_id = current_device_id()
                command = queue_valve_command(device_id, "network_scan", {})
            if not command:
                self.send_json(503, {"ok": False, "message": "serial_device_offline"})
                return
            self.send_json(202, {"ok": True, "queued": True, "command_id": command["id"], "message": "network_scan_queued"})
            return

        if path.startswith("/api/devices/") and path.endswith("/valve/result"):
            device_id = unquote(path.split("/")[3])
            if not authorized(obj, device_id):
                self.send_json(403, {"error": "bad_token"})
                return
            state = obj.get("state")
            if not isinstance(state, dict):
                self.send_json(400, {"error": "missing_state"})
                return
            with _lock:
                _valve_by_device[device_id] = state
            self.send_json(200, {"ok": True})
            return

        if path.startswith("/api/devices/") and path.endswith("/push"):
            device_id = unquote(path.split("/")[3])
            if not authorized(obj, device_id):
                self.send_json(403, {"error": "bad_token"})
                return
            actual_id, metadata, payload = normalize_push(obj, device_id)
            if actual_id != device_id:
                self.send_json(400, {"error": "device_id_mismatch"})
                return
            source = str(obj.get("data_source") or "").strip().lower()
            if source != "rk3506":
                self.send_json(400, {"ok": False, "message": "unsupported_source"})
                return
            record_device(device_id, metadata, payload, source=source)
            self.send_json(200, {"ok": True, "device_id": device_id})
            return

        self.send_text(404, "not found")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path.startswith("/api/devices/"):
            self.send_text(404, "not found")
            return
        device_id = unquote(path.split("/")[3])
        obj = self.read_json()
        if obj is None:
            self.send_json(400, {"error": "bad_json"})
            return
        with _lock:
            if device_id not in _devices:
                self.send_json(404, {"error": "device_not_found"})
                return
            for key in ("device_name", "server_url", "capabilities"):
                if key in obj:
                    _devices[device_id][key] = obj[key]
            save_state()
            self.send_json(200, device_snapshot(device_id))


if __name__ == "__main__":
    save_thread = threading.Thread(target=_save_loop, name="state-saver", daemon=True)
    save_thread.start()
    if AUTO_MODEL_ENABLED:
        auto_model_thread = threading.Thread(target=_auto_model_loop, name="daily-model", daemon=True)
        auto_model_thread.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"智润服务已启动, 监听 0.0.0.0:{PORT}")
    print(f"浏览器查看: http://<本机IP>:{PORT}/")
    try:
        srv.serve_forever()
    finally:
        save_state(force=True)

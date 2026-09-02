#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智润水肥一体化策略推理服务。"""
import json
import math
import os
import sys
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)
MODEL_DIR = os.environ.get(
    "ZHIRUN_FERTIGATION_MODEL_DIR",
    (
        os.path.join(os.path.expanduser("~"), "Desktop", "灌溉模型")
        if os.name == "nt"
        else os.path.join(PROJECT_ROOT, "灌溉模型", "灌溉模型")
    ),
)
PORT = int(os.environ.get("ZHIRUN_INFER_PORT", "10001"))

_lock = threading.Lock()
_state = {
    "loaded": False,
    "loading": False,
    "ml_loaded": False,
    "error": None,
    "hint": None,
}
_package = None
_config = None
_model_class = None
_provider = None
_calendar = None
_defaults = None

# Input quality is tracked separately from the fallback values.  This keeps
# missing sensor data from silently looking like a valid regional prior.
_SOIL_RULES = {
    "soil_moisture_20_pct": (("soil_moisture_20_pct", "moisture20", "soilMoist"), 0.0, 100.0),
    "soil_ec_ds_m": (("soil_ec_ds_m", "soilEc", "soil_ec"), 0.0, 20.0),
    "soil_ph": (("soil_ph", "soilPH"), 0.0, 14.0),
    "soil_temperature_c": (("soil_temperature_c", "soilTemp"), -40.0, 70.0),
    "soil_n_mg_kg": (("soil_n_mg_kg", "soilN"), 0.0, 10000.0),
    "soil_p_mg_kg": (("soil_p_mg_kg", "soilP"), 0.0, 10000.0),
    "soil_k_mg_kg": (("soil_k_mg_kg", "soilK"), 0.0, 10000.0),
}
_ENVIRONMENT_RULES = {
    "air_temperature_c": (("air_temperature_c", "airTemp"), -50.0, 70.0),
    "air_humidity_pct": (("air_humidity_pct", "airHum"), 0.0, 100.0),
    "co2_ppm": (("co2_ppm", "co2"), 0.0, 10000.0),
    "wind_speed_m_s": (("wind_speed_m_s", "windSpeed"), 0.0, 80.0),
    "light_lux": (("light_lux", "lux"), 0.0, 500000.0),
    "rain_24h_mm": (("rain_24h_mm", "rainMm"), 0.0, 2000.0),
}


def _quality_value(body, sensor, aliases):
    """Find an explicitly supplied value without applying model defaults."""
    for key in aliases:
        if key in body and body[key] is not None:
            return body[key]
        if key in sensor and sensor[key] is not None:
            return sensor[key]
    return None


def _input_quality(body, sensor):
    missing = []
    invalid = []
    valid = {}
    ruleset = {**_SOIL_RULES, **_ENVIRONMENT_RULES}
    concentration_keys = {"n_concentration_g_l", "p_concentration_g_l", "k_concentration_g_l",
                          "a_concentration_g_l", "b_concentration_g_l", "c_concentration_g_l"}
    has_explicit_concentrations = any(key in body for key in concentration_keys)
    if has_explicit_concentrations:
        ruleset.update({
            "soil_n_mg_kg": (("soil_n_mg_kg", "soilN", "n"), 0.0, 10000.0),
            "soil_p_mg_kg": (("soil_p_mg_kg", "soilP", "p"), 0.0, 10000.0),
            "soil_k_mg_kg": (("soil_k_mg_kg", "soilK", "k"), 0.0, 10000.0),
        })
    for name, (aliases, low, high) in ruleset.items():
        value = _quality_value(body, sensor, aliases)
        if value is None:
            missing.append(name)
            continue
        try:
            number_value = float(value)
        except (TypeError, ValueError):
            invalid.append(name)
            continue
        if not math.isfinite(number_value) or not low <= number_value <= high:
            invalid.append(name)
            continue
        valid[name] = number_value

    # A single soil-moisture reading is the installed controller interface;
    # the model adapter intentionally projects it to the three root depths.
    soil_critical = set(_SOIL_RULES)
    soil_critical_missing = sorted(soil_critical.intersection(set(missing) | set(invalid)))
    nutrient_missing = sorted({
        "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg",
    }.intersection(set(missing) | set(invalid)))
    return {
        "missing": sorted(missing),
        "invalid": sorted(invalid),
        "soil_critical_missing": soil_critical_missing,
        "fertilizer_blocked": nutrient_missing,
        "valid": valid,
    }


def load_model():
    """Load the unified model API and its optional ExtraTrees package."""
    global _package, _config, _model_class, _provider, _calendar, _defaults
    with _lock:
        if _state["loaded"] or _state["loading"]:
            return _state["loaded"]
        _state.update({"loading": True, "error": None, "hint": None})
    try:
        import joblib

        scripts_dir = os.path.join(MODEL_DIR, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from fertigation_model import (
            DEFAULT_LATITUDE,
            DEFAULT_LONGITUDE,
            EnvironmentProvider,
            FertigationModel,
        )
        from train_policy_v2 import CALENDAR

        with open(os.path.join(MODEL_DIR, "configs", "crops.json"), encoding="utf-8") as handle:
            _config = json.load(handle)
        model_path = os.path.join(MODEL_DIR, "models", "hohhot_fertigation_policy_v2.joblib")
        package_error = None
        try:
            _package = joblib.load(model_path) if os.path.exists(model_path) else None
        except Exception as exc:
            _package = None
            package_error = str(exc)
        _model_class = FertigationModel
        _provider = EnvironmentProvider()
        _calendar = CALENDAR
        _defaults = (DEFAULT_LATITUDE, DEFAULT_LONGITUDE)
        with _lock:
            _state.update({
                "loaded": True,
                "loading": False,
                "ml_loaded": _package is not None,
                "error": None,
                "hint": None if _package is not None else (
                    "ExtraTrees模型不可用，当前使用可解释规则教师安全降级"
                    + (("：" + package_error) if package_error else "")
                ),
            })
        mode = "ExtraTrees + safety rules" if _package is not None else "safety rules"
        print("[infer] 水肥策略模型已加载:", mode)
        return True
    except Exception as exc:
        with _lock:
            _state.update({
                "loaded": False,
                "loading": False,
                "ml_loaded": False,
                "error": str(exc),
                "hint": "请在灌溉模型目录执行: python -m pip install -r requirements.txt",
            })
        return False


def number(body, key, default, low=None, high=None):
    value = body.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(key + " 必须为数字")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(key + " 超出允许范围")
    return value


def first_value(body, *keys, default=None):
    for key in keys:
        if key in body and body[key] is not None:
            return body[key]
    return default


def invalid_zero_soil_frame(body):
    """Detect a powered sensor whose measurement electrodes have no valid sample."""
    values = (
        first_value(body, "soil_moisture_20_pct", "moisture20", "soilMoist"),
        first_value(body, "soil_ec_ds_m", "soilEc", "soil_ec"),
        first_value(body, "soil_n_mg_kg", "n"),
        first_value(body, "soil_p_mg_kg", "p"),
        first_value(body, "soil_k_mg_kg", "k"),
    )
    if any(value is None for value in values):
        return False
    try:
        return all(float(value) <= 0 for value in values)
    except (TypeError, ValueError):
        return False


def observation_time(body, crop):
    supplied = first_value(body, "observation_time", "date")
    if supplied:
        return str(supplied)
    return datetime.now().astimezone().isoformat()


def environment_from_request(body, crop):
    base = body.get("environment")
    sensor = dict(base) if isinstance(base, dict) else {}
    quality = _input_quality(body, sensor)
    for name in quality["invalid"]:
        rules = _SOIL_RULES.get(name) or _ENVIRONMENT_RULES.get(name)
        if rules:
            for key in rules[0]:
                sensor.pop(key, None)
    latitude = number(
        {"latitude": first_value(body, "latitude", default=sensor.get("latitude", _defaults[0]))},
        "latitude", _defaults[0], -90, 90,
    )
    longitude = number(
        {"longitude": first_value(body, "longitude", default=sensor.get("longitude", _defaults[1]))},
        "longitude", _defaults[1], -180, 180,
    )
    zero_soil_frame = invalid_zero_soil_frame(body)
    mappings = {
        "air_temperature_c": ("air_temperature_c", "airTemp"),
        "air_humidity_pct": ("air_humidity_pct", "airHum"),
        "co2_ppm": ("co2_ppm", "co2"),
        "soil_temperature_c": ("soil_temperature_c", "soilTemp"),
        "soil_n_mg_kg": ("soil_n_mg_kg", "n"),
        "soil_p_mg_kg": ("soil_p_mg_kg", "p"),
        "soil_k_mg_kg": ("soil_k_mg_kg", "k"),
        "wind_speed_m_s": ("wind_speed_m_s", "windSpeed"),
        "light_lux": ("light_lux", "lux"),
        "rain_24h_mm": ("rain_24h_mm", "rainMm"),
        "soil_ph": ("soil_ph", "soilPH"),
        "soil_ec_ds_m": ("soil_ec_ds_m", "soilEc", "soil_ec"),
        "rain_next_2d_mm": ("rain_next_2d_mm", "rain_next_2d"),
        "eto_forecast_mm": ("eto_forecast_mm", "eto"),
        "weather_forecast": ("weather_forecast",),
    }
    for target, keys in mappings.items():
        if zero_soil_frame and target in {
            "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg", "soil_ph", "soil_ec_ds_m",
        }:
            continue
        value = first_value(body, *keys)
        if value is not None:
            rules = _SOIL_RULES.get(target) or _ENVIRONMENT_RULES.get(target)
            if rules and target in quality["invalid"]:
                continue
            sensor[target] = value
    moisture20 = first_value(body, "soil_moisture_20_pct", "moisture20", "soilMoist")
    if moisture20 is not None and not zero_soil_frame:
        sensor["soil_moisture_20_pct"] = moisture20
        sensor["soil_moisture_40_pct"] = first_value(
            body, "soil_moisture_40_pct", "moisture40", default=moisture20
        )
        sensor["soil_moisture_60_pct"] = first_value(
            body, "soil_moisture_60_pct", "moisture60", default=moisture20
        )
    sensor["latitude"] = latitude
    sensor["longitude"] = longitude
    sensor["observation_time"] = observation_time(body, crop)
    sensor["days_since_fertigation"] = int(number(body, "days_since_fertigation", 8, 0, 365))
    sensor["source"] = {
        **(sensor.get("source") if isinstance(sensor.get("source"), dict) else {}),
        "sensors": "ZhiRun realtime request",
        **({"soil_sensor": "invalid_zero_frame; regional prior applied"} if zero_soil_frame else {}),
        "input_quality": quality,
    }
    if zero_soil_frame:
        quality["soil_critical_missing"] = sorted(set(quality["soil_critical_missing"]) | {
            "soil_moisture_20_pct", "soil_ec_ds_m", "soil_ph",
        })
        quality["fertilizer_blocked"] = sorted(set(quality["fertilizer_blocked"]) | {
            "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg",
        })
        sensor["source"]["input_quality"] = quality
    return _provider.fetch(
        latitude,
        longitude,
        sensor_data=sensor,
        offline=bool(body.get("offline", False)),
    )


def decide(body):
    if not _state["loaded"] and not load_model():
        raise RuntimeError(_state["error"] or "模型未就绪")
    # V2's field entry intentionally exposes only three mother-liquor
    # concentrations. Crop and area are installation defaults in this model.
    crop = "玉米"
    area = 1.0
    concentrations = (
        first_value(body, "n_concentration_g_l", "a_concentration_g_l", "n"),
        first_value(body, "p_concentration_g_l", "b_concentration_g_l", "p"),
        first_value(body, "k_concentration_g_l", "c_concentration_g_l", "k"),
    )
    if any(value is None for value in concentrations):
        raise ValueError("必须提供N、P、K三种母液浓度（g/L）")
    environment = environment_from_request(body, crop)
    model = _model_class(crop=crop, area_mu=area, use_ml=_package is not None, provider=_provider)
    model._package = _package
    result = model.plan(*concentrations, environment)
    return result["decision"], result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, code, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self):
        if self.path.split("?", 1)[0].rstrip("/") in ("", "/health"):
            self.send_json(200, {
                **_state,
                "service": "zhirun-fertigation",
                "model": "hohhot_fertigation_policy_v2",
                "schema": "fertigation_v2_automatic_environment",
                "model_dir": MODEL_DIR,
                "manual_inputs": ["N_g_L", "P2O5_g_L", "K2O_g_L"],
                "crop": "玉米",
                "area_mu": 1.0,
            })
        else:
            self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path.split("?", 1)[0].rstrip("/") != "/predict":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            decision, result = decide(body)
            self.send_json(200, {"ok": True, "decision": decision, "result": result})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    threading.Thread(target=load_model, daemon=True).start()
    print("智润水肥策略服务启动, 监听 127.0.0.1:%s" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

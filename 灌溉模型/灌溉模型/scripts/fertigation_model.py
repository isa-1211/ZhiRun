"""现场水肥模型：自动环境输入 + 三种母液浓度 + 硬件工作单。

这个模块故意把「传感器/天气数据」与「人工配液浓度」分开。现场调用
``FertigationModel.plan`` 时只需要提供 N、P、K 三路母液浓度；经纬度、
天气、土壤和空气参数由 :class:`EnvironmentProvider` 或实际传感器适配层
提供。当前模型是呼和浩特区域的规则教师蒸馏模型，输出不能替代现场校准。
"""

from __future__ import annotations

import copy
import json
import math
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import pandas as pd

try:
    from .build_job import HARDWARE, build_job
    from .recommend import CONFIG, recommend
    from .train_policy_v2 import CATEGORICAL, NUMERIC, stage_for
except ImportError:  # 允许 `python scripts/run_fertigation_model.py` 直接运行
    from build_job import HARDWARE, build_job
    from recommend import CONFIG, recommend
    from train_policy_v2 import CATEGORICAL, NUMERIC, stage_for


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LATITUDE = 40.84
DEFAULT_LONGITUDE = 111.75
DEFAULT_CROP = "玉米"
DEFAULT_AREA_MU = 1.0
MODEL_PATH = ROOT / "models" / "hohhot_fertigation_policy_v2.joblib"
WEATHER_PATH = ROOT / "data" / "weather" / "hohhot_nasa_power_daily.csv"


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是数字") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}必须是有限数字")
    return result


def _nonnegative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise ValueError(f"{name}不能为负数")
    return result


@dataclass(frozen=True)
class FertilizerConcentrations:
    """三路母液中目标养分浓度，单位 g/L。

    P、K 的单位分别对应模型里的 P2O5、K2O。浓度必须来自配液记录或
    化验结果，不能使用包装袋上的总盐分浓度代替。
    """

    n_g_l: float
    p_g_l: float
    k_g_l: float

    def __post_init__(self) -> None:
        for key in ("n_g_l", "p_g_l", "k_g_l"):
            value = _finite(getattr(self, key), key)
            if value <= 0 or value > 10000:
                raise ValueError(f"{key}必须在(0, 10000] g/L范围内")
            object.__setattr__(self, key, value)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FertilizerConcentrations":
        def get(*names: str) -> Any:
            for name in names:
                if name in value:
                    return value[name]
            raise ValueError(f"缺少母液浓度字段：{'/'.join(names)}")

        return cls(get("n_g_l", "n", "N", "氮", "nitrogen_concentration_g_l"),
                   get("p_g_l", "p", "P", "磷", "p2o5_concentration_g_l"),
                   get("k_g_l", "k", "K", "钾", "k2o_concentration_g_l"))

    def as_dict(self) -> dict[str, float]:
        return {"N_g_L": self.n_g_l, "P2O5_g_L": self.p_g_l, "K2O_g_L": self.k_g_l}


@dataclass
class EnvironmentInput:
    """所有自动输入字段及其离线安全默认值。

    ``soil_*_mg_kg`` 是土壤有效养分的统一接口。真实设备可以把实验室
    mg/kg、离子选择电极或 Modbus 传感器值转换到这里；转换过程应记录在
    传感器适配层，而不是在控制器里猜单位。
    """

    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    air_temperature_c: float = 22.0
    air_humidity_pct: float = 55.0
    co2_ppm: float = 420.0
    soil_moisture_pct: float | None = None
    soil_temperature_c: float | None = None
    soil_n_mg_kg: float | None = None
    soil_p_mg_kg: float | None = None
    soil_k_mg_kg: float | None = None
    wind_speed_m_s: float = 2.0
    light_lux: float = 20000.0
    rain_24h_mm: float = 0.0
    rain_forecast_mm: float = 0.0
    rain_next_2d_mm: float | None = None
    eto_forecast_mm: float = 5.0
    soil_ph: float | None = None
    soil_ec_ds_m: float | None = None
    days_since_fertigation: int = 8
    observation_time: str | None = None
    weather_forecast: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lat = _finite(self.latitude, "latitude")
        lon = _finite(self.longitude, "longitude")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("经纬度超出范围")
        bounded = {"air_humidity_pct": (0, 100)}
        for name, (low, high) in bounded.items():
            value = _finite(getattr(self, name), name)
            if not low <= value <= high:
                raise ValueError(f"{name}必须在{low}到{high}之间")
        for name in ("co2_ppm", "soil_n_mg_kg", "soil_p_mg_kg",
                     "soil_k_mg_kg", "wind_speed_m_s", "light_lux", "rain_24h_mm",
                     "rain_forecast_mm", "eto_forecast_mm", "soil_ec_ds_m"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)
        for name, low, high in (("soil_moisture_pct", 0, 100), ("soil_ph", 0, 14)):
            value = getattr(self, name)
            if value is not None:
                value = _finite(value, name)
                if not low <= value <= high:
                    raise ValueError(f"{name}必须在{low}到{high}之间")
        if self.soil_temperature_c is not None:
            _finite(self.soil_temperature_c, "soil_temperature_c")
        if self.rain_next_2d_mm is not None:
            _nonnegative(self.rain_next_2d_mm, "rain_next_2d_mm")
        if int(self.days_since_fertigation) < 0:
            raise ValueError("days_since_fertigation不能为负数")
        numeric_fields = ("latitude", "longitude", "air_temperature_c", "air_humidity_pct", "co2_ppm",
                          "soil_moisture_pct", "soil_temperature_c", "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg",
                          "wind_speed_m_s", "light_lux", "rain_24h_mm", "rain_forecast_mm",
                          "eto_forecast_mm", "soil_ph", "soil_ec_ds_m")
        for name in numeric_fields:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name))
        if self.rain_next_2d_mm is not None:
            object.__setattr__(self, "rain_next_2d_mm", _finite(self.rain_next_2d_mm, "rain_next_2d_mm"))
        object.__setattr__(self, "days_since_fertigation", int(self.days_since_fertigation))

    @property
    def rain_2d(self) -> float:
        return float(self.rain_next_2d_mm if self.rain_next_2d_mm is not None else self.rain_forecast_mm)

    def forecast_summary(self, horizon_days: int = 2) -> dict[str, float | int | str]:
        """Aggregate the forecast that is actually fed into the decision.

        Forecast records may come from Open-Meteo, a local adapter, or a test
        fixture. Missing fields fall back to the current sensor value, while
        rainfall and ET0 retain their explicit ``rain_next_2d_mm`` and
        ``eto_forecast_mm`` fallbacks.
        """
        horizon = max(1, int(horizon_days))
        records = [item for item in self.weather_forecast[:horizon] if isinstance(item, Mapping)]

        def values(*names: str) -> list[float]:
            result: list[float] = []
            for item in records:
                for name in names:
                    value = item.get(name)
                    if value is not None:
                        try:
                            number = float(value)
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(number):
                            result.append(number)
                            break
            return result

        rain = values("rain_mm", "precipitation_sum", "precipitation_mm")
        eto = values("eto_mm", "et0_fao_evapotranspiration")
        tmax = values("tmax_c", "temperature_2m_max")
        tmin = values("tmin_c", "temperature_2m_min")
        tmean = values("tmean_c", "temperature_2m_mean")
        humidity = values("humidity_pct", "relative_humidity_2m_mean")
        wind = values("wind_speed_m_s", "wind_speed_10m_max")
        light = values("light_lux", "shortwave_radiation_sum")
        if not tmean and tmax and tmin:
            tmean = [(high + low) / 2 for high, low in zip(tmax, tmin)]
        predicted_tmax = max(tmax) if tmax else self.air_temperature_c
        predicted_tmin = min(tmin) if tmin else self.air_temperature_c
        predicted_tmean = float(np.mean(tmean)) if tmean else self.air_temperature_c
        return {
            "horizon_days": horizon,
            "temperature_mean_c": predicted_tmean,
            "temperature_max_c": predicted_tmax,
            "temperature_min_c": predicted_tmin,
            "humidity_mean_pct": float(np.mean(humidity)) if humidity else self.air_humidity_pct,
            "wind_max_m_s": max(wind) if wind else self.wind_speed_m_s,
            "light_mean_lux": float(np.mean(light)) if light else self.light_lux,
            "rain_next_2d_mm": float(sum(rain)) if rain else self.rain_2d,
            "eto_next_2d_mm": float(sum(eto)) if eto else self.eto_forecast_mm * horizon,
            "eto_daily_mm": float(np.mean(eto)) if eto else self.eto_forecast_mm,
            "forecast_records_used": len(records),
        }

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["rain_2d"] = self.rain_2d
        value["forecast_summary"] = self.forecast_summary()
        return value

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EnvironmentInput":
        """Create a snapshot from English, Chinese, or common sensor names."""
        raw = dict(data)
        current = raw.pop("current", None)
        if isinstance(current, Mapping):
            current_map = {
                "temperature_2m": "air_temperature_c", "relative_humidity_2m": "air_humidity_pct",
                "wind_speed_10m": "wind_speed_m_s", "shortwave_radiation": "light_lux",
                "soil_temperature_0cm": "soil_temperature_c", "time": "observation_time",
            }
            for source, target in current_map.items():
                if source in current and target not in raw:
                    value = current[source]
                    if source == "shortwave_radiation" and value is not None:
                        value = float(value) * 120.0
                    raw[target] = value
        if isinstance(raw.get("daily"), Mapping) and "weather_forecast" not in raw:
            raw["weather_forecast"] = raw.pop("daily")
        if isinstance(raw.get("location"), Mapping):
            raw.setdefault("latitude", raw["location"].get("latitude"))
            raw.setdefault("longitude", raw["location"].get("longitude"))
        aliases = {
            "纬度": "latitude", "经度": "longitude", "temperature": "air_temperature_c", "humidity": "air_humidity_pct",
            "co2": "co2_ppm", "soil_temp": "soil_temperature_c", "soil_n": "soil_n_mg_kg",
            "soil_p": "soil_p_mg_kg", "soil_k": "soil_k_mg_kg", "wind": "wind_speed_m_s",
            "light": "light_lux", "rain_24h": "rain_24h_mm", "ph": "soil_ph",
            "soil_moisture": "soil_moisture_pct", "空气温度": "air_temperature_c",
            "空气温度_c": "air_temperature_c", "空气湿度": "air_humidity_pct",
            "空气湿度_pct": "air_humidity_pct", "CO2浓度": "co2_ppm", "二氧化碳": "co2_ppm",
            "土壤温度": "soil_temperature_c", "土壤湿度": "soil_moisture_pct",
            "土壤氮": "soil_n_mg_kg", "土壤氮浓度": "soil_n_mg_kg",
            "土壤磷": "soil_p_mg_kg", "土壤磷浓度": "soil_p_mg_kg",
            "土壤钾": "soil_k_mg_kg", "土壤钾浓度": "soil_k_mg_kg",
            "风速": "wind_speed_m_s", "光照强度": "light_lux", "24h雨量": "rain_24h_mm",
            "24小时雨量": "rain_24h_mm", "土壤ph值": "soil_ph", "土壤pH": "soil_ph",
            "天气预报": "weather_forecast", "预报": "weather_forecast",
        }
        for source, target in aliases.items():
            if source in raw and target not in raw:
                raw[target] = raw[source]
        moisture = raw.pop("soil_moisture_pct", None)
        npk = raw.pop("soil_npk", raw.pop("土壤氮磷钾", None))
        if isinstance(npk, Mapping):
            for source, target in (("n", "soil_n_mg_kg"), ("N", "soil_n_mg_kg"), ("氮", "soil_n_mg_kg"),
                                   ("p", "soil_p_mg_kg"), ("P", "soil_p_mg_kg"), ("磷", "soil_p_mg_kg"),
                                   ("k", "soil_k_mg_kg"), ("K", "soil_k_mg_kg"), ("钾", "soil_k_mg_kg")):
                if source in npk:
                    raw.setdefault(target, npk[source])
        if moisture is not None:
            if isinstance(moisture, Mapping) or isinstance(moisture, (list, tuple)):
                raise ValueError("仅支持单个土壤探针值 soil_moisture_pct")
            raw["soil_moisture_pct"] = moisture
        forecast = raw.get("weather_forecast")
        if isinstance(forecast, Mapping):
            rain_values = forecast.get("precipitation_sum", forecast.get("rain_mm", []))
            eto_values = forecast.get("et0_fao_evapotranspiration", forecast.get("eto_mm", []))
            if isinstance(rain_values, (list, tuple)) and rain_values:
                raw.setdefault("rain_forecast_mm", float(rain_values[0] or 0))
                raw.setdefault("rain_next_2d_mm", float(sum(float(x or 0) for x in rain_values[:2])))
            if isinstance(eto_values, (list, tuple)) and eto_values:
                raw.setdefault("eto_forecast_mm", float(eto_values[0] or 0))
        elif isinstance(forecast, (list, tuple)) and forecast:
            rain_values = [float(item.get("rain_mm", item.get("precipitation_mm", 0)) or 0)
                           for item in forecast if isinstance(item, Mapping)]
            eto_values = [float(item.get("eto_mm", 0) or 0) for item in forecast if isinstance(item, Mapping)]
            if rain_values:
                raw.setdefault("rain_forecast_mm", rain_values[0])
                raw.setdefault("rain_next_2d_mm", sum(rain_values[:2]))
            if eto_values:
                raw.setdefault("eto_forecast_mm", eto_values[0])
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        values = {key: value for key, value in raw.items() if key in allowed}
        return cls(**values)


def _fallback_weather() -> dict[str, float]:
    try:
        frame = pd.read_csv(WEATHER_PATH, parse_dates=["date"]).replace(-999, np.nan).dropna()
        # The cache ends in winter 2025. Select the latest record near today's
        # day-of-year so an offline August run does not inherit a December
        # temperature merely because it is the last row in the file.
        target_doy = datetime.now().timetuple().tm_yday
        frame["doy_distance"] = (frame["date"].dt.dayofyear - target_doy).abs()
        frame = frame.sort_values(["doy_distance", "date"])
        frame = frame.iloc[0]
        return {
            "air_temperature_c": float(frame.T2M), "air_humidity_pct": float(frame.RH2M),
            "wind_speed_m_s": float(frame.WS2M), "rain_24h_mm": float(frame.PRECTOTCORR),
            "light_lux": max(0.0, float(frame.ALLSKY_SFC_SW_DWN)) * 120.0,
        }
    except (FileNotFoundError, ValueError, IndexError, AttributeError):
        return {}


class EnvironmentProvider:
    """Merge live weather and installed-sensor overrides.

    Network failures intentionally fall back to the last locally retrieved
    weather row. A production adapter should mark stale data and let the PLC
    refuse a run when its freshness policy is exceeded.
    """

    def __init__(self, timeout_s: float = 8.0):
        self.timeout_s = timeout_s

    def _open_meteo(self, latitude: float, longitude: float) -> dict[str, Any]:
        params = {
            "latitude": latitude, "longitude": longitude, "timezone": "Asia/Shanghai",
            "forecast_days": 3,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,precipitation,soil_temperature_0cm",
            "daily": "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,wind_speed_10m_max,shortwave_radiation_sum",
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "hohhot-fertigation-model/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            payload = json.load(response)
        current = payload.get("current", {})
        daily = payload.get("daily", {})
        rain = [float(x or 0) for x in daily.get("precipitation_sum", [])]
        eto = [float(x or 0) for x in daily.get("et0_fao_evapotranspiration", [])]
        dates = daily.get("time", [])
        def series(name: str) -> list[Any]:
            values = list(daily.get(name, []))
            return values + [None] * max(0, len(dates) - len(values))

        daily_tmax = series("temperature_2m_max")
        daily_tmin = series("temperature_2m_min")
        daily_humidity = series("relative_humidity_2m_mean")
        daily_wind = series("wind_speed_10m_max")
        daily_light = series("shortwave_radiation_sum")
        forecast = [{"date": date, "rain_mm": rain[index] if index < len(rain) else 0.0,
                     "eto_mm": eto[index] if index < len(eto) else 0.0,
                     "tmax_c": daily_tmax[index], "tmin_c": daily_tmin[index],
                     "humidity_pct": daily_humidity[index], "wind_speed_m_s": daily_wind[index],
                     "light_lux": None if daily_light[index] is None else float(daily_light[index]) * 1000.0}
                    for index, date in enumerate(dates)]
        return {
            "observation_time": current.get("time"),
            "air_temperature_c": current.get("temperature_2m"),
            "air_humidity_pct": current.get("relative_humidity_2m"),
            "wind_speed_m_s": current.get("wind_speed_10m"),
            "light_lux": None if current.get("shortwave_radiation") is None else float(current["shortwave_radiation"]) * 120.0,
            # The daily sum is the 24-hour precipitation estimate. The
            # current precipitation field is only the current-hour value.
            "rain_24h_mm": rain[0] if rain else current.get("precipitation", 0.0),
            "rain_forecast_mm": rain[0] if rain else 0.0,
            "rain_next_2d_mm": sum(rain[1:3]) if len(rain) > 1 else (rain[0] if rain else 0.0),
            "eto_forecast_mm": eto[0] if eto else 5.0,
            "soil_temperature_c": current.get("soil_temperature_0cm"),
            "weather_forecast": forecast,
        }

    def fetch(self, latitude: float = DEFAULT_LATITUDE, longitude: float = DEFAULT_LONGITUDE,
              sensor_data: Mapping[str, Any] | None = None, offline: bool = False) -> EnvironmentInput:
        weather = _fallback_weather()
        data: dict[str, Any] = {
            "latitude": latitude, "longitude": longitude,
            "source": {"soil": "等待已安装土壤探针真实数据"},
            **weather,
        }
        if not offline:
            try:
                remote = self._open_meteo(latitude, longitude)
                data.update({key: value for key, value in remote.items() if value is not None})
                data["source"] = {**data["source"], "weather": "Open-Meteo实时预报"}
            except (OSError, ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError):
                data["source"] = {**data["source"], "weather": "本地NASA POWER缓存（网络不可用）"}
        else:
            data["source"] = {**data["source"], "weather": "本地NASA POWER缓存（offline）"}
        if sensor_data:
            # Installed sensor values are the only soil inputs accepted here.
            overrides = dict(sensor_data)
            overrides.setdefault("source", {})
            if isinstance(overrides["source"], Mapping):
                data["source"] = {**data.get("source", {}), **overrides.pop("source")}
            data.update(overrides)
        return EnvironmentInput.from_mapping(data)


def _soil_levels(environment: EnvironmentInput) -> tuple[str, str, str]:
    def level(value: float | None, low: float, high: float) -> str:
        if value is None:
            return "unknown"
        if value < low:
            return "low"
        if value >= high:
            return "high"
        return "medium"

    # Thresholds are conservative regional mappings. Replace with local lab
    # calibration when a field-specific soil test is available.
    return (level(environment.soil_n_mg_kg, 1000, 1800),
            level(environment.soil_p_mg_kg, 15, 30),
            level(environment.soil_k_mg_kg, 100, 220))


def _input_quality(environment: EnvironmentInput) -> dict[str, Any]:
    source = environment.source if isinstance(environment.source, Mapping) else {}
    quality = source.get("input_quality", {})
    return dict(quality) if isinstance(quality, Mapping) else {}


def _observation_doy(value: str | None) -> int:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timetuple().tm_yday
        except ValueError:
            pass
    return datetime.now().timetuple().tm_yday


def dynamic_irrigation_threshold(stage_cfg: Mapping[str, Any], environment: EnvironmentInput,
                                 forecast: Mapping[str, Any] | None = None) -> dict[str, float]:
    """Adjust direct single-probe moisture setpoints from live weather."""
    summary = dict(forecast or environment.forecast_summary())
    base_trigger = float(stage_cfg["trigger_moisture_pct"])
    base_target = float(stage_cfg["target_moisture_pct"])
    heat_delta = 0.02 * max(0.0, float(summary["temperature_max_c"]) - 28.0) / 5.0
    eto_delta = 0.02 * max(0.0, float(summary["eto_daily_mm"]) - 5.0) / 2.0
    rain_delta = 1.2 * min(float(summary["rain_next_2d_mm"]), 10.0) / 10.0
    adjustment = heat_delta + eto_delta - rain_delta
    trigger = float(np.clip(base_trigger + adjustment, max(0.0, base_trigger - 3.0), min(100.0, base_trigger + 4.0)))
    target = float(np.clip(base_target + max(0.0, adjustment) * 0.5, trigger + 0.5, 100.0))
    return {
        "base_trigger_moisture_pct": round(base_trigger, 2),
        "dynamic_trigger_moisture_pct": round(trigger, 2),
        "base_target_moisture_pct": round(base_target, 2),
        "dynamic_target_moisture_pct": round(target, 2),
        "adjustment_moisture_pct": round(trigger - base_trigger, 2),
        "heat_adjustment_moisture_pct": round(heat_delta, 2),
        "eto_adjustment_moisture_pct": round(eto_delta, 2),
        "rain_adjustment_moisture_pct": round(-rain_delta, 2),
    }


class FertigationModel:
    """Generate a decision and a hardware-safe work order.

    ``crop`` and ``area_mu`` are installation settings, not per-run manual
    inputs. They default to maize and one mu so the run form only exposes the
    three mother-liquor concentrations requested by the user.
    """

    def __init__(self, crop: str = DEFAULT_CROP, area_mu: float = DEFAULT_AREA_MU,
                 soil_profile: str | None = None, use_ml: bool = True,
                 model_path: Path = MODEL_PATH, provider: EnvironmentProvider | None = None):
        if crop not in CONFIG["crops"]:
            raise ValueError(f"未知作物：{crop}")
        if area_mu <= 0:
            raise ValueError("area_mu必须大于0")
        self.crop, self.area_mu, self.soil_profile = crop, float(area_mu), soil_profile
        self.use_ml, self.model_path, self.provider = use_ml, Path(model_path), provider or EnvironmentProvider()
        self._package: dict[str, Any] | None = None

    def _load_package(self) -> dict[str, Any] | None:
        if not self.use_ml:
            return None
        if self._package is None and self.model_path.exists():
            try:
                self._package = joblib.load(self.model_path)
            except (OSError, ValueError, EOFError):
                self._package = None
        return self._package

    def _concentrations(self, n: Any, p: Any = None, k: Any = None) -> FertilizerConcentrations:
        if isinstance(n, FertilizerConcentrations):
            return n
        if isinstance(n, Mapping):
            return FertilizerConcentrations.from_mapping(n)
        if p is None or k is None:
            raise ValueError("必须提供N、P、K三种母液浓度（g/L）")
        return FertilizerConcentrations(n, p, k)

    def _feature_row(self, environment: EnvironmentInput, stage: str,
                     soil_levels: tuple[str, str, str],
                     n_remaining: float, p_remaining: float, k_remaining: float,
                     forecast: Mapping[str, Any], threshold: Mapping[str, Any]) -> dict[str, Any]:
        stage_cfg = CONFIG["crops"][self.crop]["stages"][stage]
        date = datetime.now() if not environment.observation_time else datetime.fromisoformat(environment.observation_time.replace("Z", "+00:00"))
        # A lux-to-radiation conversion is only a feature mapping; use a PAR
        # sensor calibration table in production for the installed light meter.
        radiation = environment.light_lux / 120.0
        # Use the forecast summary for decision features. Current sensor values
        # remain available in ``automatic_inputs`` for audit and safety checks.
        t = float(forecast["temperature_mean_c"])
        levels = {"low": 1.0, "medium": 0.65, "high": 0.0, "unknown": 0.0}
        return {
            "crop": self.crop, "stage": stage, "soil_n_level": soil_levels[0], "soil_p_level": soil_levels[1],
            "soil_k_level": soil_levels[2], "doy": date.timetuple().tm_yday,
            "t_mean": t, "t_max": float(forecast["temperature_max_c"]), "t_min": float(forecast["temperature_min_c"]),
            "rh": float(forecast["humidity_mean_pct"]),
            "wind": float(forecast["wind_max_m_s"]), "radiation": float(forecast["light_mean_lux"]) / 120.0,
            "rain_today": environment.rain_24h_mm,
            "rain_next_2d": float(forecast["rain_next_2d_mm"]), "eto": float(forecast["eto_daily_mm"]),
            "gdd10_14d": max(0.0, t - 10) * 14, "rain_7d": environment.rain_24h_mm,
            "eto_7d": float(forecast["eto_next_2d_mm"]) * 3.5,
            "dry_days": 0 if float(forecast["rain_next_2d_mm"]) >= 1 else 7,
            "soil_moisture_pct": environment.soil_moisture_pct if environment.soil_moisture_pct is not None else 0.0,
            "moisture_trigger_pct": threshold["dynamic_trigger_moisture_pct"],
            "moisture_target_pct": threshold["dynamic_target_moisture_pct"],
            "soil_ec": environment.soil_ec_ds_m if environment.soil_ec_ds_m is not None else 0.0,
            "soil_ph": environment.soil_ph if environment.soil_ph is not None else 0.0,
            "days_since_fertigation": environment.days_since_fertigation,
            "n_applied_stage": 0.0, "p_applied_stage": 0.0, "k_applied_stage": 0.0,
            "kc": stage_cfg["kc"], "n_remaining": n_remaining,
            "p_remaining": p_remaining, "k_remaining": k_remaining, "n_level_factor": levels[soil_levels[0]],
            "p_level_factor": levels[soil_levels[1]], "k_level_factor": levels[soil_levels[2]],
            "fertilizer_interval_ready": int(environment.days_since_fertigation >= 7),
            "ec_block": int(environment.soil_ec_ds_m is not None and environment.soil_ec_ds_m >= 2.0),
            # Kept in the row for a future sensor-aware retraining package.
            "latitude": environment.latitude, "longitude": environment.longitude,
            "co2_ppm": environment.co2_ppm, "soil_temperature_c": environment.soil_temperature_c if environment.soil_temperature_c is not None else 0.0,
            "soil_n_mg_kg": environment.soil_n_mg_kg if environment.soil_n_mg_kg is not None else 0.0,
            "soil_p_mg_kg": environment.soil_p_mg_kg if environment.soil_p_mg_kg is not None else 0.0,
            "soil_k_mg_kg": environment.soil_k_mg_kg if environment.soil_k_mg_kg is not None else 0.0,
            "light_lux": environment.light_lux,
            "rain_24h_mm": environment.rain_24h_mm,
            "forecast_temperature_mean_c": float(forecast["temperature_mean_c"]),
            "forecast_temperature_max_c": float(forecast["temperature_max_c"]),
            "forecast_humidity_mean_pct": float(forecast["humidity_mean_pct"]),
            "forecast_wind_max_m_s": float(forecast["wind_max_m_s"]),
            "forecast_light_mean_lux": float(forecast["light_mean_lux"]),
            "forecast_rain_next_2d_mm": float(forecast["rain_next_2d_mm"]),
            "forecast_eto_daily_mm": float(forecast["eto_daily_mm"]),
        }

    def plan(self, n_concentration_g_l: Any, p_concentration_g_l: Any = None,
             k_concentration_g_l: Any = None, environment: EnvironmentInput | Mapping[str, Any] | None = None) -> dict[str, Any]:
        # Convenience form: ``plan({"N": ..., "P": ..., "K": ...}, env)``.
        if environment is None and k_concentration_g_l is None and isinstance(p_concentration_g_l, (EnvironmentInput, Mapping)):
            environment, p_concentration_g_l = p_concentration_g_l, None
        concentrations = self._concentrations(n_concentration_g_l, p_concentration_g_l, k_concentration_g_l)
        if environment is None:
            environment = self.provider.fetch()
        elif isinstance(environment, Mapping):
            environment = EnvironmentInput.from_mapping(environment)
        if not isinstance(environment, EnvironmentInput):
            raise TypeError("environment必须是EnvironmentInput或字典")

        doy = _observation_doy(environment.observation_time)
        stage = stage_for(self.crop, doy)
        soil_levels = _soil_levels(environment)
        if stage is None:
            decision = {
                "crop": self.crop, "stage": "休耕/非生育期", "irrigation_m3_mu": 0.0,
                "nitrogen_kg_mu": 0.0, "p2o5_kg_mu": 0.0, "k2o_kg_mu": 0.0,
                "fertigate": False, "irrigate": False,
                "execution_status": "not_needed",
                "execution_reason": "当前日期不在配置的作物生育期",
                "alerts": ["当前日期不在配置的作物生育期，硬件保持关闭"],
                "model": "season-safety-gate",
            }
            job = build_job(self.area_mu, decision, self._runtime_hardware(concentrations))
            return self._result(environment, concentrations, decision, job, soil_levels, None)

        quality = _input_quality(environment)
        soil_blockers = [str(item) for item in quality.get("soil_critical_missing", []) if item]
        fertilizer_blockers = [str(item) for item in quality.get("fertilizer_blocked", []) if item]
        if environment.soil_moisture_pct is None:
            soil_blockers.append("soil_moisture_pct")
        if environment.soil_ph is None:
            soil_blockers.append("soil_ph")
        for name, value in (("soil_n_mg_kg", environment.soil_n_mg_kg),
                            ("soil_p_mg_kg", environment.soil_p_mg_kg),
                            ("soil_k_mg_kg", environment.soil_k_mg_kg)):
            if value is None:
                fertilizer_blockers.append(name)
        soil_blockers = sorted(set(soil_blockers))
        fertilizer_blockers = sorted(set(fertilizer_blockers))
        if soil_blockers:
            reason = "关键土壤输入缺失或异常，拒绝自动灌溉：" + ", ".join(soil_blockers)
            decision = {
                "crop": self.crop, "stage": stage, "irrigate": False, "irrigation_m3_mu": 0.0,
                "fertigate": False, "nitrogen_kg_mu": 0.0, "p2o5_kg_mu": 0.0, "k2o_kg_mu": 0.0,
                "execution_status": "safety_blocked", "execution_reason": reason,
                "input_quality": quality, "alerts": [reason], "model": "input-quality-gate",
            }
            job = build_job(self.area_mu, decision, self._runtime_hardware(concentrations))
            return self._result(environment, concentrations, decision, job, soil_levels, None)

        stage_cfg = CONFIG["crops"][self.crop]["stages"][stage]
        forecast = environment.forecast_summary(horizon_days=2)
        threshold = dynamic_irrigation_threshold(stage_cfg, environment, forecast)
        crop_cfg = CONFIG["crops"][self.crop]
        n_remaining = crop_cfg["season_n_kg_mu"] * stage_cfg["n_share"]
        p_remaining = crop_cfg["season_p2o5_kg_mu"] * stage_cfg["p_share"]
        k_remaining = crop_cfg["season_k2o_kg_mu"] * stage_cfg["k_share"]
        row = self._feature_row(environment, stage, soil_levels, n_remaining, p_remaining, k_remaining, forecast, threshold)
        teacher = recommend(self.crop, stage, environment.soil_moisture_pct,
                            float(forecast["rain_next_2d_mm"]), float(forecast["eto_daily_mm"]),
                            environment.days_since_fertigation, environment.soil_ec_ds_m, 0, 0, 0,
                            *soil_levels, trigger_moisture_override=threshold["dynamic_trigger_moisture_pct"],
                            target_moisture_override=threshold["dynamic_target_moisture_pct"])
        package = self._load_package()
        prediction = None
        if package is not None:
            try:
                features = package.get("features", CATEGORICAL + NUMERIC)
                prediction = np.maximum(0, package["pipeline"].predict(pd.DataFrame([row])[features])[0]).tolist()
            except (KeyError, ValueError, TypeError):
                prediction = None
        if prediction is None:
            prediction = [teacher["irrigation_m3_mu"], teacher["nitrogen_kg_mu"], teacher["p2o5_kg_mu"], teacher["k2o_kg_mu"]]

        irrigation_demand = bool(teacher["irrigate"])
        physical_gate = irrigation_demand
        wind_block = float(forecast["wind_max_m_s"]) > 10.0
        if wind_block:
            physical_gate = False
        water = min(25.0, float(prediction[0])) if physical_gate and prediction[0] >= 0.5 else 0.0
        safety_alerts = list(teacher.get("alerts", []))
        if wind_block:
            safety_alerts.append("风速超过10 m/s，暂停灌溉以避免飘移")
        ph_block = not 5.0 <= environment.soil_ph <= 8.8
        cold_block = environment.soil_temperature_c is not None and environment.soil_temperature_c < 5.0
        fertilizer_gate = (
            water > 0 and (environment.soil_ec_ds_m is None or environment.soil_ec_ds_m < 2.0)
            and environment.days_since_fertigation >= 7 and not ph_block and not cold_block
            and not fertilizer_blockers
        )
        if ph_block:
            safety_alerts.append("土壤pH超出5.0-8.8注肥安全范围")
        if cold_block:
            safety_alerts.append("土壤温度低于5°C，暂停注肥")
        if fertilizer_blockers:
            safety_alerts.append("N/P/K 土壤养分输入缺失或异常，本次仅允许灌水，不允许施肥")
        nutrients = [float(x) for x in prediction[1:4]] if fertilizer_gate else [0.0, 0.0, 0.0]
        for index, level in enumerate(soil_levels):
            if level == "high":
                nutrients[index] = 0.0
        nutrients[0] = min(nutrients[0], n_remaining, 2.5)
        nutrients[1] = min(nutrients[1], p_remaining, 1.5)
        nutrients[2] = min(nutrients[2], k_remaining, 3.0)
        nutrients = [0.0 if x < 0.05 else round(x, 2) for x in nutrients]
        if water > 0:
            execution_status = "ready"
            execution_reason = "灌溉条件和安全条件均已满足，可审核后执行"
        elif irrigation_demand and wind_block:
            execution_status = "safety_blocked"
            execution_reason = "存在灌溉需求，但预报风速超过10 m/s，安全门暂停执行"
        elif not irrigation_demand:
            execution_status = "not_needed"
            execution_reason = (
                f"单个土壤探针水分为{environment.soil_moisture_pct:.1f}%，"
                f"高于本阶段{threshold['dynamic_trigger_moisture_pct']:.1f}%的灌溉触发线"
            )
        else:
            execution_status = "below_minimum"
            execution_reason = "模型建议灌水量低于0.5 m³/亩的最小执行量"
        decision = {
            "crop": self.crop, "stage": stage, "irrigate": water > 0, "irrigation_m3_mu": round(water, 1),
            "fertigate": any(nutrients), "nitrogen_kg_mu": nutrients[0], "p2o5_kg_mu": nutrients[1],
            "k2o_kg_mu": nutrients[2], "soil_moisture_pct": round(environment.soil_moisture_pct, 2),
            "execution_status": execution_status, "execution_reason": execution_reason,
            "base_trigger_moisture_pct": threshold["base_trigger_moisture_pct"],
            "dynamic_trigger_moisture_pct": threshold["dynamic_trigger_moisture_pct"],
            "dynamic_target_moisture_pct": threshold["dynamic_target_moisture_pct"],
            "threshold_adjustment_moisture_pct": threshold["adjustment_moisture_pct"],
            "soil_n_level": soil_levels[0],
            "soil_p_level": soil_levels[1], "soil_k_level": soil_levels[2], "model": "hohhot-fertigation-policy-v2",
            "model_prediction": [round(float(x), 4) for x in prediction],
            "input_quality": quality,
            "predicted_environment": forecast, "alerts": safety_alerts,
            "confidence": "区域先验迁移基线，需用本地田间数据校准",
        }
        job = build_job(self.area_mu, decision, self._runtime_hardware(concentrations))
        return self._result(environment, concentrations, decision, job, soil_levels, row)

    def _runtime_hardware(self, concentrations: FertilizerConcentrations) -> dict[str, Any]:
        hardware = copy.deepcopy(HARDWARE)
        values = {"A": concentrations.n_g_l, "B": concentrations.p_g_l, "C": concentrations.k_g_l}
        for valve, concentration in values.items():
            hardware["fertilizer_lines"][valve]["concentration_g_l"] = concentration
        return hardware

    def _result(self, environment: EnvironmentInput, concentrations: FertilizerConcentrations,
                decision: dict[str, Any], job: dict[str, Any], soil_levels: tuple[str, str, str],
                row: dict[str, Any] | None) -> dict[str, Any]:
        pumps = {"N_PUMP": {"enabled": False, "target_solution_l": 0.0},
                 "P_PUMP": {"enabled": False, "target_solution_l": 0.0},
                 "K_PUMP": {"enabled": False, "target_solution_l": 0.0}}
        valve_by_pump = {"N_PUMP": "A", "P_PUMP": "B", "K_PUMP": "C"}
        for dose in job["doses"]:
            pump = dose["pump"]
            if pump in pumps:
                pumps[pump] = {"enabled": True, "target_solution_l": dose["target_solution_l"],
                               "target_nutrient_kg": dose["target_nutrient_kg"],
                               "concentration_g_l": dose["concentration_g_l"], "flow_meter": dose["flow_meter"]}
        return {
            "manual_inputs": {"unit": "g/L", **concentrations.as_dict()},
            "automatic_inputs": environment.as_dict(),
            "decision": decision,
            "hardware": {
                "pumps": pumps,
                "flow_meters": {
                    dose["element"]: {"id": dose["flow_meter"], "pump": dose["pump"],
                                      "pulses_per_liter": dose["pulses_per_liter"]}
                    for dose in job["doses"]
                },
                "outlet_pump": {"enabled_after_all_fertilizers": bool(job["outlet_run_s"]),
                                "duration_s": job["outlet_run_s"],
                                "target_water_l": job["target_main_water_l"]},
                "phase_outputs": [{"state": phase["state"], "outputs": phase.get("open_outputs", [])}
                                  for phase in job["phases"]],
            },
            "soil_npk_levels": {"N": soil_levels[0], "P": soil_levels[1], "K": soil_levels[2]},
            "job": job,
            "data_provenance": {
                "location": "呼和浩特区域",
                "training": "NASA POWER历史天气 + ISRIC SoilGrids区域先验 + 作物阶段规则映射",
                "sensor_mapping": "有效养分mg/kg按区域阈值映射low/medium/high；光照lux按120换算为W/m²特征",
                "limitations": "没有本地水肥-产量标签，当前输出是安全的迁移基线，不是增产保证",
            },
            "model_features": row,
        }


__all__ = ["EnvironmentInput", "EnvironmentProvider", "FertilizerConcentrations", "FertigationModel"]

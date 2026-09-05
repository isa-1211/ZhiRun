"""按农田坐标获取天气预报；土壤数据必须来自现场传感器。"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def get_json(base: str, params: dict) -> dict:
    url = base + "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.load(response)


def fetch(latitude: float, longitude: float) -> dict:
    forecast = get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": latitude, "longitude": longitude, "timezone": "Asia/Shanghai", "forecast_days": 7,
        "daily": "et0_fao_evapotranspiration,precipitation_sum,temperature_2m_max,temperature_2m_min",
    })
    return {
        "location": {"latitude": latitude, "longitude": longitude},
        "forecast": forecast["daily"],
        "sources": {
            "forecast": "Open-Meteo (FAO ET0 and weather forecast)",
            "soil": "未获取；运行时必须使用现场土壤传感器",
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--latitude", type=float, required=True)
    p.add_argument("--longitude", type=float, required=True)
    args = p.parse_args()
    payload = fetch(args.latitude, args.longitude)
    out = ROOT / "data" / "environment"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"field_{args.latitude:.5f}_{args.longitude:.5f}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存 {path}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

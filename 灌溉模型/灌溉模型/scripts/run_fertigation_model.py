"""现场入口：手动只输入 N/P/K 母液浓度，输出硬件工作单 JSON。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .fertigation_model import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, EnvironmentProvider, FertigationModel
except ImportError:
    from fertigation_model import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, EnvironmentProvider, FertigationModel


CROPS = ("玉米", "马铃薯", "甜菜", "向日葵")


def main() -> None:
    parser = argparse.ArgumentParser(description="呼和浩特自动环境输入水肥控制模型")
    parser.add_argument("--crop", choices=CROPS, default="玉米", help="种植作物；生育阶段按当前日期和该作物日历自动计算")
    parser.add_argument("--n-concentration", "--n", dest="n", type=float, required=True, help="氮母液浓度，目标养分 g/L")
    parser.add_argument("--p-concentration", "--p", dest="p", type=float, required=True, help="磷(P2O5)母液浓度，目标养分 g/L")
    parser.add_argument("--k-concentration", "--k", dest="k", type=float, required=True, help="钾(K2O)母液浓度，目标养分 g/L")
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE, help="可由定位模块自动替换，默认呼和浩特")
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE, help="可由定位模块自动替换，默认呼和浩特")
    parser.add_argument("--sensor-file", type=Path, help="现场传感器适配层导出的JSON；不传则用自动天气+土壤先验")
    parser.add_argument("--offline", action="store_true", help="不访问网络，使用本地NASA POWER和SoilGrids缓存")
    parser.add_argument("--no-ml", action="store_true", help="仅运行可解释规则教师，适合模型文件不可用时的安全降级")
    args = parser.parse_args()

    sensor_data = None
    if args.sensor_file:
        payload = json.loads(args.sensor_file.read_text(encoding="utf-8"))
        sensor_data = payload.get("environment", payload) if isinstance(payload, dict) else payload
    provider = EnvironmentProvider()
    environment = provider.fetch(args.latitude, args.longitude, sensor_data=sensor_data, offline=args.offline)
    result = FertigationModel(crop=args.crop, use_ml=not args.no_ml, provider=provider).plan(args.n, args.p, args.k, environment)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""使用单个土壤探针和实时环境输入运行 V2 水肥策略。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

try:
    from .fertigation_model import FertigationModel
except ImportError:
    from fertigation_model import FertigationModel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soil-moisture", type=float, required=True, help="单个土壤探针百分比")
    parser.add_argument("--soil-ph", type=float, required=True)
    parser.add_argument("--soil-n", type=float, required=True, help="有效氮 mg/kg")
    parser.add_argument("--soil-p", type=float, required=True, help="有效磷 mg/kg")
    parser.add_argument("--soil-k", type=float, required=True, help="有效钾 mg/kg")
    parser.add_argument("--soil-temperature", type=float, required=True)
    parser.add_argument("--soil-ec", type=float)
    parser.add_argument("--n-concentration", type=float, required=True)
    parser.add_argument("--p-concentration", type=float, required=True)
    parser.add_argument("--k-concentration", type=float, required=True)
    args = parser.parse_args()
    environment = {
        "soil_moisture_pct": args.soil_moisture, "soil_ph": args.soil_ph,
        "soil_n_mg_kg": args.soil_n, "soil_p_mg_kg": args.soil_p, "soil_k_mg_kg": args.soil_k,
        "soil_temperature_c": args.soil_temperature, "soil_ec_ds_m": args.soil_ec,
        "observation_time": datetime.now().astimezone().isoformat(),
    }
    result = FertigationModel().plan(args.n_concentration, args.p_concentration,
                                     args.k_concentration, environment)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

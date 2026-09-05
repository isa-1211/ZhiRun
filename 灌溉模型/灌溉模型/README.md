# 呼和浩特水肥一体化V2策略模型

面向呼和浩特露地马铃薯、甜菜、玉米和向日葵，包含三部分：

1. NASA POWER真实逐日气象和四作物阶段参数；
2. 39,600条环境—作物—传感器情景训练的ExtraTrees多输出策略模型；
3. N/P/K 三路独立流量计与四路泵组联锁状态机。

## 快速开始

```powershell
python -m pip install -r requirements.txt
python scripts/train_policy_v2.py
python scripts/predict_policy_v2.py --soil-moisture 35 --soil-ph 7.0 `
  --soil-n 1200 --soil-p 20 --soil-k 160 --soil-temperature 20 `
  --n-concentration 100 --p-concentration 80 --k-concentration 120
python scripts/simulate_controller.py
```

## 现场自动输入入口

现场运行时人工只填写三种母液中目标养分浓度（单位 g/L）。天气由 Open-Meteo 获取，
网络不可用时回退到 `data/weather` 的 NASA POWER 缓存；土壤水分、土温、pH、
有效 N/P/K 必须来自已安装传感器，缺失或异常时自动决策保持安全拦截。

```powershell
python scripts/run_fertigation_model.py --n 100 --p 80 --k 120
python scripts/run_fertigation_model.py --n 100 --p 80 --k 120 --offline `
  --sensor-file configs/sensor_snapshot.example.json
```

`configs/sensor_snapshot.example.json` 中的传感器字段覆盖空气温湿度、CO2、
单个土壤探针水分、土温、土壤 N/P/K、风速、光照、24 小时雨量和 pH；
实际项目应由 Modbus/串口适配器按同一 JSON 字段提供，而不是让操作员每次手填。

输出 JSON 的 `hardware.phase_outputs` 是给 PLC/ESP32 适配层的控制表：

1. `N_PUMP`、`P_PUMP`、`K_PUMP` 可并行运行，分别使用 `N_FLOW`、`P_FLOW`、`K_FLOW`；
2. 每路累计达到自己的目标体积时立即单独停泵；
3. 三路投料全部完成后才允许 `OUTLET_PUMP` 启动。

控制器在任一运行肥泵无流量、超时、故障或急停时关闭四路输出。

模型现在会使用天气预报前两天摘要参与决策：最高/平均温度、平均湿度、最大风速、
光照、未来两天降雨和日 ET0 会映射到模型特征。天气只对单探针水分触发值做有限调整，
预报降雨会降低触发值并由降雨安全门抑制灌溉；具体值写入
`decision.dynamic_trigger_moisture_pct`。

获取地块环境先验：

```powershell
python scripts/fetch_environment.py --latitude 40.72 --longitude 111.55
```

独立测试水量R²为0.9748，N/P/K分别为0.8529/0.8690/0.7863，灌溉判断准确率93.47%。这些分数表示模型对物理—农艺教师策略的复现能力，不代表真实增产效果或产量最优精度。

训练方法和边界见 [模型报告.md](模型报告.md)，管路、流量计和安全联锁见 [硬件与模型实施说明.md](硬件与模型实施说明.md)。

# ZhiRun RK3506B Irrigation System

Current hardware baseline: RK3506B + USB-RS485 + ESP32-S3. The public server,
dashboard, and fertigation V2 inference service all use this baseline.

农田环境监测、水肥决策与定量投料项目。RK3506B 采集 RS485 传感器并通过 USB 串口控制 ESP32-S3，ESP32-S3 根据三只独立流量计闭环控制 N/P/K 泵，再联锁启动混合罐出口泵。

## Architecture

```text
Browser <-> Public server <-> Wi-Fi <-> RK3506B <-> USB/CH341 <-> ESP32-S3
                                      |                         |-- N/P/K pumps + 3 flow meters
                                      |                         +-- mixing-tank outlet pump
                                      +-> USB-RS485 environmental/rain sensors
```

The RK3506B collector reads the USB-RS485 bus and uploads directly to the
public server over Wi-Fi or Ethernet. The ESP32 is the authoritative relay/pump
controller; the RK3506B forwards sensor and control messages. The complete
Python/ExtraTrees model runs only on the public server.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `server/` | Public dashboard, API relay, and fertigation inference service |
| `edge/` | RK3506B sensor collector and USB serial/RS485 transport |
| `esp32_pump_controller/` | PlatformIO ESP32-S3 four-relay flow-control firmware |
| `fertigation_model/` | Fertigation decision model, scripts, test cases, and datasets |
| `docs/` | RK3506B wiring and deployment notes |
| `tools/` | Windows network/NAT helper scripts |
| `deploy/` | Server units and RK3506B BusyBox init script |
| `.env.rk3506.example` | RK3506B configuration template without secrets |

The existing `灌溉模型/灌溉模型/` directory contains the fertigation model source. It is retained at its current path for compatibility; treat it as the `fertigation_model` component described above.

## Local Setup

1. Copy `.env.rk3506.example` to `/etc/zhirun-rk3506.env` on the RK3506B and supply deployment-specific values.
2. Build the ESP32 firmware:

```powershell
cd esp32_pump_controller
pio run
```

3. Run the public server:

```powershell
python server/zhirun_server.py
```

4. Install `edge/rk3506_collector.py` and `deploy/zhirun-rk3506-collector.init`
   on the board, then enable the init script. The model is deployed only on
   the public server using `deploy/zhirun-infer.service`.

The RK3506B configuration must set `ZHIRUN_SERVER` to the public service.
Verify the runtime route with:

```bash
ip route get 8.145.49.45
wget -qO- http://8.145.49.45/data
```

The RK3506B image uses BusyBox init rather than systemd. Keep the board's
static Ethernet address for local management, and confirm that public traffic
selects `wlan0`. The Ethernet interface must not install the public default
route.

## Campus Wi-Fi

The network page recognizes the open `IMAU` SSID as the Inner Mongolia
Agricultural University captive network. Select `IMAU`, enter the campus
account and authentication password, and choose **认证连接**. The RK3506B
obtains a fresh DHCP lease and completes the school's SRun Portal login with
`ac_id=6`. It checks the public connection every 30 seconds and automatically
authenticates again after a Wi-Fi or Portal session drop.

Campus credentials are never persisted by the public server. The board stores
the active campus profile at `/userdata/zhirun-campus.json` with mode `0600` so
it can recover after reboot.

## Security

Do not commit a real `.env` file, SSH keys, Wi-Fi passwords, device tokens, firmware images, or PlatformIO `.pio` build directories. The `.gitignore` excludes these paths by default.

## Verification

```powershell
cd "灌溉模型/灌溉模型"
python -m pytest tests
```

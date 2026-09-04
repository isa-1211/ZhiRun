# RK3506B LVGL HMI Demo

官方教程确认 HD-RK3506-HMI 的默认图形应用是 LVGL，板端已有：

- `/usr/lib/liblvgl.so`
- `/usr/lib/liblv_drivers.so`
- DRM 显示设备 `/dev/dri/card0`
- 触摸输入 `/dev/input/event0`（厂商 demo 的输入设备可能因固件配置显示为 event2）
- 800x480 屏幕

本项目新增 `hmi_demo/`，按官方“LVGL 程序单独编译”方式组织。它不运行浏览器，显示内容来自现有前端的核心字段：

1. 空气温度、空气湿度、CO2 和光照；
2. 土壤水分、温度、pH 和氮磷钾；
3. 风速和雨量；
4. 灌溉泵状态及触摸控制。

模型仍只在服务器运行，板端 demo 只通过 HTTP 读取 `/data` 和 `/valve/config`。

## 编译与安装

需要官方 `rk3506_linux6.1_sdk` 的交叉工具链和 LVGL port 源码。仓库不包含厂商工具链。编译前确认官方 port 提供：

```c
void lv_port_disp_init(void);
void lv_port_indev_init(void);
```

在 SDK 的 sysroot 中执行：

```sh
make CC=<官方交叉编译器> LVGL_PREFIX=<目标sysroot>/usr \\
  CFLAGS='-DHMI_SERVER_HOST=\"服务器IP\" -DHMI_SERVER_PORT=80'
```

将生成的 `zhirun_hmi_demo` 复制到板端 `/oem/usr/bin/`。确认手动运行正常后，再把 `hmi_demo/start_demo.sh` 配置到厂商开机启动位置。不要在未验证前停止 `/usr/bin/lv_demo` 或直接修改 `/etc/init.d/pre_init/S10lv_demo`。

## 现场前置条件

- 板端能访问服务器 HTTP 端口 80；
- 服务器已启动 `zhirun_server.py` 和 `infer_server.py`；
- RK3506B 已运行采集器并有 `rk3506` 设备数据；
- 触摸设备节点和官方 LVGL input port 配置一致；
- 服务器 URL 和端口在编译宏中替换为实际地址。

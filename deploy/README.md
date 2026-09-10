# Production services

The runtime is split between the RK3506B edge controller, the ESP32 pump
controller, and the public server.

## Public server

The Ubuntu server runs the dashboard/API and fertigation inference service.
Install the source under `/opt/zhirun`, create the `zhirun` service user, then
install and enable the units:

```bash
install -m 0644 deploy/zhirun-server.service deploy/zhirun-infer.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zhirun-server.service zhirun-infer.service
```

Install `deploy/nginx-zhirun.conf` as an Nginx site so the board can use the
standard HTTP port. Set a non-empty `ZHIRUN_PUSH_TOKEN` in
`/etc/zhirun/server.env`; device upload and binding-code access are rejected
when it is absent.

Copy the account configuration from `deploy/server.env.example` into the same
file. Production login additionally requires an SMS delivery webhook and an
approved WeChat Open Platform website application. Use HTTPS and set
`ZHIRUN_AUTH_COOKIE_SECURE=1` before exposing account login publicly. Preserve
the configured SQLite database across deployments and include it in backups.

## RK3506B controller

The board uses BusyBox init. Install the collector, configuration, and init
script as follows:

```sh
mkdir -p /oem/usr/bin /oem/usr/lib/modules
install -m 0644 ch341.ko /oem/usr/lib/modules/ch341.ko
install -m 0755 edge/rk3506_collector.py /oem/usr/bin/rk3506_collector.py
install -m 0600 .env.rk3506.example /etc/zhirun-rk3506.env
install -m 0755 deploy/zhirun-ch341.init /etc/init.d/S03zhirun-ch341
install -m 0755 deploy/zhirun-rk3506-collector.init /etc/init.d/S98zhirun-collector
/etc/init.d/S03zhirun-ch341 start
/etc/init.d/S98zhirun-collector start
```

Adjust the device token, serial ports, Modbus addresses, and server URL in
`/etc/zhirun-rk3506.env` before starting the service. The complete model stays
on the public server; the controller only performs acquisition and transport.
The supplied CH341 module is kernel-release specific; verify its `vermagic`
matches the running kernel before installation.

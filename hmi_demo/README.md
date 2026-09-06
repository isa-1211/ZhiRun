# RK3506B HMI Demo

This is a native LVGL screen for the HD-RK3506-HMI board. It mirrors the
important parts of the web frontend without running a browser:

- air temperature and humidity, CO2, and light;
- soil moisture, temperature, pH, nitrogen, phosphorus, and potassium;
- wind speed and rainfall;
- N/P/K dosing-pump and mixing-tank outlet-pump states;
- server-side model work-order generation from live sensor/weather data;
- editable N/P2O5/K2O solution concentrations, compact work-order review,
  explicit execution through `/fertigation/run`, and touch stop through
  `/fertigation/stop`;
- offline Wi-Fi setup from the Network page: scan nearby SSIDs, tap a network
  in the touch list, enter its password with the on-screen keyboard, and
  connect without the server. Open networks can be connected without a
  password; the list and page can be scrolled on the 800x480 display.

The full model remains on the server. The board uses `/data`, `/weather`, and
`/valve/config` for status. Its Model page requests the existing
`/fertigation/predict?compact=1` response so the 98 MB target does not need to
parse the full inference document. Generating a work order never starts a pump;
the operator must tap `EXECUTE WORK ORDER` after reviewing an executable result.

## Official port assumptions

The vendor tutorial documents two build paths: build in the RK3506 SDK, or
build the LVGL application separately. The board image provides
`/usr/lib/liblvgl.so` and `/usr/lib/liblv_drivers.so`; the vendor display/input
port supplies `lv_port_disp_init()` and `lv_port_indev_init()`.

Build this directory inside the vendor SDK or with the vendor cross compiler:

```sh
make CC=arm-linux-gnueabihf-gcc LVGL_PREFIX=/path/to/target/sysroot/usr
```

The exact compiler prefix and include directory must come from the official
`rk3506_linux6.1_sdk` toolchain. The repository does not guess or bundle a
toolchain.

## Board installation

Copy the executable to `/oem/usr/bin/zhirun_hmi_demo` or another persistent
application directory, then replace the vendor demo startup command only
after the binary has been verified manually:

```sh
/oem/usr/bin/zhirun_hmi_demo
```

The default model/API host is `8.145.49.45:80`. Override it at compile
time with `-DHMI_SERVER_HOST=\"your.server.ip\"` and
`-DHMI_SERVER_PORT=80`.

The board boot service plays the visual RGB565 frame sequence and matching PCM WAV
derived from `assets/zhirun_boot_animation.mp4` before starting this application.
They are deployed to `/userdata/zhirun/zhirun_boot_frames.rgb565` and
`/userdata/zhirun/zhirun_boot_audio.wav`; see
`docs/RK3506B_扬声器接线与开机动画.md` for the speaker wiring and audio test.

Do not stop `/usr/bin/lv_demo` or edit `/etc/init.d/pre_init/S10lv_demo` until
the vendor SDK build and the UART/network wiring have been confirmed.

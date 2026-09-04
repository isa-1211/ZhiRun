import os
import time
from pathlib import Path

import paramiko


PROJECT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
PASSWORD = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=10,
              look_for_keys=False, allow_agent=False)
    _, out, _ = c.exec_command(
        "/etc/init.d/S99zhirun-hmi stop 2>/dev/null || true; "
        "rm -rf /tmp/zhirun-boot-new /tmp/zhirun_boot_assets.tgz.new"
    )
    out.channel.recv_exit_status()
    time.sleep(2)
    s = c.open_sftp()
    s.put(str(PROJECT / "downloads" / "zhirun_hmi_demo"), "/tmp/zhirun_hmi_demo.new")
    s.put(str(PROJECT / "downloads" / "liblvgl.so"), "/tmp/liblvgl.so.new")
    s.put(str(PROJECT / "assets" / "zhirun_boot_assets.tgz"), "/userdata/zhirun_boot_assets.tgz.new")
    s.put(str(PROJECT / "deploy" / "zhirun-hmi.init"), "/tmp/S99zhirun-hmi")
    s.put(str(PROJECT / "deploy" / "zhirun-hmi-preinit"), "/tmp/S10lv_demo")
    s.close()
    cmd = (
        "test -e /userdata/zhirun_hmi_demo.pre-rain || "
        "cp -p /oem/usr/bin/zhirun_hmi_demo /userdata/zhirun_hmi_demo.pre-rain; "
        "test -e /userdata/S10lv_demo.pre-zhirun || "
        "cp -p /etc/init.d/pre_init/S10lv_demo /userdata/S10lv_demo.pre-zhirun; "
        "install -m 755 /tmp/zhirun_hmi_demo.new /oem/usr/bin/zhirun_hmi_demo; "
        "install -m 644 /tmp/liblvgl.so.new /oem/usr/lib/liblvgl.so; "
        "rm -rf /userdata/zhirun-boot-new; mkdir -p /userdata/zhirun-boot-new; "
        "tar -xzf /userdata/zhirun_boot_assets.tgz.new -C /userdata/zhirun-boot-new; "
        "test $(wc -c < /userdata/zhirun-boot-new/zhirun_boot_frames.rgb565) -eq 13824000; "
        "mkdir -p /userdata/zhirun; "
        "rm -f /oem/usr/share/zhirun/zhirun_boot_frames.rgb565 "
        "/oem/usr/share/zhirun/zhirun_boot_animation.gif "
        "/oem/usr/share/zhirun/zhirun_boot_animation.mp4 "
        "/oem/usr/share/zhirun/zhirun_boot_audio.mp3; "
        "rm -f /userdata/zhirun/zhirun_boot_frames.rgb565 "
        "/userdata/zhirun/zhirun_boot_audio.mp3 /userdata/zhirun/zhirun_boot_audio.wav; "
        "install -m 644 /userdata/zhirun-boot-new/zhirun_boot_frames.rgb565 /userdata/zhirun/zhirun_boot_frames.rgb565; "
        "install -m 644 /userdata/zhirun-boot-new/zhirun_boot_audio.wav /userdata/zhirun/zhirun_boot_audio.wav; "
        "rm -rf /userdata/zhirun-boot-new /userdata/zhirun_boot_assets.tgz.new; sync; "
        "install -m 755 /tmp/S99zhirun-hmi /etc/init.d/S99zhirun-hmi; "
        "install -m 755 /tmp/S10lv_demo /etc/init.d/pre_init/S10lv_demo; sync"
    )
    _, out, err = c.exec_command(cmd)
    output = out.read().decode(errors="replace")
    error = err.read().decode(errors="replace")
    code = out.channel.recv_exit_status()
    if code:
        print(output)
        print(error)
        raise RuntimeError("HMI file installation failed")

    # The display/audio stack can briefly trigger the OOM killer on this
    # 98 MB board. Do not keep the deployment shell sleeping there: launch
    # the restart independently, wait locally, then verify in a fresh shell.
    _, out, err = c.exec_command(
        "nohup /etc/init.d/S99zhirun-hmi restart "
        ">/tmp/zhirun-hmi-restart.log 2>&1 </dev/null &"
    )
    out.channel.recv_exit_status()
    time.sleep(12)
    verify = (
        "ps | grep zhirun_hmi_demo | grep -v grep; "
        "echo DRM_PID=$(fuser /dev/dri/card0 2>/dev/null); "
        "sed -n '1,120p' /tmp/zhirun_hmi.log"
    )
    _, out, err = c.exec_command(verify)
    output = out.read().decode(errors="replace")
    error = err.read().decode(errors="replace")
    code = out.channel.recv_exit_status()
    print(output)
    print(error)
    if code or "zhirun_hmi_demo" not in output:
        raise RuntimeError("HMI deployment failed")
    c.close()

if __name__ == "__main__":
    main()

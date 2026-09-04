import os
from pathlib import Path

import paramiko


PROJECT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
PASSWORD = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")


def run(client, command, timeout=30):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError("%s\n%s" % (output, error))
    return output + error


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        password=PASSWORD,
        timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        sftp = client.open_sftp()
        sftp.put(str(PROJECT / "edge" / "rk3506_collector.py"), "/tmp/rk3506_collector.py")
        sftp.put(str(PROJECT / "tools" / "board_serial_role_probe.py"), "/tmp/board_serial_role_probe.py")
        sftp.close()
        print(run(client, "python3 -m py_compile /tmp/rk3506_collector.py /tmp/board_serial_role_probe.py"))
        print(run(client, (
            "/etc/init.d/S98zhirun-collector stop; "
            "cp -p /etc/zhirun-rk3506.env /userdata/zhirun-rk3506.env.before-sensor-stability 2>/dev/null || true; "
            "for kv in 'ZHIRUN_MODBUS_TIMEOUT_S=0.6' 'ZHIRUN_SENSOR_RETRY_COUNT=3' 'ZHIRUN_SENSOR_HOLD_S=30'; do "
            "key=${kv%%=*}; if grep -q \"^${key}=\" /etc/zhirun-rk3506.env; then sed -i \"s/^${key}=.*/${kv}/\" /etc/zhirun-rk3506.env; else echo \"${kv}\" >> /etc/zhirun-rk3506.env; fi; done; "
            "cp -p /oem/usr/bin/rk3506_collector.py /userdata/rk3506_collector.py.before-usb-role-detect; "
            "install -m 755 /tmp/rk3506_collector.py /oem/usr/bin/rk3506_collector.py; "
            "python3 /tmp/board_serial_role_probe.py; "
            "/etc/init.d/S98zhirun-collector start; "
            "sleep 4; ps | grep '[r]k3506_collector.py'; "
            "tail -40 /userdata/zhirun-rk3506-collector.log"
        ), timeout=40))
    finally:
        client.close()


if __name__ == "__main__":
    main()

import io
import os
from pathlib import Path

import paramiko


PROJECT = Path(__file__).resolve().parent.parent
SERVER_HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
SERVER_PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]
BOARD_HOST = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
BOARD_PASSWORD = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")


def connect(host, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username="root",
        password=password,
        timeout=15,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def push_token():
    server = connect(SERVER_HOST, SERVER_PASSWORD)
    sftp = server.open_sftp()
    try:
        with sftp.open("/etc/zhirun/server.env", "r") as env_file:
            lines = env_file.read().decode("utf-8", "replace").splitlines()
    except FileNotFoundError:
        lines = []
    finally:
        sftp.close()
        server.close()
    for line in lines:
        if line.startswith("ZHIRUN_PUSH_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main():
    token = push_token()
    config = "\n".join((
        "ZHIRUN_SERVER=http://8.145.49.45",
        "ZHIRUN_TOKEN=" + token,
        "ZHIRUN_DEVICE_ID=rk3506b-01",
        "ZHIRUN_DEVICE_NAME=RK3506B fertigation controller",
        "ZHIRUN_RS485_PORT=/dev/ttyUSB0",
        "ZHIRUN_RS485_BAUD=4800",
        "ZHIRUN_MODBUS_TIMEOUT_S=0.6",
        "ZHIRUN_SENSOR_RETRY_COUNT=3",
        "ZHIRUN_SENSOR_HOLD_S=30",
        "ZHIRUN_ESP_SERIAL_PORT=/dev/ttyS1",
        "ZHIRUN_ESP_SERIAL_BAUD=115200",
        "ZHIRUN_POLL_INTERVAL_S=0.5",
        "ZHIRUN_PUSH_INTERVAL_S=5",
        "ZHIRUN_SOIL_ADDR=2",
        "ZHIRUN_TH_ADDR=1",
        "ZHIRUN_CO2_ADDR=3",
        "ZHIRUN_LIGHT_ADDR=5",
        "ZHIRUN_WIND_ADDR=4",
        "ZHIRUN_WIND_REG=0",
        "ZHIRUN_RAIN_ADDR=6",
        "ZHIRUN_RAIN_REG=0",
        "ZHIRUN_RAIN_FUNCTION=3",
        "ZHIRUN_RAIN_SCALE=0.1",
        "ZHIRUN_RAIN_MM_PER_TIP=0.3",
        "",
    )).encode("utf-8")

    board = connect(BOARD_HOST, BOARD_PASSWORD)
    _, stdout, stderr = board.exec_command("mkdir -p /oem/usr/lib/modules /oem/usr/bin")
    stdout.read()
    directory_error = stderr.read().decode(errors="replace")
    if directory_error:
        raise RuntimeError(directory_error)
    sftp = board.open_sftp()
    uploads = (
        (PROJECT / "downloads" / "ch341.ko", "/oem/usr/lib/modules/ch341.ko", 0o644),
        (PROJECT / "edge" / "rk3506_collector.py", "/oem/usr/bin/rk3506_collector.py", 0o755),
        (PROJECT / "deploy" / "zhirun-ch341.init", "/etc/init.d/S03zhirun-ch341", 0o755),
        (PROJECT / "deploy" / "zhirun-rk3506-collector.init", "/etc/init.d/S98zhirun-collector", 0o755),
    )
    for local, remote, mode in uploads:
        sftp.put(str(local), remote)
        sftp.chmod(remote, mode)
    sftp.putfo(io.BytesIO(config), "/etc/zhirun-rk3506.env")
    sftp.chmod("/etc/zhirun-rk3506.env", 0o600)
    sftp.close()

    command = (
        "/etc/init.d/S03zhirun-ch341 start; "
        "/etc/init.d/S98zhirun-collector restart; sleep 14; "
        "echo DEVICE; ls -l /dev/ttyUSB* 2>/dev/null; "
        "echo PROCESS; ps | grep rk3506_collector | grep -v grep; "
        "echo LOG; tail -60 /userdata/zhirun-rk3506-collector.log; "
        "echo DATA; wget -T 5 -qO- http://8.145.49.45/data; echo"
    )
    _, stdout, stderr = board.exec_command(command, timeout=40)
    print(stdout.read().decode(errors="replace"))
    error = stderr.read().decode(errors="replace")
    if error:
        print(error)
    code = stdout.channel.recv_exit_status()
    board.close()
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

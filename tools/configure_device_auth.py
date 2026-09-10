"""Synchronize a private device token across the server and RK3506B board."""
from __future__ import annotations

import io
import os
import secrets
import time
from pathlib import Path

import paramiko


SERVER_HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
SERVER_PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]
BOARD_HOST = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
BOARD_PASSWORD = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")
PROJECT = Path(__file__).resolve().parent.parent


def connect(host: str, password: str):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username="root", password=password, timeout=15, look_for_keys=False, allow_agent=False)
    return client


def run(client, command: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f"remote command failed ({code}): {error.strip()}")
    return output.strip()


def replace_environment(source: str, key: str, value: str) -> str:
    lines = source.splitlines()
    replaced = False
    result = []
    for line in lines:
        if line.strip().startswith(key + "="):
            result.append(f"{key}={value}")
            replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"{key}={value}")
    return "\n".join(result) + "\n"


def main():
    token = secrets.token_urlsafe(48)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    binary = PROJECT / "downloads" / "zhirun_hmi_demo"
    if not binary.exists():
        raise FileNotFoundError(binary)

    server = connect(SERVER_HOST, SERVER_PASSWORD)
    server_sftp = server.open_sftp()
    try:
        with server_sftp.open("/etc/zhirun/server.env", "r") as handle:
            server_env = handle.read().decode("utf-8")
        with server_sftp.open("/tmp/zhirun-server.env.device-auth", "w") as handle:
            handle.write(replace_environment(server_env, "ZHIRUN_PUSH_TOKEN", token))
        server_sftp.chmod("/tmp/zhirun-server.env.device-auth", 0o600)
    finally:
        server_sftp.close()
    run(server, f"cp -a /etc/zhirun/server.env /opt/zhirun/backups/server.env.before-device-auth-{stamp}")
    run(server, "install -o root -g zhirun -m 0640 /tmp/zhirun-server.env.device-auth /etc/zhirun/server.env")
    run(server, "systemctl restart zhirun-server.service; sleep 3; systemctl is-active zhirun-server.service")
    server.close()

    board = connect(BOARD_HOST, BOARD_PASSWORD)
    board_sftp = board.open_sftp()
    try:
        with board_sftp.open("/etc/zhirun-rk3506.env", "r") as handle:
            board_env = handle.read().decode("utf-8")
        board_sftp.putfo(
            io.BytesIO(replace_environment(board_env, "ZHIRUN_TOKEN", token).encode("utf-8")),
            "/tmp/zhirun-rk3506.env.device-auth",
        )
        board_sftp.put(str(binary), "/tmp/zhirun_hmi_demo.device-auth")
        board_sftp.chmod("/tmp/zhirun-rk3506.env.device-auth", 0o600)
        board_sftp.chmod("/tmp/zhirun_hmi_demo.device-auth", 0o755)
    finally:
        board_sftp.close()
    run(board, f"cp -p /etc/zhirun-rk3506.env /userdata/zhirun-rk3506.env.before-device-auth-{stamp}")
    run(board, f"cp -p /oem/usr/bin/zhirun_hmi_demo /userdata/zhirun_hmi_demo.before-device-auth-{stamp}")
    run(board, "install -m 0600 /tmp/zhirun-rk3506.env.device-auth /etc/zhirun-rk3506.env")
    run(board, "install -m 0755 /tmp/zhirun_hmi_demo.device-auth /oem/usr/bin/zhirun_hmi_demo")
    run(
        board,
        "/etc/init.d/S98zhirun-collector restart; "
        "nohup /etc/init.d/S99zhirun-hmi restart >/tmp/zhirun-hmi-auth-restart.log 2>&1 </dev/null &",
    )
    time.sleep(12)
    status = run(
        board,
        "ps | grep -E 'rk3506_collector|zhirun_hmi_demo' | grep -v grep; "
        "tail -8 /userdata/zhirun-rk3506-collector.log; tail -8 /tmp/zhirun_hmi.log",
    )
    board.close()
    if "rk3506_collector" not in status or "zhirun_hmi_demo" not in status:
        raise RuntimeError("board services did not restart")
    print(status)
    print("DEVICE_AUTH_SYNC=OK")
    print(f"BACKUP_STAMP={stamp}")


if __name__ == "__main__":
    main()

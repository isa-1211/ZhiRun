"""Deploy only the public app-version endpoint and web server code.

This intentionally leaves the server data, auth database, model service, and
device configuration untouched. Set ZHIRUN_BUILD_PASSWORD before running.
"""
import datetime as dt
import os
from pathlib import Path

import paramiko


HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]
PROJECT = Path(__file__).resolve().parent.parent


def run(client, command, timeout=45):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    if output:
        print(output, end="")
    if error:
        print(error, end="")
    if code:
        raise RuntimeError(f"remote command failed with exit code {code}")


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        password=PASSWORD,
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    remote_tmp = f"/tmp/zhirun_server_{stamp}.py"
    backup_dir = f"/opt/zhirun/server/backups/app-version-{stamp}"
    sftp = client.open_sftp()
    try:
        sftp.put(str(PROJECT / "server" / "zhirun_server.py"), remote_tmp)
    finally:
        sftp.close()

    run(client, f"/opt/zhirun/.venv/bin/python -m py_compile {remote_tmp}")
    run(
        client,
        f"mkdir -p {backup_dir}; cp -p /opt/zhirun/server/zhirun_server.py {backup_dir}/zhirun_server.py; "
        f"install -o zhirun -g zhirun -m 0644 {remote_tmp} /opt/zhirun/server/zhirun_server.py; "
        "systemctl restart zhirun-server.service; sleep 3; "
        "systemctl is-active zhirun-server.service; "
        "curl -fsS http://127.0.0.1:10000/app/version; echo",
    )
    client.close()
    print("DONE — app version endpoint deployed; auth/data/model files were not changed")


if __name__ == "__main__":
    main()

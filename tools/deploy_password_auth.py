"""Deploy password-only authentication to the production ZhiRun service."""
from __future__ import annotations

import http.client
import json
import os
import posixpath
import secrets
import shlex
import time
from pathlib import Path

import paramiko


HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
SSH_PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]
ADMIN_USERNAME = os.environ.get("ZHIRUN_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ZHIRUN_ADMIN_PASSWORD") or ("Zr7!" + secrets.token_urlsafe(12))
DEVICE_ID = os.environ.get("ZHIRUN_INITIAL_DEVICE", "rk3506b-01")
PROJECT = Path(__file__).resolve().parent.parent
REMOTE_SERVER = "/opt/zhirun/server"
REMOTE_ENV = "/etc/zhirun/server.env"
AUTH_DB = "/var/lib/zhirun/auth.sqlite3"


def merge_environment(source: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    result = []
    for raw in source.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}")
                continue
        result.append(raw)
    if result and result[-1]:
        result.append("")
    result.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(result) + "\n"


def run(client, command: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f"remote command failed ({code}): {error.strip()}")
    return output.strip()


def public_verification():
    connection = http.client.HTTPConnection(HOST, 80, timeout=12)
    connection.request("GET", "/auth/config")
    response = connection.getresponse()
    config = json.loads(response.read())
    assert response.status == 200 and config["auth_mode"] == "password"
    connection.request("GET", "/data")
    response = connection.getresponse()
    response.read()
    assert response.status == 401
    body = json.dumps({"account": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    connection.request("POST", "/auth/login/password", body, {"Content-Type": "application/json"})
    response = connection.getresponse()
    login = json.loads(response.read())
    assert response.status == 200 and DEVICE_ID in {item["device_id"] for item in login["devices"]}
    cookie = response.getheader("Set-Cookie").split(";", 1)[0]
    connection.request("GET", "/data", headers={"Cookie": cookie, "X-ZhiRun-Device": DEVICE_ID})
    response = connection.getresponse()
    response.read()
    assert response.status == 200
    connection.close()


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        password=SSH_PASSWORD,
        timeout=15,
        look_for_keys=False,
        allow_agent=False,
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"/opt/zhirun/backups/password-auth-{stamp}"
    auth_secret = ""
    sftp = client.open_sftp()
    try:
        with sftp.open(REMOTE_ENV, "r") as handle:
            current_env = handle.read().decode("utf-8")
        for raw in current_env.splitlines():
            if raw.startswith("ZHIRUN_AUTH_SECRET="):
                auth_secret = raw.split("=", 1)[1].strip()
                break
        if not auth_secret:
            auth_secret = secrets.token_urlsafe(48)
        updated_env = merge_environment(current_env, {
            "ZHIRUN_AUTH_SECRET": auth_secret,
            "ZHIRUN_AUTH_DB": AUTH_DB,
            "ZHIRUN_AUTH_MODE": "password",
            "ZHIRUN_AUTH_COOKIE_SECURE": "0",
            "ZHIRUN_AUTH_DEV_CODE": "",
        })
        for name in ("zhirun_server.py", "auth_store.py", "index.html"):
            sftp.put(str(PROJECT / "server" / name), f"/tmp/{name}.password-auth")
        with sftp.open("/tmp/zhirun-server.env.password-auth", "w") as handle:
            handle.write(updated_env)
        sftp.chmod("/tmp/zhirun-server.env.password-auth", 0o600)
    finally:
        sftp.close()

    quoted_backup = shlex.quote(backup)
    run(client, f"install -d -m 0750 {quoted_backup}")
    run(client, f"cp -a {REMOTE_ENV} {quoted_backup}/server.env")
    for name in ("zhirun_server.py", "auth_store.py", "index.html"):
        remote = posixpath.join(REMOTE_SERVER, name)
        run(client, f"test ! -e {shlex.quote(remote)} || cp -a {shlex.quote(remote)} {quoted_backup}/{name}")
    run(client, "/opt/zhirun/.venv/bin/python -m py_compile /tmp/zhirun_server.py.password-auth /tmp/auth_store.py.password-auth")
    run(client, f"install -d -o zhirun -g zhirun -m 0750 {posixpath.dirname(AUTH_DB)}")
    run(client, "install -o zhirun -g zhirun -m 0644 /tmp/zhirun_server.py.password-auth /opt/zhirun/server/zhirun_server.py")
    run(client, "install -o zhirun -g zhirun -m 0644 /tmp/auth_store.py.password-auth /opt/zhirun/server/auth_store.py")
    run(client, "install -o zhirun -g zhirun -m 0644 /tmp/index.html.password-auth /opt/zhirun/server/index.html")
    run(client, "install -o root -g zhirun -m 0640 /tmp/zhirun-server.env.password-auth /etc/zhirun/server.env")
    provision = (
        "import sys; from auth_store import AuthStore; "
        "AuthStore(sys.argv[1], sys.argv[2]).ensure_password_account(sys.argv[3], sys.argv[4], sys.argv[5])"
    )
    command = "runuser -u zhirun -- env PYTHONPATH=/opt/zhirun/server /opt/zhirun/.venv/bin/python -c {} {} {} {} {} {}".format(
        shlex.quote(provision),
        shlex.quote(AUTH_DB),
        shlex.quote(auth_secret),
        shlex.quote(ADMIN_USERNAME),
        shlex.quote(ADMIN_PASSWORD),
        shlex.quote(DEVICE_ID),
    )
    run(client, command)
    run(client, "systemctl restart zhirun-server.service; sleep 3; systemctl is-active zhirun-server.service")
    client.close()
    public_verification()
    print(f"BACKUP={backup}")
    print(f"ADMIN_USERNAME={ADMIN_USERNAME}")
    print(f"ADMIN_PASSWORD={ADMIN_PASSWORD}")
    print(f"DEVICE_ID={DEVICE_ID}")
    print("PUBLIC_VERIFICATION=OK")


if __name__ == "__main__":
    main()

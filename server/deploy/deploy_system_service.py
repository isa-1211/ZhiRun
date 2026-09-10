# -*- coding: utf-8 -*-
"""Deploy the web page and server to the system-level ZhiRun service."""
import os
import posixpath
import time

import paramiko


def load_local_env():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_local_env()
HOST = os.environ["ZHIRUN_HOST"]
USER = os.environ.get("ZHIRUN_USER", "root")
PASSWORD = os.environ["ZHIRUN_PWD"]
LOCAL_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_DIR = "/home/lijing"
STAMP = time.strftime("%Y%m%d-%H%M%S")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=20)


def run(command):
    _stdin, stdout, stderr = ssh.exec_command(command)
    code = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", "ignore").strip()
    error = stderr.read().decode("utf-8", "ignore").strip()
    if code:
        raise RuntimeError(f"command failed ({code}): {command}\n{error}")
    return output


for name in ("index.html", "zhirun_server.py", "auth_store.py"):
    remote = posixpath.join(REMOTE_DIR, name)
    run(f"test ! -e {remote} || cp {remote} {remote}.before-auth-{STAMP}")

sftp = ssh.open_sftp()
for name in ("index.html", "zhirun_server.py", "auth_store.py"):
    local = os.path.join(LOCAL_SERVER, name)
    remote = posixpath.join(REMOTE_DIR, name)
    sftp.put(local, remote)
    print(f"uploaded {remote} ({os.path.getsize(local)} bytes)")
sftp.close()

run("systemctl restart zhirun.service")
time.sleep(2)
print("service:", run("systemctl is-active zhirun.service"))
print("backup stamp:", STAMP)
ssh.close()

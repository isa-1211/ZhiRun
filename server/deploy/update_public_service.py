"""Deploy the public HTTP service without replacing its systemd configuration."""
import os
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
if ENV_FILE.exists():
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


host = os.environ["ZHIRUN_HOST"]
user = os.environ.get("ZHIRUN_ROOT_USER", "root")
password = os.environ["ZHIRUN_PWD"]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=password, timeout=20)

with ssh.open_sftp() as sftp:
    sftp.put(str(ROOT / "server" / "index.html"), "/home/lijing/index.html")
    sftp.put(str(ROOT / "server" / "zhirun_server.py"), "/home/lijing/zhirun_server.py")
    sftp.put(str(ROOT / "server" / "auth_store.py"), "/home/lijing/auth_store.py")

_, stdout, stderr = ssh.exec_command("systemctl restart zhirun.service && systemctl is-active zhirun.service")
status = stdout.read().decode("utf-8", "ignore").strip()
error = stderr.read().decode("utf-8", "ignore").strip()
ssh.close()
if status != "active":
    raise SystemExit(error or f"unexpected service status: {status}")
print("public service updated and active")

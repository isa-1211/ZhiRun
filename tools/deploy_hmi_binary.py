"""Deploy only the compiled RK3506B HMI binary with a timestamped backup."""
import os
import time
from pathlib import Path

import paramiko


project = Path(__file__).resolve().parent.parent
binary = project / "downloads" / "zhirun_hmi_demo"
host = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
password = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")
stamp = time.strftime("%Y%m%d-%H%M%S")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username="root", password=password, timeout=10, look_for_keys=False, allow_agent=False)
sftp = client.open_sftp()
sftp.put(str(binary), "/tmp/zhirun_hmi_demo.new")
sftp.chmod("/tmp/zhirun_hmi_demo.new", 0o755)
sftp.close()

command = (
    f"cp -p /oem/usr/bin/zhirun_hmi_demo /userdata/zhirun_hmi_demo.before-identity-{stamp}; "
    "install -m 0755 /tmp/zhirun_hmi_demo.new /oem/usr/bin/zhirun_hmi_demo; sync; "
    "nohup /etc/init.d/S99zhirun-hmi restart >/tmp/zhirun-hmi-identity-restart.log 2>&1 </dev/null &"
)
_, stdout, stderr = client.exec_command(command, timeout=20)
code = stdout.channel.recv_exit_status()
error = stderr.read().decode("utf-8", "replace").strip()
if code:
    raise RuntimeError(error or f"install failed with {code}")
time.sleep(12)
_, stdout, stderr = client.exec_command(
    "ps | grep zhirun_hmi_demo | grep -v grep; tail -30 /tmp/zhirun_hmi.log", timeout=20
)
output = stdout.read().decode("utf-8", "replace")
error = stderr.read().decode("utf-8", "replace")
code = stdout.channel.recv_exit_status()
client.close()
print(output)
if error:
    print(error)
if code or "zhirun_hmi_demo" not in output:
    raise RuntimeError("HMI did not restart")
print(f"BACKUP=/userdata/zhirun_hmi_demo.before-identity-{stamp}")

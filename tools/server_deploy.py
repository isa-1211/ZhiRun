import os
from pathlib import Path

import paramiko


HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]
PROJECT = Path(__file__).resolve().parent.parent


def run(client, command, timeout=60):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    if output:
        print(output, end="")
    if error:
        print(error, end="")
    if code:
        raise RuntimeError("remote command failed with exit code %d" % code)


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        password=PASSWORD,
        timeout=15,
        look_for_keys=False,
        allow_agent=False,
    )
    sftp = client.open_sftp()
    sftp.put(str(PROJECT / "server" / "zhirun_server.py"), "/tmp/zhirun_server.py.new")
    sftp.put(str(PROJECT / "server" / "auth_store.py"), "/tmp/zhirun_auth_store.py.new")
    sftp.put(str(PROJECT / "server" / "infer_server.py"), "/tmp/zhirun_infer_server.py.new")
    sftp.put(str(PROJECT / "server" / "index.html"), "/tmp/zhirun_index.html.new")
    sftp.put(
        str(PROJECT / "灌溉模型" / "灌溉模型" / "scripts" / "fertigation_model.py"),
        "/tmp/zhirun_fertigation_model.py.new",
    )
    sftp.close()

    run(client, "/opt/zhirun/.venv/bin/python -m py_compile /tmp/zhirun_server.py.new")
    run(client, "/opt/zhirun/.venv/bin/python -m py_compile /tmp/zhirun_auth_store.py.new")
    run(client, "/opt/zhirun/.venv/bin/python -m py_compile /tmp/zhirun_infer_server.py.new")
    run(client, "/opt/zhirun/.venv/bin/python -m py_compile /tmp/zhirun_fertigation_model.py.new")
    run(
        client,
        "test -e /opt/zhirun/server/zhirun_server.py.pre-rk3506 || "
        "cp -p /opt/zhirun/server/zhirun_server.py /opt/zhirun/server/zhirun_server.py.pre-rk3506; "
        "test -e /opt/zhirun/server/index.html.pre-rk3506 || "
        "cp -p /opt/zhirun/server/index.html /opt/zhirun/server/index.html.pre-rk3506; "
        "state=$(sed -n 's/^ZHIRUN_STATE_FILE=//p' /etc/zhirun/server.env 2>/dev/null | tail -1); "
        "test -n \"$state\" || state=/opt/zhirun/server/.zhirun_state.json; "
        "test ! -e \"$state\" -o -e \"$state.pre-rk3506\" || cp -p \"$state\" \"$state.pre-rk3506\"; "
        "install -o zhirun -g zhirun -m 0644 /tmp/zhirun_server.py.new /opt/zhirun/server/zhirun_server.py; "
        "install -o zhirun -g zhirun -m 0644 /tmp/zhirun_auth_store.py.new /opt/zhirun/server/auth_store.py; "
        "install -o zhirun -g zhirun -m 0644 /tmp/zhirun_infer_server.py.new /opt/zhirun/server/infer_server.py; "
        "install -o zhirun -g zhirun -m 0644 /tmp/zhirun_index.html.new /opt/zhirun/server/index.html; "
        "install -m 0644 /tmp/zhirun_fertigation_model.py.new '/opt/zhirun/灌溉模型/灌溉模型/scripts/fertigation_model.py'; "
        "systemctl restart zhirun-infer.service zhirun-server.service; sleep 8; "
        "systemctl is-active zhirun-infer.service; "
        "systemctl is-active zhirun-server.service; "
        "curl -fsS http://127.0.0.1:10001/health; echo; "
        "token=$(sed -n 's/^ZHIRUN_PUSH_TOKEN=//p' /etc/zhirun/server.env | tail -1); "
        "curl -fsS -H \"X-Device-Token: $token\" http://127.0.0.1:10000/data; echo; "
        "curl -fsS -H \"X-Device-Token: $token\" http://127.0.0.1:10000/api/devices; echo; "
        "curl -fsS -H \"X-Device-Token: $token\" http://127.0.0.1:10000/schema; echo",
        timeout=45,
    )
    client.close()


if __name__ == "__main__":
    main()

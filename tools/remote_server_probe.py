import os

import paramiko


HOST = os.environ.get("ZHIRUN_BUILD_HOST", "8.145.49.45")
PASSWORD = os.environ["ZHIRUN_BUILD_PASSWORD"]


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
    command = (
        "systemctl cat zhirun-server.service 2>/dev/null; "
        "pid=$(systemctl show -p MainPID --value zhirun-server.service 2>/dev/null); "
        "echo MAIN_PID=$pid; test -n \"$pid\" -a \"$pid\" != 0 && "
        "{ echo CWD=$(readlink /proc/$pid/cwd); tr '\\0' ' ' </proc/$pid/cmdline; echo; }; "
        "find /opt /root -maxdepth 5 -type f -name zhirun_server.py 2>/dev/null; "
        "echo AUTH_DEPLOY_STATE; "
        "state=$(sed -n 's/^ZHIRUN_STATE_FILE=//p' /etc/zhirun/server.env 2>/dev/null | tail -1); "
        "test -n \"$state\" || state=/opt/zhirun/server/.zhirun_state.json; "
        "echo STATE_FILE=$state; "
        "if test -e /var/lib/zhirun/auth.sqlite3; then echo AUTH_DB_EXISTS; else echo AUTH_DB_ABSENT; fi; "
        "runuser -u zhirun -- /opt/zhirun/.venv/bin/python -c 'import sqlite3; "
        "d=sqlite3.connect(\"/var/lib/zhirun/auth.sqlite3\"); "
        "print(\"BINDINGS\",d.execute(\"select device_id,user_id,created_at from device_bindings\").fetchall()); "
        "print(\"CODES\",d.execute(\"select device_id,created_at,expires_at,consumed_at from device_binding_codes\").fetchall())' 2>/dev/null; "
        "echo AUTH_RELATED_PROCESSES; ps -ef | grep -E 'deploy_password_auth|ensure_password_account|auth/device/bind' | grep -v grep || true; "
        "python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); "
        "print(\"DEVICE_IDS\", *d.get(\"devices\", {}).keys(), sep=\"\\n\")' \"$state\"; "
        "echo SERVER_ENV_KEYS; sed -n 's/=.*//p' /etc/zhirun/server.env 2>/dev/null; "
        "echo INFER_SYSTEM_SERVICE; systemctl cat zhirun-infer.service 2>/dev/null; "
        "echo INFER_STATUS; systemctl status zhirun-infer.service --no-pager 2>&1 | head -25; "
        "echo INFER_FILES; find /opt/zhirun /root/zhirun-infer -maxdepth 5 -type f "
        "\\( -name infer_server.py -o -name hohhot_fertigation_policy_v2.joblib "
        "-o -name fertigation_model.py \\) 2>/dev/null"
    )
    _, stdout, stderr = client.exec_command(command, timeout=30)
    print(stdout.read().decode(errors="replace"))
    error = stderr.read().decode(errors="replace")
    if error:
        print(error)
    client.close()


if __name__ == "__main__":
    main()

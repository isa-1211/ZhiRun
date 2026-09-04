import os
import shlex
import time

import paramiko


HOST = os.environ.get("ZHIRUN_BOARD_HOST", "192.168.1.10")
PASSWORD = os.environ.get("ZHIRUN_BOARD_PASSWORD", "root")
SSID = os.environ.get("ZHIRUN_CAMPUS_SSID", "IMAU")


def run(client, command, timeout=20):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(error or output or "remote command failed")
    return output


def wpa_string(value):
    return shlex.quote('"' + value.replace('"', '\\"') + '"')


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
    probe_id = None
    previous_id = None
    try:
        status = run(client, "wpa_cli -i wlan0 status")
        current_ssid = next(
            (line.split("=", 1)[1] for line in status.splitlines() if line.startswith("ssid=")),
            "",
        )
        for line in run(client, "wpa_cli -i wlan0 list_networks").splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) >= 2 and fields[1] == current_ssid:
                previous_id = fields[0]
                break
        if previous_id is None:
            raise RuntimeError("current Wi-Fi network id was not found")

        probe_id = run(client, "wpa_cli -i wlan0 add_network").strip().splitlines()[-1]
        run(client, "wpa_cli -i wlan0 set_network %s ssid %s" % (probe_id, wpa_string(SSID)))
        run(client, "wpa_cli -i wlan0 set_network %s key_mgmt NONE" % probe_id)
        run(client, "wpa_cli -i wlan0 select_network %s" % probe_id)
        for _ in range(30):
            time.sleep(1)
            status = run(client, "wpa_cli -i wlan0 status")
            if "wpa_state=COMPLETED" in status and ("ssid=" + SSID) in status:
                break
        else:
            raise RuntimeError("campus Wi-Fi association timed out")

        dhcp = run(
            client,
            "dhcpcd -k wlan0 >/dev/null 2>&1 || true; "
            "ip addr flush dev wlan0 scope global; "
            "udhcpc -i wlan0 -n -q -t 10 -T 2 2>&1 || true",
            timeout=30,
        )
        print("DHCP\n" + dhcp)
        time.sleep(2)
        probe = run(
            client,
            "echo STATUS; wpa_cli -i wlan0 status; "
            "echo ROUTE; ip -4 addr show dev wlan0; ip route; "
            "echo PROBE; "
            "wget -T 8 -S -O /tmp/zhirun-campus-page "
            "http://connectivitycheck.gstatic.com/generate_204 2>&1 || true; "
            "echo PAGE; head -c 4096 /tmp/zhirun-campus-page 2>/dev/null || true",
            timeout=30,
        )
        print(probe)
        https_probe = """import ssl
import urllib.request
url = 'https://login.imau.edu.cn/static/themes/pro/js/Portal.js?v=2.00.20220810'
request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
response = urllib.request.urlopen(request, timeout=12, context=ssl._create_unverified_context())
lines = response.read(524288).decode('utf-8', 'replace').splitlines()
needles = ('challenge', 'hmd5', 'chksum', 'srun_bx1', 'ac_id', 'encodeUserInfo', 'getToken', 'loginAccount')
selected = set()
for index, line in enumerate(lines):
    if any(needle.lower() in line.lower() for needle in needles):
        selected.update(range(max(0, index - 4), min(len(lines), index + 8)))
for index in sorted(selected):
    print('%05d %s' % (index + 1, lines[index]))
"""
        print("HTTPS\n" + run(client, "python3 -c %s" % shlex.quote(https_probe), timeout=25))
    finally:
        if previous_id is not None:
            try:
                run(client, "wpa_cli -i wlan0 select_network %s" % previous_id)
                time.sleep(4)
                run(
                    client,
                    "ip addr flush dev wlan0 scope global; "
                    "dhcpcd wlan0 >/dev/null 2>&1 || true",
                )
            except RuntimeError:
                pass
        if probe_id is not None:
            try:
                run(client, "wpa_cli -i wlan0 remove_network %s" % probe_id)
            except RuntimeError:
                pass
        client.close()


if __name__ == "__main__":
    main()

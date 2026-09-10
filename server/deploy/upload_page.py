# -*- coding: utf-8 -*-
"""上传 index.html + zhirun_server.py 到服务器, 并注册为用户级 systemd 服务
(开机自启 + 崩溃重启)。

本项目服务器账号无 root 权限, 服务以"用户级"(systemctl --user)方式运行,
监听高位端口 10000 (绑定 80 需 root, 本账号做不到)。

用法:
    复制项目根目录 `.env.example` 为 `.env`, 填写自己的服务器参数后运行。
    也可直接通过环境变量覆盖 `.env` 中的值。
"""
import paramiko, os, sys, time


def load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_local_env()

HOST = os.environ.get("ZHIRUN_HOST") or sys.exit("请设置 ZHIRUN_HOST (服务器地址)")
USER = os.environ.get("ZHIRUN_USER") or sys.exit("请设置 ZHIRUN_USER (服务器用户名)")
PWD  = os.environ.get("ZHIRUN_PWD")
if not PWD:
    sys.exit("请设置 ZHIRUN_PWD (服务器 SSH 密码)")
TOKEN = os.environ.get("ZHIRUN_PUSH_TOKEN")
if not TOKEN:
    sys.exit("请设置 ZHIRUN_PUSH_TOKEN (ESP32 上报口令)")
# 服务以用户级运行, 默认监听 10000; 可用 ZHIRUN_PORT 覆盖
PORT = os.environ.get("ZHIRUN_PORT", "10000").strip() or "10000"

# 待上传的文件在本脚本上一级目录 (server/)
SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=20)


def run(cmd):
    _in, out, err = ssh.exec_command(cmd)
    o = out.read().decode("utf-8", "ignore")
    e = err.read().decode("utf-8", "ignore")
    return o.strip(), e.strip()


# --- 1. 上传文件到用户家目录 ---
sftp = ssh.open_sftp()
for fn in ["index.html", "zhirun_server.py", "auth_store.py"]:
    local = os.path.join(SERVER_DIR, fn)
    sftp.put(local, fn)  # 相对路径 = 家目录
    print("uploaded", fn, os.path.getsize(local), "bytes")

# 生成用户级 systemd 服务文件 (替换 token / port 占位符)
with open(os.path.join(DEPLOY_DIR, "zhirun.service"), "r") as f:
    svc = (f.read()
           .replace("__TOKEN__", TOKEN)
           .replace("__PORT__", PORT)
           .replace("__AUTH_SECRET__", os.environ.get("ZHIRUN_AUTH_SECRET", TOKEN)))

run("mkdir -p ~/.config/systemd/user")
svc_remote = ".config/systemd/user/zhirun.service"
with sftp.open(svc_remote, "w") as f:
    f.write(svc)
auth_keys = [
    "ZHIRUN_AUTH_SECRET", "ZHIRUN_AUTH_DB", "ZHIRUN_AUTH_COOKIE_SECURE",
    "ZHIRUN_SMS_WEBHOOK", "ZHIRUN_SMS_WEBHOOK_TOKEN", "ZHIRUN_AUTH_DEV_CODE",
    "ZHIRUN_WECHAT_APP_ID", "ZHIRUN_WECHAT_APP_SECRET", "ZHIRUN_WECHAT_REDIRECT_URI",
]
auth_lines = []
for key in auth_keys:
    value = os.environ.get(key, "")
    if "\n" in value or "\r" in value:
        sys.exit(f"{key} 不能包含换行符")
    auth_lines.append(f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"')
with sftp.open(".zhirun-auth.env", "w") as f:
    f.write("\n".join(auth_lines) + "\n")
sftp.chmod(".zhirun-auth.env", 0o600)
sftp.close()
print("uploaded user-level zhirun.service")

# --- 2. 安装并(重)启动用户级服务 ---
run("systemctl --user daemon-reload")
run("systemctl --user enable zhirun.service")
run("systemctl --user restart zhirun.service")
# 让用户级服务在用户未登录时也能常驻 (需管理员开过 linger, 开过即幂等)
run(f"loginctl enable-linger {USER} 2>/dev/null; true")
print("user service (re)started")

# --- 3. 确认状态 + 自测 ---
time.sleep(2)
o, _ = run("systemctl --user is-active zhirun.service")
print("active:", o)
o, _ = run(f"curl -s http://127.0.0.1:{PORT}/ | head -c 120")
print("page head:", o)
o, _ = run(f"curl -s http://127.0.0.1:{PORT}/data | head -c 300")
print("data:", o)

ssh.close()
print(f"DONE — 用户级 systemd 服务已就绪, 监听 {PORT}, 开机自启 + 崩溃自动重启")

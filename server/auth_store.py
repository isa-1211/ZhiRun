"""Persistent account, session, WeChat-link and device-binding storage."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$")
PASSWORD_ITERATIONS = 310_000
SESSION_SECONDS = 30 * 24 * 60 * 60
CODE_SECONDS = 5 * 60
BINDING_CODE_SECONDS = 10 * 60


class AuthError(Exception):
    def __init__(self, code: str, status: int = 400, message: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message or code


class AutoClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class AuthStore:
    def __init__(self, path: str, secret: str, sms_webhook: str = "", sms_webhook_token: str = "", dev_code: str = ""):
        self.path = path
        self.secret = secret.encode("utf-8")
        self.sms_webhook = sms_webhook.strip()
        self.sms_webhook_token = sms_webhook_token.strip()
        self.dev_code = dev_code.strip()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, factory=AutoClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL UNIQUE,
                    phone_verified_at INTEGER NOT NULL,
                    password_hash TEXT,
                    password_salt TEXT,
                    password_iterations INTEGER,
                    wechat_openid TEXT UNIQUE,
                    wechat_unionid TEXT UNIQUE,
                    wechat_nickname TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    user_agent TEXT,
                    ip TEXT
                );
                CREATE TABLE IF NOT EXISTS verification_codes (
                    phone TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at INTEGER,
                    PRIMARY KEY(phone, purpose)
                );
                CREATE TABLE IF NOT EXISTS device_binding_codes (
                    device_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS device_bindings (
                    device_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wechat_states (
                    state_hash TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    user_id INTEGER,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_wechat (
                    token_hash TEXT PRIMARY KEY,
                    openid TEXT NOT NULL,
                    unionid TEXT,
                    nickname TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_bindings_user ON device_bindings(user_id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "username" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL")

    def _digest(self, *parts) -> str:
        body = "\0".join(str(part) for part in parts).encode("utf-8")
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    @staticmethod
    def normalize_phone(phone) -> str:
        phone = str(phone or "").replace(" ", "").replace("-", "")
        if phone.startswith("+86"):
            phone = phone[3:]
        if not PHONE_RE.fullmatch(phone):
            raise AuthError("invalid_phone", message="请输入有效的中国大陆手机号")
        return phone

    @staticmethod
    def normalize_username(username) -> str:
        username = str(username or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise AuthError("invalid_username", message="账号需为 3-32 位字母、数字、点、短横线或下划线，且以字母开头")
        return username.lower()

    @staticmethod
    def validate_password(password) -> str:
        password = str(password or "")
        if len(password) < 8 or len(password) > 72:
            raise AuthError("weak_password", message="密码需为 8-72 位")
        if password.isdigit() or password.isalpha():
            raise AuthError("weak_password", message="密码需同时包含字母和数字")
        return password

    @staticmethod
    def _password_hash(password: str, salt_hex: str, iterations: int) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations).hex()

    def _set_password(self, db, user_id: int, password: str, timestamp: int):
        password = self.validate_password(password)
        salt = secrets.token_hex(16)
        digest = self._password_hash(password, salt, PASSWORD_ITERATIONS)
        db.execute(
            "UPDATE users SET password_hash=?, password_salt=?, password_iterations=?, updated_at=? WHERE id=?",
            (digest, salt, PASSWORD_ITERATIONS, timestamp, user_id),
        )

    def request_code(self, phone, purpose: str, ip: str = "") -> dict:
        phone = self.normalize_phone(phone)
        if purpose not in {"login", "register", "reset", "wechat_link"}:
            raise AuthError("invalid_purpose")
        if not self.sms_webhook and not self.dev_code:
            raise AuthError("sms_not_configured", 503, "短信服务尚未配置")
        timestamp = int(time.time())
        with self._connect() as db:
            previous = db.execute(
                "SELECT sent_at FROM verification_codes WHERE phone=? AND purpose=?", (phone, purpose)
            ).fetchone()
            if previous and timestamp - int(previous["sent_at"]) < 60:
                raise AuthError("code_too_frequent", 429, "请在 60 秒后重新获取验证码")
            code = self.dev_code if re.fullmatch(r"\d{6}", self.dev_code) else f"{secrets.randbelow(1_000_000):06d}"
            db.execute(
                "INSERT OR REPLACE INTO verification_codes(phone,purpose,code_hash,sent_at,expires_at,attempts,consumed_at) VALUES(?,?,?,?,?,0,NULL)",
                (phone, purpose, self._digest("sms", phone, purpose, code), timestamp, timestamp + CODE_SECONDS),
            )
        if self.sms_webhook:
            body = json.dumps({"phone": phone, "code": code, "purpose": purpose}, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", "User-Agent": "ZhiRun-Auth/1.0"}
            if self.sms_webhook_token:
                headers["Authorization"] = "Bearer " + self.sms_webhook_token
            try:
                with urlopen(Request(self.sms_webhook, data=body, headers=headers, method="POST"), timeout=8) as response:
                    if response.status >= 300:
                        raise OSError(f"sms webhook returned {response.status}")
            except Exception as exc:
                raise AuthError("sms_unavailable", 503, f"验证码发送服务不可用: {exc}") from exc
        result = {"ok": True, "retry_after": 60, "expires_in": CODE_SECONDS}
        if self.dev_code:
            result["development_code"] = code
        return result

    def verify_code(self, phone, purpose: str, code) -> str:
        phone = self.normalize_phone(phone)
        timestamp = int(time.time())
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM verification_codes WHERE phone=? AND purpose=?", (phone, purpose)
            ).fetchone()
            if not row or row["consumed_at"] or int(row["expires_at"]) < timestamp:
                raise AuthError("code_expired", message="验证码无效或已过期")
            if int(row["attempts"]) >= 5:
                raise AuthError("code_locked", 429, "验证码错误次数过多，请重新获取")
            supplied = self._digest("sms", phone, purpose, str(code or ""))
            if not hmac.compare_digest(supplied, row["code_hash"]):
                db.execute("UPDATE verification_codes SET attempts=attempts+1 WHERE phone=? AND purpose=?", (phone, purpose))
                raise AuthError("invalid_code", message="验证码错误")
            db.execute("UPDATE verification_codes SET consumed_at=? WHERE phone=? AND purpose=?", (timestamp, phone, purpose))
        return phone

    def _find_or_create_user(self, db, phone: str, timestamp: int):
        row = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if row:
            if row["status"] != "active":
                raise AuthError("account_disabled", 403, "账号已停用")
            return row
        cursor = db.execute(
            "INSERT INTO users(phone,phone_verified_at,created_at,updated_at) VALUES(?,?,?,?)",
            (phone, timestamp, timestamp, timestamp),
        )
        return db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()

    def register(self, phone, code, password):
        password = self.validate_password(password)
        phone = self.verify_code(phone, "register", code)
        timestamp = int(time.time())
        with self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
                raise AuthError("phone_exists", 409, "该手机号已注册")
            user = self._find_or_create_user(db, phone, timestamp)
            self._set_password(db, user["id"], password, timestamp)
            return int(user["id"])

    def ensure_password_account(self, username, password, device_id: str = "") -> dict:
        """Provision an administrator once without resetting existing credentials."""
        username = self.normalize_username(username)
        password = self.validate_password(password)
        timestamp = int(time.time())
        created = False
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                cursor = db.execute(
                    "INSERT INTO users(phone,phone_verified_at,username,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("account:" + username, 0, username, timestamp, timestamp),
                )
                user_id = int(cursor.lastrowid)
                self._set_password(db, user_id, password, timestamp)
                created = True
            else:
                user_id = int(row["id"])
                if row["status"] != "active":
                    raise AuthError("account_disabled", 403, "账号已停用")
            if device_id:
                owner = db.execute("SELECT user_id FROM device_bindings WHERE device_id=?", (device_id,)).fetchone()
                if not owner:
                    db.execute(
                        "INSERT INTO device_bindings(device_id,user_id,role,created_at) VALUES(?,?,?,?)",
                        (device_id, user_id, "owner", timestamp),
                    )
        return {"user_id": user_id, "created": created}

    def login_password(self, identifier, password) -> int:
        identifier = str(identifier or "").strip()
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (identifier.lower(),)).fetchone()
            if not row and PHONE_RE.fullmatch(identifier.replace(" ", "").replace("-", "").removeprefix("+86")):
                phone = self.normalize_phone(identifier)
                row = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if not row or not row["password_hash"] or row["status"] != "active":
            raise AuthError("invalid_credentials", 401, "账号或密码错误")
        digest = self._password_hash(str(password or ""), row["password_salt"], int(row["password_iterations"]))
        if not hmac.compare_digest(digest, row["password_hash"]):
            raise AuthError("invalid_credentials", 401, "账号或密码错误")
        return int(row["id"])

    def login_code(self, phone, code) -> int:
        phone = self.verify_code(phone, "login", code)
        timestamp = int(time.time())
        with self._connect() as db:
            return int(self._find_or_create_user(db, phone, timestamp)["id"])

    def reset_password(self, phone, code, password):
        password = self.validate_password(password)
        phone = self.verify_code(phone, "reset", code)
        timestamp = int(time.time())
        with self._connect() as db:
            row = db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
            if not row:
                raise AuthError("account_not_found", 404, "账号不存在")
            self._set_password(db, int(row["id"]), password, timestamp)
            db.execute("DELETE FROM sessions WHERE user_id=?", (int(row["id"]),))

    def create_session(self, user_id: int, user_agent: str = "", ip: str = "") -> dict:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        timestamp = int(time.time())
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at,last_seen_at,user_agent,ip) VALUES(?,?,?,?,?,?,?,?)",
                (self._digest("session", token), user_id, csrf, timestamp, timestamp + SESSION_SECONDS, timestamp, user_agent[:256], ip[:64]),
            )
        return {"token": token, "csrf_token": csrf, "expires_at": timestamp + SESSION_SECONDS}

    def session(self, token: str | None):
        if not token:
            return None
        timestamp = int(time.time())
        token_hash = self._digest("session", token)
        with self._connect() as db:
            row = db.execute(
                "SELECT s.*,u.phone,u.username,u.wechat_openid,u.wechat_nickname,u.status FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row or row["status"] != "active" or int(row["expires_at"]) < timestamp:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
                return None
            if timestamp - int(row["last_seen_at"]) > 300:
                db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (timestamp, token_hash))
            return dict(row)

    def logout(self, token: str | None):
        if token:
            with self._connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (self._digest("session", token),))

    def user_payload(self, user_id: int) -> dict:
        with self._connect() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            bindings = db.execute(
                "SELECT device_id,role,created_at FROM device_bindings WHERE user_id=? ORDER BY created_at", (user_id,)
            ).fetchall()
        phone = user["phone"] if not str(user["phone"]).startswith("account:") else ""
        username = user["username"] or ""
        return {
            "id": int(user["id"]),
            "username": username,
            "account_label": username or (phone[:3] + "****" + phone[-4:]),
            "phone": phone,
            "phone_masked": phone[:3] + "****" + phone[-4:] if phone else "",
            "wechat_linked": bool(user["wechat_openid"]),
            "wechat_nickname": user["wechat_nickname"],
            "devices": [dict(row) for row in bindings],
        }

    def device_ids(self, user_id: int) -> set[str]:
        with self._connect() as db:
            rows = db.execute("SELECT device_id FROM device_bindings WHERE user_id=?", (user_id,)).fetchall()
        return {row["device_id"] for row in rows}

    def identity_code(self, device_id: str, rotate: bool = False) -> dict:
        timestamp = int(time.time())
        with self._connect() as db:
            owner = db.execute("SELECT user_id FROM device_bindings WHERE device_id=?", (device_id,)).fetchone()
            if owner:
                return {"ok": True, "device_id": device_id, "bound": True, "code": None, "expires_at": 0}
            row = db.execute("SELECT * FROM device_binding_codes WHERE device_id=?", (device_id,)).fetchone()
            if rotate or not row or row["consumed_at"] or int(row["expires_at"]) <= timestamp:
                while True:
                    code = f"{secrets.randbelow(100_000_000):08d}"
                    if not db.execute("SELECT 1 FROM device_binding_codes WHERE code=? AND expires_at>?", (code, timestamp)).fetchone():
                        break
                db.execute(
                    "INSERT OR REPLACE INTO device_binding_codes(device_id,code,created_at,expires_at,consumed_at) VALUES(?,?,?,?,NULL)",
                    (device_id, code, timestamp, timestamp + BINDING_CODE_SECONDS),
                )
            else:
                code = row["code"]
            return {"ok": True, "device_id": device_id, "bound": False, "code": code, "expires_at": timestamp + BINDING_CODE_SECONDS if rotate or not row or row["consumed_at"] or int(row["expires_at"]) <= timestamp else int(row["expires_at"])}

    def bind_device(self, user_id: int, code) -> str:
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{8}", code):
            raise AuthError("invalid_binding_code", message="请输入板端显示的 8 位身份码")
        timestamp = int(time.time())
        with self._connect() as db:
            row = db.execute("SELECT * FROM device_binding_codes WHERE code=?", (code,)).fetchone()
            if not row or row["consumed_at"] or int(row["expires_at"]) < timestamp:
                raise AuthError("binding_code_expired", message="身份码无效或已过期，请查看板端新码")
            existing = db.execute("SELECT user_id FROM device_bindings WHERE device_id=?", (row["device_id"],)).fetchone()
            if existing and int(existing["user_id"]) != user_id:
                raise AuthError("device_already_bound", 409, "设备已绑定其他账号")
            db.execute(
                "INSERT OR IGNORE INTO device_bindings(device_id,user_id,role,created_at) VALUES(?,?,?,?)",
                (row["device_id"], user_id, "owner", timestamp),
            )
            db.execute("UPDATE device_binding_codes SET consumed_at=? WHERE device_id=?", (timestamp, row["device_id"]))
            return row["device_id"]

    def unbind_device(self, user_id: int, device_id: str):
        with self._connect() as db:
            result = db.execute("DELETE FROM device_bindings WHERE user_id=? AND device_id=?", (user_id, device_id))
            db.execute("DELETE FROM device_binding_codes WHERE device_id=?", (device_id,))
            if result.rowcount != 1:
                raise AuthError("binding_not_found", 404, "未找到设备绑定")

    def create_wechat_state(self, purpose: str, user_id: int | None = None) -> str:
        state = secrets.token_urlsafe(24)
        timestamp = int(time.time())
        with self._connect() as db:
            db.execute("DELETE FROM wechat_states WHERE expires_at<?", (timestamp,))
            db.execute(
                "INSERT INTO wechat_states(state_hash,purpose,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                (self._digest("wechat-state", state), purpose, user_id, timestamp, timestamp + 600),
            )
        return state

    def consume_wechat_state(self, state: str) -> dict:
        timestamp = int(time.time())
        digest = self._digest("wechat-state", state)
        with self._connect() as db:
            row = db.execute("SELECT * FROM wechat_states WHERE state_hash=?", (digest,)).fetchone()
            db.execute("DELETE FROM wechat_states WHERE state_hash=?", (digest,))
        if not row or int(row["expires_at"]) < timestamp:
            raise AuthError("invalid_wechat_state", 400, "微信登录状态已过期")
        return dict(row)

    def resolve_wechat(self, openid: str, unionid: str = "", nickname: str = "") -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM users WHERE wechat_openid=? OR (?<>'' AND wechat_unionid=?)",
                (openid, unionid, unionid),
            ).fetchone()
            if row:
                return {"user_id": int(row["id"]), "pending_token": None}
            token = secrets.token_urlsafe(24)
            timestamp = int(time.time())
            db.execute(
                "INSERT INTO pending_wechat(token_hash,openid,unionid,nickname,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (self._digest("pending-wechat", token), openid, unionid or None, nickname[:128], timestamp, timestamp + 600),
            )
            return {"user_id": None, "pending_token": token}

    def link_pending_wechat(self, token: str, phone, code) -> int:
        timestamp = int(time.time())
        digest = self._digest("pending-wechat", token)
        with self._connect() as db:
            pending = db.execute("SELECT * FROM pending_wechat WHERE token_hash=?", (digest,)).fetchone()
            if not pending or int(pending["expires_at"]) < timestamp:
                raise AuthError("wechat_link_expired", message="微信绑定已过期，请重新扫码")
        phone = self.verify_code(phone, "wechat_link", code)
        with self._connect() as db:
            pending = db.execute("SELECT * FROM pending_wechat WHERE token_hash=?", (digest,)).fetchone()
            if not pending or int(pending["expires_at"]) < timestamp:
                raise AuthError("wechat_link_expired", message="微信绑定已过期，请重新扫码")
            user = self._find_or_create_user(db, phone, timestamp)
            conflict = db.execute(
                "SELECT id FROM users WHERE (wechat_openid=? OR (? IS NOT NULL AND wechat_unionid=?)) AND id<>?",
                (pending["openid"], pending["unionid"], pending["unionid"], user["id"]),
            ).fetchone()
            if conflict or user["wechat_openid"]:
                raise AuthError("wechat_already_linked", 409, "该微信或手机号已绑定其他账号")
            db.execute(
                "UPDATE users SET wechat_openid=?,wechat_unionid=?,wechat_nickname=?,updated_at=? WHERE id=?",
                (pending["openid"], pending["unionid"], pending["nickname"], timestamp, user["id"]),
            )
            db.execute("DELETE FROM pending_wechat WHERE token_hash=?", (digest,))
            return int(user["id"])


def wechat_authorize_url(appid: str, redirect_uri: str, state: str) -> str:
    return "https://open.weixin.qq.com/connect/qrconnect?" + urlencode({
        "appid": appid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "snsapi_login",
        "state": state,
    }) + "#wechat_redirect"


def exchange_wechat_code(appid: str, secret: str, code: str) -> dict:
    token_url = "https://api.weixin.qq.com/sns/oauth2/access_token?" + urlencode({
        "appid": appid, "secret": secret, "code": code, "grant_type": "authorization_code",
    })
    with urlopen(Request(token_url, headers={"User-Agent": "ZhiRun-Auth/1.0"}), timeout=10) as response:
        token = json.loads(response.read().decode("utf-8"))
    if token.get("errcode") or not token.get("openid"):
        raise AuthError("wechat_exchange_failed", 502, token.get("errmsg") or "微信授权失败")
    nickname = ""
    try:
        info_url = "https://api.weixin.qq.com/sns/userinfo?" + urlencode({
            "access_token": token["access_token"], "openid": token["openid"], "lang": "zh_CN",
        })
        with urlopen(Request(info_url, headers={"User-Agent": "ZhiRun-Auth/1.0"}), timeout=10) as response:
            info = json.loads(response.read().decode("utf-8"))
        nickname = str(info.get("nickname") or "")
    except Exception:
        pass
    return {"openid": token["openid"], "unionid": token.get("unionid") or "", "nickname": nickname}

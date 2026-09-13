import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from server.auth_store import AuthStore
from server import zhirun_server


class AuthHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.auth = AuthStore(
            os.path.join(cls.tempdir.name, "auth.sqlite3"),
            "http-test-secret",
            dev_code="123456",
        )
        cls.patches = [
            mock.patch.object(zhirun_server, "AUTH", cls.auth),
            mock.patch.object(zhirun_server, "PUSH_TOKEN", "device-secret"),
        ]
        for patch in cls.patches:
            patch.start()
        cls.device_id = "rk3506b-http-test"
        zhirun_server._devices[cls.device_id] = {
            "source": "rk3506", "capabilities": ["valve_control"]
        }
        zhirun_server._latest_by_device[cls.device_id] = {
            "_ts": zhirun_server.now(), "soilMoist": 38.2
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), zhirun_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        zhirun_server._devices.pop(cls.device_id, None)
        zhirun_server._latest_by_device.pop(cls.device_id, None)
        for patch in reversed(cls.patches):
            patch.stop()
        cls.tempdir.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, payload, request_headers)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if raw else {}
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, result, headers_out

    def test_full_login_binding_and_protected_data_flow(self):
        status, _, _ = self.request("GET", "/data")
        self.assertEqual(status, 401)

        status, identity, _ = self.request(
            "GET",
            "/device/identity?device_id=" + self.device_id,
            headers={"X-Device-Token": "device-secret"},
        )
        self.assertEqual(status, 200)
        self.assertRegex(identity["code"], r"^\d{8}$")

        self.request("POST", "/auth/code/request", {"phone": "13800138001", "purpose": "login"})
        status, login, headers = self.request(
            "POST", "/auth/login/code", {"phone": "13800138001", "code": "123456"}
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf_headers = {"Cookie": cookie, "X-CSRF-Token": login["csrf_token"]}

        status, _, _ = self.request("GET", "/data", headers={"Cookie": cookie})
        self.assertEqual(status, 403)
        status, bound, _ = self.request(
            "POST", "/auth/device/bind", {"code": identity["code"]}, csrf_headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(bound["device_id"], self.device_id)

        status, data, _ = self.request(
            "GET", "/data", headers={"Cookie": cookie, "X-ZhiRun-Device": self.device_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["soilMoist"], 38.2)

        status, _, _ = self.request(
            "POST", "/auth/device/unbind", {"device_id": self.device_id}, {"Cookie": cookie}
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/auth/device/unbind", {"device_id": self.device_id}, csrf_headers
        )
        self.assertEqual(status, 200)

    def test_collector_command_poll_accepts_existing_token_parameter(self):
        status, result, _ = self.request(
            "GET",
            f"/api/devices/{self.device_id}/valve/commands/next?token=device-secret",
        )
        self.assertEqual(status, 200)
        self.assertIn("command", result)

    def test_device_header_scopes_legacy_board_data_endpoint(self):
        other_id = "rk3506b-http-other"
        zhirun_server._devices[other_id] = {"source": "rk3506"}
        zhirun_server._latest_by_device[other_id] = {
            "_ts": zhirun_server.now() + 1, "soilMoist": 99.0
        }
        try:
            status, data, _ = self.request(
                "GET",
                "/data",
                headers={
                    "X-Device-Token": "device-secret",
                    "X-ZhiRun-Device": self.device_id,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["soilMoist"], 38.2)
        finally:
            zhirun_server._devices.pop(other_id, None)
            zhirun_server._latest_by_device.pop(other_id, None)

    def test_provisioned_username_can_use_password_login(self):
        account = self.auth.ensure_password_account("operator", "OperatorPass123")
        status, result, headers = self.request(
            "POST", "/auth/login/password", {"account": "operator", "password": "OperatorPass123"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["id"], account["user_id"])
        self.assertEqual(result["account_label"], "operator")
        self.assertIn("HttpOnly", headers["Set-Cookie"])

    def test_password_mode_disables_self_service_methods(self):
        with mock.patch.object(zhirun_server, "AUTH_MODE", "password"):
            status, result, _ = self.request(
                "POST", "/auth/code/request", {"phone": "13800138009", "purpose": "login"}
            )
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "auth_method_disabled")

    def test_app_version_is_public_and_cache_safe(self):
        status, result, headers = self.request("GET", "/app/version")
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertRegex(result["content_version"], r"^[0-9a-f]{16}$")
        self.assertIn("no-store", headers["Cache-Control"])

    def test_one_account_can_switch_between_multiple_bound_devices(self):
        devices = {
            "multi-device-a": {"source": "rk3506", "device_name": "温室 A"},
            "multi-device-b": {"source": "rk3506", "device_name": "温室 B"},
        }
        for device_id, metadata in devices.items():
            zhirun_server._devices[device_id] = metadata
            zhirun_server._latest_by_device[device_id] = {
                "_ts": zhirun_server.now(),
                "soilMoist": 21.0 if device_id.endswith("a") else 78.0,
            }
        try:
            codes = {}
            for device_id in devices:
                status, identity, _ = self.request(
                    "GET", "/device/identity?device_id=" + device_id,
                    headers={"X-Device-Token": "device-secret"},
                )
                self.assertEqual(status, 200)
                codes[device_id] = identity["code"]

            self.request("POST", "/auth/code/request", {"phone": "13800138002", "purpose": "login"})
            status, login, headers = self.request(
                "POST", "/auth/login/code", {"phone": "13800138002", "code": "123456"}
            )
            self.assertEqual(status, 200)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            csrf = {"Cookie": cookie, "X-CSRF-Token": login["csrf_token"]}
            for code in codes.values():
                status, _result, _ = self.request("POST", "/auth/device/bind", {"code": code}, csrf)
                self.assertEqual(status, 200)

            status, listed, _ = self.request("GET", "/auth/devices", headers={"Cookie": cookie})
            self.assertEqual(status, 200)
            self.assertEqual(listed["count"], 2)
            self.assertEqual({item["device_id"] for item in listed["devices"]}, set(devices))

            selected_headers = {"Cookie": cookie, "X-ZhiRun-Device": "multi-device-b"}
            status, data, _ = self.request("GET", "/data", headers=selected_headers)
            self.assertEqual(status, 200)
            self.assertEqual(data["soilMoist"], 78.0)
            status, selected, _ = self.request("GET", "/auth/devices", headers=selected_headers)
            self.assertEqual(status, 200)
            self.assertEqual(selected["selected_device_id"], "multi-device-b")
        finally:
            for device_id in devices:
                zhirun_server._devices.pop(device_id, None)
                zhirun_server._latest_by_device.pop(device_id, None)


if __name__ == "__main__":
    unittest.main()

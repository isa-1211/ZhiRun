import hashlib
import hmac
import json
import sys
import types
import unittest
from unittest import mock

if "termios" not in sys.modules:
    sys.modules["termios"] = types.SimpleNamespace(
        B2400=2400, B4800=4800, B9600=9600, B19200=19200,
        B38400=38400, B115200=115200,
    )

from edge import rk3506_collector as collector
from server import zhirun_server


class SrunProtocolTests(unittest.TestCase):
    def test_jsonp_response_is_decoded(self):
        value = collector._jsonp(b'callback_1({"error":"ok","res":"ok"})')
        self.assertEqual(value["error"], "ok")

    def test_login_uses_challenge_and_never_sends_plain_password(self):
        calls = []

        def request(path, params, timeout=12):
            calls.append((path, params))
            if path.endswith("get_challenge"):
                return {"challenge": "0123456789abcdef"}
            return {"error": "ok", "res": "ok", "suc_msg": "login_ok"}

        with mock.patch.object(collector, "_srun_request", side_effect=request):
            result = collector.srun_login("student01", "Secure!234", "10.1.2.3", "6")

        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][1], {"username": "student01", "ip": "10.1.2.3"})
        login = calls[1][1]
        expected_hmd5 = hmac.new(b"0123456789abcdef", b"Secure!234", hashlib.md5).hexdigest()
        self.assertEqual(login["password"], "{MD5}" + expected_hmd5)
        self.assertNotIn("Secure!234", json.dumps(login))
        self.assertTrue(login["info"].startswith("{SRBX1}"))
        checksum_source = "".join((
            "0123456789abcdef", "student01",
            "0123456789abcdef", expected_hmd5,
            "0123456789abcdef", "6",
            "0123456789abcdef", "10.1.2.3",
            "0123456789abcdef", "200",
            "0123456789abcdef", "1",
            "0123456789abcdef", login["info"],
        ))
        self.assertEqual(login["chksum"], hashlib.sha1(checksum_source.encode()).hexdigest())


class PortalStateTests(unittest.TestCase):
    def setUp(self):
        self.device_id = "campus-test"
        self.original_latest = dict(zhirun_server._latest_by_device)
        self.original_states = dict(zhirun_server._valve_by_device)
        self.original_attempts = dict(zhirun_server._network_attempt_by_device)

    def tearDown(self):
        zhirun_server._latest_by_device.clear()
        zhirun_server._latest_by_device.update(self.original_latest)
        zhirun_server._valve_by_device.clear()
        zhirun_server._valve_by_device.update(self.original_states)
        zhirun_server._network_attempt_by_device.clear()
        zhirun_server._network_attempt_by_device.update(self.original_attempts)

    def test_campus_wifi_is_not_successful_until_portal_is_authenticated(self):
        zhirun_server._latest_by_device[self.device_id] = {
            "_ts": zhirun_server.now(), "wifiConnected": True, "wifiSsid": "IMAU"
        }
        zhirun_server._valve_by_device[self.device_id] = {
            "portalAuthenticated": False, "lastCommandId": "42"
        }
        zhirun_server._network_attempt_by_device[self.device_id] = {
            "ssid": "IMAU", "started_at": zhirun_server.now(),
            "campus": True, "command_id": "42",
        }
        state = zhirun_server.valve_snapshot(self.device_id)
        self.assertEqual(state["networkAttempt"]["status"], "connecting")

        zhirun_server._valve_by_device[self.device_id]["portalAuthenticated"] = True
        state = zhirun_server.valve_snapshot(self.device_id)
        self.assertEqual(state["networkAttempt"]["status"], "success")


if __name__ == "__main__":
    unittest.main()

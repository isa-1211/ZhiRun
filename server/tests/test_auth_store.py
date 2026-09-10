import os
import tempfile
import unittest

from server.auth_store import AuthError, AuthStore


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = AuthStore(
            os.path.join(self.tempdir.name, "auth.sqlite3"),
            "test-secret",
            dev_code="123456",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, phone, purpose):
        result = self.store.request_code(phone, purpose)
        self.assertEqual(result["development_code"], "123456")

    def test_password_registration_login_and_session(self):
        self.request("13800138000", "register")
        user_id = self.store.register("13800138000", "123456", "Secure123")
        self.assertEqual(self.store.login_password("+86 13800138000", "Secure123"), user_id)
        with self.assertRaises(AuthError):
            self.store.login_password("13800138000", "wrong123")

        created = self.store.create_session(user_id, "test", "127.0.0.1")
        session = self.store.session(created["token"])
        self.assertEqual(session["user_id"], user_id)
        self.store.logout(created["token"])
        self.assertIsNone(self.store.session(created["token"]))

    def test_code_login_creates_account(self):
        self.request("13900139000", "login")
        user_id = self.store.login_code("13900139000", "123456")
        self.assertEqual(self.store.user_payload(user_id)["phone"], "13900139000")

    def test_binding_code_is_eight_digits_single_use_and_single_owner(self):
        self.request("13700137000", "login")
        first_user = self.store.login_code("13700137000", "123456")
        identity = self.store.identity_code("rk3506b-test")
        self.assertRegex(identity["code"], r"^\d{8}$")
        self.assertEqual(self.store.bind_device(first_user, identity["code"]), "rk3506b-test")
        self.assertEqual(self.store.device_ids(first_user), {"rk3506b-test"})
        self.assertTrue(self.store.identity_code("rk3506b-test")["bound"])

        self.request("13600136000", "login")
        second_user = self.store.login_code("13600136000", "123456")
        with self.assertRaises(AuthError):
            self.store.bind_device(second_user, identity["code"])

        self.store.unbind_device(first_user, "rk3506b-test")
        rotated = self.store.identity_code("rk3506b-test")
        self.assertNotEqual(rotated["code"], identity["code"])
        self.assertEqual(self.store.bind_device(second_user, rotated["code"]), "rk3506b-test")

    def test_first_wechat_login_requires_phone_link(self):
        pending = self.store.resolve_wechat("openid-a", "unionid-a", "测试用户")
        self.assertIsNone(pending["user_id"])
        self.request("13500135000", "wechat_link")
        user_id = self.store.link_pending_wechat(
            pending["pending_token"], "13500135000", "123456"
        )
        profile = self.store.user_payload(user_id)
        self.assertTrue(profile["wechat_linked"])
        self.assertEqual(profile["phone"], "13500135000")
        self.assertEqual(self.store.resolve_wechat("openid-a", "unionid-a")["user_id"], user_id)

    def test_sms_must_be_explicitly_configured(self):
        store = AuthStore(os.path.join(self.tempdir.name, "disabled.sqlite3"), "secret")
        with self.assertRaises(AuthError) as context:
            store.request_code("13400134000", "login")
        self.assertEqual(context.exception.code, "sms_not_configured")


if __name__ == "__main__":
    unittest.main()

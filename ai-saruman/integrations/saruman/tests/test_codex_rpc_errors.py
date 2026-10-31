"""RPC failures preserve actionable causes without printing credentials or replaying."""

import unittest
from unittest.mock import Mock

from saruman.codex_rpc import CodexRpcError, Rpc


class RpcErrorTests(unittest.TestCase):
    def fail_request(self, method, error):
        rpc = Rpc()
        rpc.send = Mock()
        rpc.incoming.put({"id": 1, "error": error})
        with self.assertRaises(CodexRpcError) as raised:
            rpc.request(method, {})
        rpc.send.assert_called_once_with({"id": 1, "method": method, "params": {}})
        return raised.exception

    def test_routing_failure_explains_network_and_isolated_profile_without_retry(self):
        error = self.fail_request(
            "account/read",
            {"code": -32603, "message": "workspace routing discovery failed"},
        )
        self.assertEqual(error.code, -32603)
        self.assertEqual(error.method, "account/read")
        for text in ("workspace routing discovery failed", "VPN", "CODEX_HOME", "codex login status"):
            self.assertIn(text, str(error))

    def test_other_methods_preserve_error_code_and_message(self):
        error = self.fail_request(
            "turn/start", {"code": -32602, "message": "Unsupported model effort"}
        )
        self.assertIn("Unsupported model effort", str(error))
        self.assertNotIn("codex login", str(error))

    def test_error_data_is_never_formatted(self):
        error = self.fail_request(
            "account/read",
            {"code": -32603, "message": "Access denied", "data": {"secret": "data-secret"}},
        )
        self.assertNotIn("data-secret", str(error))
        self.assertIn("Access denied", str(error))

    def test_credentials_urls_and_emails_are_redacted(self):
        secrets = (
            "bearer-secret", "key-secret", "access-secret", "refresh-secret",
            "id-secret", "sk-short-secret", "eyJhbGci.payload.signature",
            "x" * 80, "private@example.com", "https://example.com/?token=url-secret",
        )
        message = (
            'Authorization: Bearer bearer-secret api_key="key-secret" '
            'access_token=access-secret refresh_token: refresh-secret '
            'id_token=id-secret sk-short-secret eyJhbGci.payload.signature '
            + " ".join(secrets[7:])
        )
        error = self.fail_request("account/read", {"message": message})
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, str(error))
        self.assertIn("[REDACTED]", str(error))

    def test_malformed_errors_do_not_expose_nested_objects(self):
        for value in (None, "secret", {"message": {"secret": "nested-secret"}}):
            with self.subTest(value=value):
                error = self.fail_request("turn/start", value)
                self.assertIn("Codex не указал причину", str(error))
                self.assertNotIn("secret", str(error))

    def test_message_is_bounded_and_uses_one_line(self):
        error = self.fail_request("turn/start", {"message": "line\n" * 500})
        self.assertNotIn("\n", str(error))
        self.assertLess(len(str(error)), 650)


if __name__ == "__main__":
    unittest.main()

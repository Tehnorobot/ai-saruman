"""Exercise Windows subprocess stdin, never SSH or real key provisioning."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import setup_tunnel


class SetupTests(unittest.TestCase):
    def test_remote_script_reaches_child_with_lf_and_utf8(self):
        native_run = subprocess.run
        public_key = "ssh-ed25519 AAAA тестовый-ключ"
        received = []

        def ssh_stub(args, **kwargs):
            received.append(args)
            # Read actual stdin bytes in a real child process. On Windows the old
            # text=True path changes LF to CRLF and/or uses a locale encoding.
            child = (
                "import sys; data=sys.stdin.buffer.read(); "
                "assert data.startswith(b'set -eu\\n'), repr(data[:16]); "
                "assert b'\\r' not in data; "
                "assert 'ssh-ed25519 AAAA ' in data.decode('utf-8'); "
                "assert data.decode('utf-8').count(chr(1090)) > 0"
            )
            return native_run([sys.executable, "-c", child], **kwargs)

        with patch.object(setup_tunnel.subprocess, "run", side_effect=ssh_stub):
            setup_tunnel.install_public_key("192.0.2.1", 2222, public_key)
        self.assertEqual(received[0][-3:], ["root@192.0.2.1", "sh", "-s"])
        self.assertIn("StrictHostKeyChecking=yes", received[0])

    def run_main(self, directory, runner):
        root = Path(directory)
        key = root / "key"
        key.write_bytes(b"private fixture: unchanged")
        Path(str(key) + ".pub").write_text("ssh-ed25519 AAAA fixture", encoding="utf-8")
        config = root / ".env.client"
        config.write_text("GATEWAY_API_KEY=preserve-me\n", encoding="utf-8")
        argv = ["setup_tunnel.py", "--server", "192.0.2.1", "--key-file", str(key),
                "--env-file", str(config)]
        with patch.object(sys, "argv", argv), patch.object(setup_tunnel.subprocess, "run", runner), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            setup_tunnel.main()
        return key, config

    def test_retry_reuses_key_and_keeps_api_key(self):
        calls = []
        def successful(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with tempfile.TemporaryDirectory() as directory:
            key, config = self.run_main(directory, successful)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "ssh")
            self.assertEqual(key.read_bytes(), b"private fixture: unchanged")
            self.assertIn("GATEWAY_API_KEY=preserve-me", config.read_text())
            self.assertIn("GATEWAY_SSH_USER=telegram-tunnel", config.read_text())

    def test_failed_ssh_does_not_rewrite_config_or_private_key(self):
        def failed(args, **kwargs):
            raise subprocess.CalledProcessError(2, args)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as error:
                self.run_main(directory, failed)
            self.assertEqual(error.exception.code, 1)
            self.assertEqual((Path(directory) / ".env.client").read_text(), "GATEWAY_API_KEY=preserve-me\n")
            self.assertEqual((Path(directory) / "key").read_bytes(), b"private fixture: unchanged")


if __name__ == "__main__":
    unittest.main()

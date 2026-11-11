"""One-time, interactive SSH key setup. Run on the owner's PC, never by an LLM."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shlex
import subprocess


def remote_script(public_key):
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?", public_key):
        raise ValueError("Expected an Ed25519 public key")
    # This account cannot execute arbitrary commands or open a terminal. Its local
    # forwarding destination is fixed. No changes to sshd/root login configuration.
    authorized = 'restrict,port-forwarding,permitopen="127.0.0.1:8787",command="/bin/false" ' + public_key
    return """set -eu
if ! id telegram-tunnel >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir /var/lib/telegram-tunnel --shell /bin/sh telegram-tunnel
    usermod --password '*' telegram-tunnel
fi
install -d -m 700 -o telegram-tunnel -g telegram-tunnel /var/lib/telegram-tunnel/.ssh
touch /var/lib/telegram-tunnel/.ssh/authorized_keys
""" + "line=" + shlex.quote(authorized) + "\n" + """if ! grep -Fqx -- "$line" /var/lib/telegram-tunnel/.ssh/authorized_keys; then
    printf '%s\n' "$line" >> /var/lib/telegram-tunnel/.ssh/authorized_keys
fi
chown telegram-tunnel:telegram-tunnel /var/lib/telegram-tunnel/.ssh/authorized_keys
chmod 600 /var/lib/telegram-tunnel/.ssh/authorized_keys
printf 'Restricted tunnel account configured.\n'
"""


def update_config(path, values):
    raw = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = [line for line in raw.splitlines() if line.partition("=")[0].strip() not in values]
    lines.extend(key + "=" + str(value) for key, value in values.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_public_key(server, port, public_key):
    # Text-mode stdin translates LF into CRLF on Windows. Unix sh interprets the
    # extra CR in `set -eu\r` as an option character. Send literal UTF-8 bytes.
    script = remote_script(public_key).encode("utf-8")
    subprocess.run(["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=yes",
                    f"root@{server}", "sh", "-s"], input=script, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key-file", type=Path, default=Path.home() / ".ssh/saruman-telegram")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).parent / ".env.client")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", args.server) or not 1 <= args.port <= 65535:
        parser.error("Invalid server or port")
    key = args.key_file.resolve()
    key.parent.mkdir(parents=True, exist_ok=True)
    if not key.exists():
        # Dedicated unattended key. ssh-keygen creates the private file/Windows ACL;
        # it is never read by this script, sent to the VPS or printed.
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-f", str(key), "-N", "",
                        "-C", "saruman-telegram-tunnel"], check=True)
    public = Path(str(key) + ".pub")
    if not public.is_file():
        parser.error("Public key file missing; do not overwrite your existing private key")
    print("Connecting once as root to install the RESTRICTED public key. Enter the VPS password if prompted.")
    try:
        install_public_key(args.server, args.port, public.read_text(encoding="utf-8").strip())
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"SSH setup failed (exit {error.returncode}). Client configuration was not changed. "
                    "Check the SSH error above and rerun; the existing key will be reused.\n")
    update_config(args.env_file, {"GATEWAY_SSH_HOST": args.server, "GATEWAY_SSH_PORT": args.port,
                                 "GATEWAY_SSH_USER": "telegram-tunnel", "GATEWAY_SSH_IDENTITY_FILE": key})
    print("SSH configuration saved. Keep your existing GATEWAY_API_KEY. The UI can now manage the tunnel.")


if __name__ == "__main__":
    main()

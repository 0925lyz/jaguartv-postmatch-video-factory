from __future__ import annotations

import os
import subprocess


class CredentialUnavailable(RuntimeError):
    pass


def keychain_secret(service: str) -> str:
    account = os.environ.get("USER") or os.environ.get("LOGNAME") or "jaguar"
    completed = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-a", account, "-s", service, "-w"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    value = completed.stdout.strip() if completed.returncode == 0 else ""
    if not value:
        raise CredentialUnavailable(f"credential {service} is unavailable in the macOS Keychain")
    return value


def env_or_keychain(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or keychain_secret(name)

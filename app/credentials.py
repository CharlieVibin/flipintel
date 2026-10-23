"""Server-side credential management.

Marketplace API keys must never reach the browser bundle, the SQLite database
or a version-controlled file. The dashboard writes them through this module,
which updates the existing gitignored ``.env`` atomically with owner-only file
permissions and updates the running process environment immediately.

Values are never returned by the API. ``status()`` exposes only whether a key is
present, its environment and a short hint derived from the non-secret client ID.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = BASE_DIR / ".env"

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9._\-]{8,200}$")
_ENVIRONMENTS = {"production", "sandbox"}

EBAY_KEYS = ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV")


@dataclass(frozen=True)
class EbayCredentials:
    client_id: str
    client_secret: str
    environment: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def fingerprint(self) -> str:
        # A stable non-reversible identity used only to invalidate a cached
        # OAuth token after the credentials are rotated.
        import hashlib

        return hashlib.sha256(f"{self.client_id}\0{self.client_secret}".encode()).hexdigest()[:16]


class CredentialStore:
    def __init__(self, env_file: Path | str | None = None) -> None:
        self.env_file = Path(env_file or DEFAULT_ENV_FILE)

    # --- reading ------------------------------------------------------------
    def _file_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            lines = self.env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return values
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _KEY_RE.match(line)
            if not match:
                continue
            raw = match.group(2).strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
                raw = raw[1:-1]
            values[match.group(1)] = raw
        return values

    def _value(self, key: str) -> str:
        # The live process environment wins, so keys set by the host (or by a
        # previous save in this process) take effect without a restart.
        if key in os.environ:
            return os.environ.get(key, "").strip()
        return self._file_values().get(key, "").strip()

    @property
    def ebay(self) -> EbayCredentials:
        environment = self._value("EBAY_ENV").lower() or "production"
        if environment not in _ENVIRONMENTS:
            environment = "production"
        return EbayCredentials(
            client_id=self._value("EBAY_CLIENT_ID"),
            client_secret=self._value("EBAY_CLIENT_SECRET"),
            environment=environment,
        )

    @property
    def ebay_configured(self) -> bool:
        credentials = self.ebay
        return credentials.configured

    def status(self) -> dict[str, object]:
        credentials = self.ebay
        source = "environment"
        if self._file_values().get("EBAY_CLIENT_ID") or self._file_values().get("EBAY_CLIENT_SECRET"):
            source = "local .env"
        client_id = credentials.client_id
        hint = f"••••{client_id[-4:]}" if len(client_id) >= 8 else None
        return {
            "configured": credentials.configured,
            "environment": credentials.environment,
            "client_id_hint": hint,
            "credential_source": source if credentials.configured else None,
            "storage": "local .env (gitignored, owner-only permissions)",
            "can_clear": source == "local .env",
        }

    # --- writing ------------------------------------------------------------
    def save_ebay(self, client_id: str, client_secret: str, environment: str = "production") -> dict[str, object]:
        client_id = (client_id or "").strip()
        client_secret = (client_secret or "").strip()
        environment = (environment or "production").strip().lower()
        if not _VALUE_RE.match(client_id):
            raise ValueError("Client ID must be 8–200 characters and contain no spaces or newlines")
        if not _VALUE_RE.match(client_secret):
            raise ValueError("Client secret must be 8–200 characters and contain no spaces or newlines")
        if environment not in _ENVIRONMENTS:
            raise ValueError("Environment must be production or sandbox")
        self._write({**os.environ, "EBAY_CLIENT_ID": client_id, "EBAY_CLIENT_SECRET": client_secret, "EBAY_ENV": environment})
        os.environ["EBAY_CLIENT_ID"] = client_id
        os.environ["EBAY_CLIENT_SECRET"] = client_secret
        os.environ["EBAY_ENV"] = environment
        return self.status()

    def clear_ebay(self) -> dict[str, object]:
        self._write({key: value for key, value in os.environ.items() if key not in EBAY_KEYS})
        for key in EBAY_KEYS:
            os.environ.pop(key, None)
        return self.status()

    def _write(self, values: dict[str, str]) -> None:
        """Rewrite .env preserving unrelated lines and comments.

        Only the three eBay keys are changed. Unknown content, including other
        providers' secrets, is left byte-for-byte intact.
        """
        updates = {
            "EBAY_CLIENT_ID": values.get("EBAY_CLIENT_ID", ""),
            "EBAY_CLIENT_SECRET": values.get("EBAY_CLIENT_SECRET", ""),
            "EBAY_ENV": values.get("EBAY_ENV", "production"),
        }
        try:
            original = self.env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            original = []
        output: list[str] = []
        seen: set[str] = set()
        for line in original:
            match = _KEY_RE.match(line)
            if match and match.group(1) in updates:
                key = match.group(1)
                seen.add(key)
                value = updates[key]
                if value:
                    output.append(f"{key}={value}")
                continue
            output.append(line)
        for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV"):
            if key not in seen and updates[key]:
                output.append(f"{key}={updates[key]}")
        content = "\n".join(output).rstrip() + "\n"
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=str(self.env_file.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.env_file)
            try:
                os.chmod(self.env_file, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


credential_store = CredentialStore()

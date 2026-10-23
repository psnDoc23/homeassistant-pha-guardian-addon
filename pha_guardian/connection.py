"""Remember which website account this app is linked to.

The credential lives in the app's own persistent data folder, so it survives restarts
and updates, and the user never has to copy it anywhere.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONNECTION_FILE = "website_connection.json"


def default_data_dir() -> Path:
    return Path(os.environ.get("GUARDIAN_DATA_DIR", "/data"))


class ConnectionStore:
    def __init__(self, data_dir: Path | None = None):
        self.path = (data_dir or default_data_dir()) / CONNECTION_FILE

    def load(self) -> dict | None:
        """The saved link, or None. An unreadable file counts as not linked."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("token"):
            return data
        return None

    def save(self, token: str, account: str = "") -> None:
        if not token:
            raise ValueError("A token is required to save a website connection")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": token,
            "account": account or "",
            "linked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        # Write to a private temp file first so a crash never leaves half a credential.
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".conn-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

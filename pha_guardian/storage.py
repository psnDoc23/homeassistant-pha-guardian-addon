# storage.py

import json
import os

STORAGE_PATH = "/data/guardian_config.json"


def load_monitored_ids() -> list:
    """Reads the list of monitored automation IDs from persistent storage."""
    try:
        if not os.path.exists(STORAGE_PATH):
            return []
        with open(STORAGE_PATH, "r") as f:
            data = json.load(f)
            return data.get("monitored_automation_ids", [])
    except Exception:
        return []


def save_monitored_ids(ids: list) -> bool:
    """Saves the list of monitored automation IDs to persistent storage."""
    try:
        os.makedirs(os.path.dirname(STORAGE_PATH), exist_ok=True)
        with open(STORAGE_PATH, "w") as f:
            json.dump({"monitored_automation_ids": ids}, f)
        return True
    except Exception:
        return False
import time
import requests
import os

# Home Assistant Supervisor event endpoint
SUPERVISOR_EVENT_URL = "http://supervisor/core/api/events/pha_guardian_api_status"
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")


def fire_event(payload: dict):
    """Send an event to Home Assistant with the given payload."""
    if not SUPERVISOR_TOKEN:
        print("SUPERVISOR_TOKEN not available; cannot fire HA event.")
        return

    try:
        headers = {
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        }
        requests.post(SUPERVISOR_EVENT_URL, json={"data": payload}, headers=headers)
    except Exception as e:
        print(f"Failed to fire Home Assistant event: {e}")


def check_api_availability(api_url: str, timeout: int = 5) -> dict:
    """Check Guardian API availability and fire a Home Assistant event."""
    try:
        start = time.time()
        response = requests.get(api_url, timeout=timeout)
        latency_ms = int((time.time() - start) * 1000)

        result = {
            "status": "ok" if response.status_code == 200 else "error",
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        }

    except requests.exceptions.RequestException as e:
        result = {
            "status": "unreachable",
            "error": str(e),
        }

    # Emit event to Home Assistant
    fire_event(result)

    return result

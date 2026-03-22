# checkers/usb_path.py

USB_ERROR_PATTERNS = [
    "/dev/ttyUSB",
    "/dev/ttyACM",
    "Could not open",
    "No such file or directory",
]

async def check_usb_path(supervisor) -> list:
    """
    Scans HA core logs for signs of USB device path errors.
    Returns a list with one issue dict if a problem is detected, empty list if not.
    """
    try:
        data = await supervisor._get_text("/core/logs")
        logs = data.get("logs", "")

        for pattern in USB_ERROR_PATTERNS:
            if pattern in logs:
                return [{
                    "id": "usb_path_error",
                    "title": "Zigbee/Z-Wave USB device not found",
                    "severity": "high",
                    "detail": (
                        "A USB device path error was detected in HA logs. "
                        "If you recently rebooted, your device may have re-enumerated "
                        "to a different path. Consider switching to a persistent path "
                        "like /dev/serial/by-id/..."
                    )
                }]

        return []

    except Exception as e:
        return [{
            "id": "usb_path_check_failed",
            "title": "USB path check failed",
            "severity": "unknown",
            "detail": str(e)
        }]
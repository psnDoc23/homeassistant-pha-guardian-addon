# checkers/disk_space.py

LOW_DISK_THRESHOLD_PERCENT = 10

async def check_disk_space(supervisor) -> list:
    """
    Calls the Supervisor /host/info endpoint and checks free disk space.
    Returns a list with one issue dict if disk is low, empty list if not.
    """
    try:
        data = await supervisor._get("/host/info")
        disk_free = data.get("data", {}).get("disk_free")
        disk_total = data.get("data", {}).get("disk_total")

        if disk_free is None or disk_total is None:
            return []

        percent_free = (disk_free / disk_total) * 100

        if percent_free < LOW_DISK_THRESHOLD_PERCENT:
            return [{
                "id": "disk_space_low",
                "title": "Low disk space",
                "severity": "high",
                "detail": f"{percent_free:.1f}% free ({disk_free}GB of {disk_total}GB)"
            }]

        return []

    except Exception as e:
        return [{
            "id": "disk_space_check_failed",
            "title": "Disk space check failed",
            "severity": "unknown",
            "detail": str(e)
        }]


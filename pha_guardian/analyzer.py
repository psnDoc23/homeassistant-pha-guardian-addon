# analyzer.py

def analyze_dropouts(entries: list) -> dict:
    dropouts = []

    for i in range(len(entries) - 1):
        current = entries[i]
        next_entry = entries[i + 1]

        if current.get("state") == "unavailable" and next_entry.get("state") == "on":
            duration_seconds = (
                _parse(next_entry["when"]) - _parse(current["when"])
            ).total_seconds()

            dropouts.append({
                "started": current["when"],
                "recovered": next_entry["when"],
                "duration_seconds": duration_seconds,
            })

    return {
        "entity_id": entries[0]["entity_id"] if entries else None,
        "total_dropouts": len(dropouts),
        "dropouts": dropouts,
    }


def _parse(when: str):
    from datetime import datetime
    return datetime.fromisoformat(when)



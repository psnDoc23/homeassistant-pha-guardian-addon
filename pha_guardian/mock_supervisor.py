from fastapi import APIRouter

router = APIRouter()

@router.get("/core/api/states")
async def mock_states():
    return [
        {"entity_id": "light.kitchen", "state": "on"},
        {"entity_id": "sensor.temperature", "state": "72"},
        {"entity_id": "switch.garage", "state": "off"},
    ]

@router.get("/core/info")
async def mock_info():
    return {
        "result": "ok",
        "data": {
            "version": "2025.1.0",
            "healthy": True,
            "supported": True,
            "supervisor": "2025.1.0",
        }
    }

@router.get("/core/logs")
async def mock_logs():
    return {
        "logs": [
            "Mock Supervisor Log Entry 1",
            "Mock Supervisor Log Entry 2",
            "Mock Supervisor Log Entry 3",
        ]
    }


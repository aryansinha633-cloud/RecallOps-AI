from __future__ import annotations

from backend.app.deps import get_investigation_service, get_memory_service
from backend.app.main import app
from backend.app.services.band_service import BandService
from backend.app.services.investigation_service import InvestigationService
from backend.app.services.llm_service import LLMService
from backend.app.config import Settings


chinarrrrrrr

class FakeRetainingMemory:
    def retain_incident(self, db, incident):
        incident.memory_retained = True
        db.commit()
        return {"status": "success", "operation_id": 999}


def _incident_id(client, code="INC-007"):
    incidents = client.get("/api/incidents").json()
    return next(item["id"] for item in incidents if item["incident_code"] == code)


def _service(memory):
    settings = Settings()
    return InvestigationService(
        settings, memory, BandService(settings), LLMService(settings)
    )


def test_health_reports_explicit_unconfigured_state(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["llm"]["configured"] is False
    assert body["llm"]["source"] == "missing"
    assert body["band"]["configured"] is False


def test_seeded_incidents_are_synthetic_and_complete(client):
    incidents = client.get("/api/incidents").json()
    assert len(incidents) == 8
    assert {item["incident_code"] for item in incidents} == {f"INC-{i:03d}" for i in range(1, 9)}
    assert all(item["is_synthetic"] for item in incidents)
    assert any(item["service"] == "payments-api" for item in incidents)


def test_patterns_and_dashboard(client):
    patterns = client.get("/api/analytics/patterns").json()
    assert patterns["totals"]["incidents"] == 8
    assert patterns["totals"]["recurring_services"] >= 1

    dashboard = client.get("/api/dashboard/summary").json()
    assert dashboard["total_incidents"] == 8
    assert dashboard["resolved_incidents"] >= 1
    assert dashboard["memory_health"] in {"healthy", "degraded", "unavailable"}


def test_memory_on_investigation_uses_recalled_evidence(client):
    memory = FakeMemory(
        [
            {
                "id": "mem-1",
                "text": "INC-001 payments-api pool exhaustion was fixed by raising DB_POOL_MAX_SIZE to 50.",
                "score": 0.91,
                "metadata": {"incident_code": "INC-001"},
            }
        ]
    )
    app.dependency_overrides[get_investigation_service] = lambda: _service(memory)
    incident_id = _incident_id(client, "INC-007")

    response = client.post(
        f"/api/incidents/{incident_id}/investigate", json={"memory_enabled": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["memory_status"] == "used"
    assert len(body["recalled_memories"]) == 1
    assert body["confidence"] == 0.92
    assert body["llm_provider"] == "local-deterministic"
    assert len(body["agent_runs"]) == 2
    assert {run["agent_name"] for run in body["agent_runs"]} == {"Tom", "Jerry"}


def test_memory_off_never_calls_recall(client):
    app.dependency_overrides[get_investigation_service] = lambda: _service(MemoryMustNotBeCalled())
    incident_id = _incident_id(client, "INC-007")

    response = client.post(
        f"/api/incidents/{incident_id}/investigate", json={"memory_enabled": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["memory_status"] == "disabled"
    assert body["recalled_memories"] == []
    assert body["confidence"] == 0.64


def test_comparison_reports_measured_delta(client):
    memory = FakeMemory(
        [
            {
                "id": "mem-1",
                "text": "INC-001 payments-api pool exhaustion was fixed by raising DB_POOL_MAX_SIZE to 50.",
                "score": 0.91,
                "metadata": {"incident_code": "INC-001"},
            }
        ]
    )
    app.dependency_overrides[get_investigation_service] = lambda: _service(memory)
    incident_id = _incident_id(client, "INC-007")

    response = client.post(f"/api/incidents/{incident_id}/compare")
    assert response.status_code == 200
    body = response.json()
    assert body["memory_off"]["memory_status"] == "disabled"
    assert body["memory_on"]["memory_status"] == "used"
    assert body["memories_used"] == 1
    assert body["delta_confidence"] == 0.28


def test_created_incident_can_be_resolved_and_retained(client):
    app.dependency_overrides[get_memory_service] = lambda: FakeRetainingMemory()
    created = client.post(
        "/api/incidents",
        json={
            "title": "Test incident for resolution path",
            "service": "test-service",
            "severity": "SEV-3",
            "description": "Synthetic test incident used to verify the resolution and retain path.",
            "symptoms": ["elevated latency"],
        },
    )
    assert created.status_code == 201
    incident_id = created.json()["id"]

    response = client.post(
        f"/api/incidents/{incident_id}/resolve",
        json={
            "root_cause": "Synthetic root cause",
            "resolution_summary": "Synthetic fix",
            "resolution_steps": ["step one"],
            "retain_to_memory": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["incident"]["status"] == "resolved"
    assert body["incident"]["memory_retained"] is True
    assert body["memory"]["status"] == "success"

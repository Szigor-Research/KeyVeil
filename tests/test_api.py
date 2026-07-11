from fastapi.testclient import TestClient

from demo.web_server import app

client = TestClient(app)


def test_scenario_catalog_is_synthetic_and_bounded():
    response = client.get("/api/scenarios")

    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert len(scenarios) == 9
    assert {item["budget_scope_id"] for item in scenarios} == {"reference-organization"}
    assert {item["expected_status"] for item in scenarios} == {
        "approved",
        "blocked",
        "pending_human",
    }
    assert response.headers["cache-control"] == "no-store"


def test_simulation_returns_scoped_trace_and_hashed_receipt():
    response = client.post("/api/simulate", json={"scenario": "approve_small"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "synthetic-reference"
    assert payload["trace"]
    assert payload["receipt"]["status"] == "approved"
    assert payload["receipt"]["schema_version"] == "keyveil.receipt.v2"
    assert payload["receipt"]["intent_schema_version"] == "keyveil.intent.v1"
    assert len(payload["receipt"]["intent_hash"]) == 64
    assert len(payload["receipt"]["receipt_hash"]) == 64
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_unknown_scenario_does_not_echo_internal_error_details():
    response = client.post("/api/simulate", json={"scenario": "does_not_exist"})

    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "unknown synthetic scenario"}


def test_simulation_rejects_unexpected_request_fields():
    response = client.post(
        "/api/simulate",
        json={"scenario": "approve_small", "execute": True},
    )

    assert response.status_code == 422

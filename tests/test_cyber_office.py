from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from project_supervisor.api import APISettings, create_app
from project_supervisor.autonomous_host import AutonomousHostRepository
from project_supervisor.autonomy import GoalService
from project_supervisor.heartbeat import HeartbeatConfig, MetabolicHeartbeat, SleepPolicy, utc_time
from project_supervisor.memory import MemoryRepository
from project_supervisor.store import StateStore, timestamp


@pytest.fixture
def office_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "office.db")
    store.create_project(project_id="p", name="Office", root_path=str(tmp_path), goal="Observe")
    GoalService(store).create_goal(project_id="p", intent="Read-only review", goal_id="g")
    return store


def test_static_office_is_public_but_cannot_access_state_or_write(office_store):
    before = office_store.highest_event_sequence()
    with TestClient(create_app(office_store)) as client:
        for asset in ("", "tokens.css", "office.css", "office.js", "art.js", "mark.svg"):
            response = client.get(f"/office/{asset}")
            assert response.status_code == 200
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert "'unsafe-inline'" not in response.headers["content-security-policy"]
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["cache-control"] == "no-cache"
        assert client.post("/office/index.html").status_code == 405
        assert client.get("/office/%2e%2e/store.py").status_code == 404
        assert client.get("/office/.env").status_code == 404
        assert client.get("/v1/memory").status_code == 401
        assert client.get("/v1/goals").status_code == 401
    assert office_store.highest_event_sequence() == before


def test_office_rest_projections_share_canonical_sleep_and_memory(office_store):
    hosts = AutonomousHostRepository(office_store)
    hosts.register_host("office-host", process_id=1, metadata={"leaseTTLSeconds": 30.0})
    hosts.heartbeat_host("office-host", active_goal_count=0)
    heartbeat = MetabolicHeartbeat(office_store)
    now = datetime.now(UTC)
    heartbeat.enter_sleep(
        "g", SleepPolicy(wake_reason="timer", next_wake_at=utc_time(now + timedelta(hours=1)))
    )
    heartbeat.tick("office-host", config=HeartbeatConfig(), lease_ttl_seconds=30.0, now=now)
    GoalService(office_store).create_goal(project_id="p", intent="Fixture outcome", goal_id="done")
    GoalService(office_store).stop("done", reason="A canonical outcome")
    MemoryRepository(office_store).reflect_terminal_goals()
    before = office_store.highest_event_sequence()
    with TestClient(create_app(office_store, allow_unauthenticated_loopback=True)) as client:
        goals = client.get("/v1/goals").json()["data"]
        assert next(g for g in goals if g["id"] == "g")["wakeState"] == "sleeping"
        tick = client.get("/v1/autonomy/heartbeat").json()["data"]["ticks"][0]
        assert tick["result"] == "NOOP" and tick["local_tokens"] == 0
        memory = client.get("/v1/memory").json()["data"]
        assert memory["authority"] == "advisoryOnly"
        assert memory["entries"][0]["source_goal_id"] == "done"
        assert len(memory["entries"][0]["content_sha256"]) == 64
        for endpoint in (
            "workers",
            "autonomy/hosts",
            "autonomy/leases",
            "events",
            "exploration",
            "bridge",
            "resources/snapshots",
        ):
            assert client.get(f"/v1/{endpoint}").status_code == 200
        capabilities = client.get("/v1/capabilities").json()["data"]
        assert capabilities["mutations"] == []
        assert "metabolicHeartbeat" in capabilities["resources"]
    assert office_store.highest_event_sequence() == before


def test_browser_stream_first_frame_auth_and_live_control_receipt(office_store):
    _, token = office_store.issue_api_token(
        label="test only", scopes={"observe:read", "goal:control"}
    )
    settings = APISettings(allow_goal_mutations=True, poll_interval_seconds=0.01)
    with (
        TestClient(create_app(office_store, settings)) as client,
        client.websocket_connect("/v1/stream", subprotocols=["chips-observe-v1"]) as stream,
    ):
        assert stream.accepted_subprotocol == "chips-observe-v1"
        stream.send_json({"type": "authenticate", "token": token})
        assert stream.receive_json()["type"] == "snapshot"
        response = client.post(
            "/v1/goals/g/pause",
            json={"mode": "hard", "reason": "Human brake"},
            headers={"Authorization": f"Bearer {token}"},
        )
        receipt = response.json()["data"]
        assert receipt["state"] == "hardPaused"
        assert GoalService(office_store).get_goal("g")["version"] == receipt["version"]
        # Replay and live frames may overlap; only journaled event IDs are authoritative.
        observed = []
        for _ in range(5):
            frame = stream.receive_json()
            if frame["type"] == "events":
                observed.extend(frame["events"])
            if any(e["message"] == "Goal hard paused" for e in observed):
                break
            if any("Human brake" in str(e) for e in observed):
                break
        assert any("Human brake" in str(e) or "hard paused" in e["message"] for e in observed)
        assert any(e["kind"] == "goalPaused" for e in office_store.list_events(limit=100))


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "authenticate", "token": "invalid"},
        {"type": "authenticate", "token": 123},
        {"type": "authenticate", "token": "", "scope": "*"},
        {"type": "authenticate", "token": "x" * 9000},
        {"type": "snapshot", "token": ""},
    ],
)
def test_browser_stream_invalid_auth_fails_before_any_snapshot(office_store, frame):
    with (
        TestClient(create_app(office_store)) as client,
        client.websocket_connect("/v1/stream", subprotocols=["chips-observe-v1"]) as stream,
    ):
        stream.send_json(frame)
        with pytest.raises(WebSocketDisconnect) as error:
            stream.receive_json()
        assert error.value.code == 4403


def test_browser_stream_origin_and_query_tokens_are_rejected(office_store):
    _, token = office_store.issue_api_token(label="test only", scopes={"observe:read"})
    with TestClient(create_app(office_store)) as client:
        with (
            pytest.raises(WebSocketDisconnect) as error,
            client.websocket_connect(
                "/v1/stream",
                subprotocols=["chips-observe-v1"],
                headers={"Origin": "https://untrusted.invalid"},
            ),
        ):
            pass
        assert error.value.code == 4403
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"/v1/stream?token={token}"),
        ):
            pass


def test_browser_stream_revocation_disconnects(office_store):
    token_id, token = office_store.issue_api_token(label="test only", scopes={"observe:read"})
    settings = APISettings(poll_interval_seconds=0.01)
    with (
        TestClient(create_app(office_store, settings)) as client,
        client.websocket_connect("/v1/stream", subprotocols=["chips-observe-v1"]) as stream,
    ):
        stream.send_json({"type": "authenticate", "token": token})
        assert stream.receive_json()["type"] == "snapshot"
        with office_store.transaction() as connection:
            connection.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE id=?", (timestamp(), token_id)
            )
        with pytest.raises(WebSocketDisconnect) as error:
            while True:
                stream.receive_json()
        assert error.value.code == 4403


def test_browser_dev_stream_does_not_grant_human_controls(office_store):
    settings = APISettings(allow_goal_mutations=True, allow_unauthenticated_loopback=True)
    with (
        TestClient(create_app(office_store, settings)) as client,
        client.websocket_connect("/v1/stream", subprotocols=["chips-observe-v1"]) as stream,
    ):
        stream.send_json({"type": "authenticate", "token": ""})
        assert stream.receive_json()["type"] == "snapshot"
        assert client.post("/v1/goals/g/stop", json={"reason": "no token"}).status_code == 401
        _, observe = office_store.issue_api_token(label="test only", scopes={"observe:read"})
        assert (
            client.post(
                "/v1/goals/g/stop",
                json={"reason": "read only"},
                headers={"Authorization": f"Bearer {observe}"},
            ).status_code
            == 403
        )
    assert GoalService(office_store).get_goal("g")["state"] == "created"


def test_stream_has_no_post_authentication_command_channel(office_store):
    with (
        TestClient(create_app(office_store, allow_unauthenticated_loopback=True)) as client,
        client.websocket_connect("/v1/stream", subprotocols=["chips-observe-v1"]) as stream,
    ):
        stream.send_json({"type": "authenticate", "token": ""})
        assert stream.receive_json()["type"] == "snapshot"
        stream.send_json({"type": "stop", "goalID": "g"})
        with pytest.raises(WebSocketDisconnect) as error:
            while True:
                stream.receive_json()
        assert error.value.code == 4400
    assert GoalService(office_store).get_goal("g")["state"] == "created"

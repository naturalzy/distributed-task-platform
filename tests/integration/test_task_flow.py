"""Second-stage API flow against the local Compose stack and its real MySQL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DOCKER_E2E") != "1",
        reason="Set RUN_DOCKER_E2E=1 to test the local Compose API and MySQL",
    ),
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Runs in the API container using its database settings. Only SELECTs are issued;
# each snapshot opens a new connection to MySQL, independent of HTTP responses.
MYSQL_SNAPSHOT = """
import json
import sys
from sqlalchemy import create_engine, text
from app.core.config import get_settings

engine = create_engine(get_settings().sync_database_url)
assert engine.dialect.name == 'mysql', 'This test requires real MySQL'
try:
    with engine.connect() as connection:
        row = connection.execute(text('''
            SELECT t.task_uuid, t.status, t.task_type, t.parameters, t.result,
                   t.error_message, t.execution_attempts, t.created_at,
                   t.started_at, t.finished_at,
                   t.lease_token IS NOT NULL AS has_lease,
                   t.lease_expires_at, u.public_id AS owner_public_id,
                   o.id IS NOT NULL AS has_outbox, o.sent_at AS outbox_sent_at
            FROM tasks AS t
            JOIN users AS u ON u.id = t.user_id
            LEFT JOIN outbox_events AS o ON o.task_id = t.id
            WHERE t.task_uuid = :task_uuid
        '''), {'task_uuid': sys.argv[1]}).mappings().one()
        snapshot = dict(row)
        for field in ('parameters', 'result'):
            if isinstance(snapshot[field], str):
                snapshot[field] = json.loads(snapshot[field])
        print(json.dumps(snapshot, default=str))
finally:
    engine.dispose()
"""


def compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(PROJECT_ROOT / "docker-compose.yml"), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        pytest.fail(f"Docker command failed ({' '.join(args)}): {result.stderr.strip()}")
    return result.stdout


def mysql_snapshot(task_id: str) -> dict:
    # Validate the external identifier before passing it as a process argument.
    uuid.UUID(task_id)
    return json.loads(compose("exec", "-T", "api", "python", "-c", MYSQL_SNAPSHOT, task_id))


@pytest.fixture(scope="module")
def client():
    if shutil.which("docker") is None:
        pytest.fail("Docker Compose is required; no simulated services are used")
    base_url = os.getenv("INTEGRATION_API_URL", "http://localhost:8000/api/v1")
    parsed = urlsplit(base_url)
    assert parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}, (
        "This test is limited to the local HTTP API"
    )
    running = set(compose("ps", "--services", "--status", "running").splitlines())
    required = {"api", "mysql", "redis", "dispatcher", "worker"}
    assert required <= running, f"Compose services are not running: {required - running}"
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as live_client:
        live = live_client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        ready = live_client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "checks": {"mysql": "ok", "redis": "ok"}}
        yield live_client


@pytest.fixture(scope="module")
def accounts(client):
    accounts = []
    for label in ("owner", "other"):
        username = f"e2e-{label}-{uuid.uuid4().hex[:12]}"
        password = f"Test-{uuid.uuid4().hex}"
        registration = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": password},
        )
        assert registration.status_code == 201, registration.text
        user = registration.json()
        assert user["username"] == username
        assert "password_hash" not in user
        login = client.post("/auth/login", data={"username": username, "password": password})
        assert login.status_code == 200, login.text
        assert login.json()["token_type"] == "bearer"
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        me = client.get("/users/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["public_id"] == user["public_id"]
        accounts.append({"user": user, "headers": headers})
    return accounts


def submit_task(client: httpx.Client, account: dict, numbers: list[int], delay: int) -> str:
    response = client.post(
        "/tasks",
        headers=account["headers"],
        json={
            "task_type": "sum_numbers",
            "parameters": {"numbers": numbers, "delay_seconds": delay},
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PENDING"
    task_id = response.json()["task_id"]
    uuid.UUID(task_id)
    return task_id


def wait_for_status(client: httpx.Client, task_id: str, account: dict, wanted: str) -> dict:
    deadline = time.monotonic() + 45
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}", headers=account["headers"])
        assert response.status_code == 200, response.text
        last = response.json()
        assert last["task_id"] == task_id
        assert last["status"] in {"PENDING", "RUNNING", "SUCCESS", "FAILED"}
        if last["status"] == wanted:
            return last
        assert last["status"] != "FAILED", f"Task unexpectedly failed: {last}"
        assert not (wanted == "RUNNING" and last["status"] == "SUCCESS"), (
            "Task completed before RUNNING could be observed"
        )
        time.sleep(0.1)
    pytest.fail(f"Task did not reach {wanted}: {last}")


def test_register_login_submit_status_result_persisted_in_mysql(client, accounts) -> None:
    owner = accounts[0]
    numbers = [2, 3, 5]
    # Longer than the default lease: real Worker heartbeats must keep it alive.
    task_id = submit_task(client, owner, numbers, delay=15)
    wait_for_status(client, task_id, owner, "RUNNING")
    pending_result = client.get(f"/tasks/{task_id}/result", headers=owner["headers"])
    assert pending_result.status_code == 202
    assert pending_result.json()["result"] is None
    assert pending_result.json()["error_message"] is None

    running = mysql_snapshot(task_id)
    assert running["task_uuid"] == task_id
    assert running["status"] == "RUNNING"
    assert running["owner_public_id"] == owner["user"]["public_id"]
    assert running["parameters"] == {"numbers": numbers, "delay_seconds": 15}
    assert running["has_outbox"]
    assert running["execution_attempts"] == 1
    assert running["has_lease"]
    assert running["lease_expires_at"] is not None
    assert running["started_at"] is not None
    assert running["finished_at"] is None

    status = wait_for_status(client, task_id, owner, "SUCCESS")
    response = client.get(f"/tasks/{task_id}/result", headers=owner["headers"])
    assert response.status_code == 200
    result = response.json()
    assert result == {
        "task_id": task_id,
        "status": "SUCCESS",
        "result": {"sum": 10, "count": 3},
        "error_message": None,
    }
    persisted = mysql_snapshot(task_id)
    assert persisted["status"] == "SUCCESS"
    assert persisted["task_type"] == "sum_numbers"
    assert persisted["owner_public_id"] == owner["user"]["public_id"]
    assert persisted["parameters"] == running["parameters"]
    assert persisted["result"] == result["result"]
    assert persisted["error_message"] is None
    assert persisted["execution_attempts"] == 1
    assert not persisted["has_lease"]
    assert persisted["lease_expires_at"] is None
    assert persisted["outbox_sent_at"] is not None
    for field in ("created_at", "started_at", "finished_at"):
        assert datetime.fromisoformat(persisted[field]) == datetime.fromisoformat(status[field])
    assert datetime.fromisoformat(persisted["started_at"]) <= datetime.fromisoformat(
        persisted["finished_at"]
    )
    print(f"MySQL confirmed RUNNING -> SUCCESS: task_id={task_id}, attempts=1, sum=10, count=3")


def test_users_can_only_read_their_own_tasks(client, accounts) -> None:
    task_ids = [submit_task(client, account, [-4, 0, 4, 4], delay=3) for account in accounts]
    for index, account in enumerate(accounts):
        task_id = task_ids[index]
        other = accounts[1 - index]
        own_status = client.get(f"/tasks/{task_id}", headers=account["headers"])
        assert own_status.status_code == 200
        assert own_status.json()["status"] in {"PENDING", "RUNNING"}
        for suffix in ("", "/result"):
            path = f"/tasks/{task_id}{suffix}"
            assert client.get(path).status_code == 401
            assert client.get(path, headers=other["headers"]).status_code == 404

    for index, account in enumerate(accounts):
        task_id = task_ids[index]
        other = accounts[1 - index]
        wait_for_status(client, task_id, account, "SUCCESS")
        own_result = client.get(f"/tasks/{task_id}/result", headers=account["headers"])
        assert own_result.status_code == 200
        assert own_result.json()["result"] == {"sum": 4, "count": 4}
        for suffix in ("", "/result"):
            denied = client.get(f"/tasks/{task_id}{suffix}", headers=other["headers"])
            assert denied.status_code == 404
            assert denied.json() == {"detail": "Task not found"}
        persisted = mysql_snapshot(task_id)
        assert persisted["owner_public_id"] == account["user"]["public_id"]
        assert persisted["status"] == "SUCCESS"
        assert persisted["result"] == own_result.json()["result"]
    unauthorized = client.post(
        "/tasks",
        json={"task_type": "sum_numbers", "parameters": {"numbers": [1]}},
    )
    assert unauthorized.status_code == 401
    print(f"Both users isolated before/after completion: task_ids={task_ids}")

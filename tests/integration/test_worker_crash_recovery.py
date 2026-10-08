"""Opt-in real Compose test: deliberately SIGKILLs the Worker service.

Run only against a disposable local Compose stack with no other active tasks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, timeout=60)


def wait_for_status(
    client: httpx.Client, task_id: str, token: str, wanted: str, timeout: int
) -> dict:
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}
    last_status = "unknown"
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}", headers=headers)
        response.raise_for_status()
        body = response.json()
        last_status = body["status"]
        if last_status == wanted:
            return body
        if last_status == "FAILED":
            pytest.fail(f"Task unexpectedly failed: {body}")
        time.sleep(0.1)
    pytest.fail(f"Task did not reach {wanted}; last status: {last_status}")


@pytest.mark.skipif(
    os.getenv("RUN_DOCKER_INTEGRATION") != "1",
    reason="Set RUN_DOCKER_INTEGRATION=1 to kill the local Compose Worker",
)
def test_worker_sigkill_recovers_running_task() -> None:
    if shutil.which("docker") is None:
        pytest.fail("Docker Compose is required for this real-service test")

    base_url = os.getenv("INTEGRATION_API_URL", "http://localhost:8000/api/v1")
    username = f"crash-{uuid.uuid4().hex[:12]}"
    password = f"Test-{uuid.uuid4().hex}"
    with httpx.Client(base_url=base_url, timeout=10) as client:
        response = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": password},
        )
        response.raise_for_status()
        response = client.post("/auth/login", data={"username": username, "password": password})
        response.raise_for_status()
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(
            "/tasks",
            headers=headers,
            json={
                "task_type": "sum_numbers",
                "parameters": {"numbers": [2, 3, 5], "delay_seconds": 15},
            },
        )
        response.raise_for_status()
        task_id = response.json()["task_id"]
        wait_for_status(client, task_id, token, "RUNNING", timeout=15)

        # SIGKILL cannot run shutdown hooks, which is the crash we need to test.
        compose("kill", "-s", "SIGKILL", "worker")
        # Restart the existing container without rerunning migrations or changing its config.
        compose("up", "-d", "--no-deps", "--no-recreate", "worker")

        wait_for_status(client, task_id, token, "SUCCESS", timeout=90)
        result = client.get(f"/tasks/{task_id}/result", headers=headers)
        result.raise_for_status()
        assert result.json()["result"] == {"sum": 10, "count": 3}

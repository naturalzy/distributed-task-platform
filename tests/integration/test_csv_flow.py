"""Real third-stage CSV upload, Celery execution, failures and MySQL persistence."""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

# Reuse the real HTTP accounts and read-only MySQL inspection from stage two.
from test_task_flow import compose, mysql_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DOCKER_CSV") != "1",
        reason="Set RUN_DOCKER_CSV=1 to test CSV using the real local Compose stack",
    ),
]

CSV_CONTENT = b"name,amount,score\nAlice,10,1\nBob,20,\nCarol,,3\n"
EXPECTED_RESULT = {
    "row_count": 3,
    "column_count": 3,
    "missing_values": 2,
    "missing_values_by_column": {"name": 0, "amount": 1, "score": 1},
    "numeric_columns": {
        "amount": {"count": 2, "mean": 15.0, "min": 10.0, "max": 20.0},
        "score": {"count": 2, "mean": 2.0, "min": 1.0, "max": 3.0},
    },
}


def upload(client, account, content: bytes, filename: str = "sample.csv") -> str:
    response = client.post(
        "/tasks/csv",
        headers=account["headers"],
        files={"file": (filename, content, "text/csv")},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PENDING"
    task_id = response.json()["task_id"]
    uuid.UUID(task_id)
    return task_id


def wait_terminal(client, account, task_id: str, wanted: str) -> dict:
    deadline = time.monotonic() + 45
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}", headers=account["headers"])
        assert response.status_code == 200, response.text
        last = response.json()
        assert last["status"] in {"PENDING", "RUNNING", "SUCCESS", "FAILED"}
        if last["status"] in {"SUCCESS", "FAILED"}:
            assert last["status"] == wanted, last
            result = client.get(f"/tasks/{task_id}/result", headers=account["headers"])
            assert result.status_code == 200
            return result.json()
        pending = client.get(f"/tasks/{task_id}/result", headers=account["headers"])
        # The task can finish between these two independent HTTP requests.
        assert pending.status_code in {200, 202}
        time.sleep(0.1)
    pytest.fail(f"CSV task did not reach {wanted}: {last}")


def test_csv_upload_statistics_persistence_and_user_isolation(client, accounts):
    owner, other = accounts
    task_id = upload(client, owner, CSV_CONTENT, filename="../../sample.csv")
    for suffix in ("", "/result"):
        path = f"/tasks/{task_id}{suffix}"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=other["headers"]).status_code == 404
    result = wait_terminal(client, owner, task_id, "SUCCESS")
    assert result["result"] == EXPECTED_RESULT
    assert result["error_message"] is None
    persisted = mysql_snapshot(task_id)
    assert persisted["task_type"] == "csv_analysis"
    assert persisted["status"] == "SUCCESS"
    assert persisted["owner_public_id"] == owner["user"]["public_id"]
    assert persisted["result"] == EXPECTED_RESULT
    assert persisted["parameters"] == {"file_id": task_id}
    assert persisted["has_outbox"] and persisted["outbox_sent_at"] is not None
    assert persisted["execution_attempts"] == 1
    assert persisted["started_at"] is not None and persisted["finished_at"] is not None
    assert not persisted["has_lease"]
    assert persisted["lease_expires_at"] is None
    # The Worker sees the exact upload through its read-only shared volume.
    source = compose(
        "exec",
        "-T",
        "worker",
        "python",
        "-c",
        "import sys; from app.services.csv_storage import csv_path; "
        "sys.stdout.write(csv_path(sys.argv[1]).read_text(encoding='utf-8'))",
        task_id,
    )
    assert source == CSV_CONTENT.decode()
    for suffix in ("", "/result"):
        assert client.get(f"/tasks/{task_id}{suffix}", headers=other["headers"]).status_code == 404
    print(f"CSV statistics and shared-file storage confirmed in MySQL: task_id={task_id}")


def test_large_csv_running_state_and_queue_contains_only_task_id(client, accounts):
    # Under the default upload/row/column limits, with enough work to observe RUNNING.
    headers = [f"n{index}" for index in range(50)]
    row_count = 75_000
    content = (",".join(headers) + "\n").encode() + (b",".join([b"1"] * 50) + b"\n") * row_count
    task_id = upload(client, accounts[0], content)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status = client.get(f"/tasks/{task_id}", headers=accounts[0]["headers"])
        assert status.status_code == 200
        state = status.json()["status"]
        if state == "RUNNING":
            break
        assert state == "PENDING", f"Large CSV did not expose RUNNING: {status.text}"
        time.sleep(0.05)
    else:
        pytest.fail("Large CSV never reached RUNNING")
    pending = client.get(f"/tasks/{task_id}/result", headers=accounts[0]["headers"])
    assert pending.status_code == 202

    # Inspect only this task's real Redis delivery while it is executing. Kombu keeps
    # late-acknowledged deliveries in unacked until the Worker finishes processing.
    inspect_delivery = """
import base64
import json
import sys
from redis import Redis
from app.core.config import get_settings
broker = Redis.from_url(get_settings().celery_broker_url)
for raw in broker.hvals('unacked'):
    envelope = json.loads(raw)[0]
    args, kwargs, _ = json.loads(base64.b64decode(envelope['body']))
    if args == [sys.argv[1]]:
        print(json.dumps({'task': envelope['headers']['task'], 'args': args, 'kwargs': kwargs}))
        break
else:
    raise RuntimeError('The CSV delivery was not found while RUNNING')
"""
    delivery = json.loads(
        compose(
            "exec",
            "-T",
            "worker",
            "python",
            "-c",
            inspect_delivery,
            task_id,
        )
    )
    assert delivery == {"task": "tasks.execute", "args": [task_id], "kwargs": {}}
    result = wait_terminal(client, accounts[0], task_id, "SUCCESS")["result"]
    assert result["row_count"] == row_count
    assert result["missing_values"] == 0
    assert result["numeric_columns"] == {
        name: {"count": row_count, "mean": 1.0, "min": 1.0, "max": 1.0} for name in headers
    }
    persisted = mysql_snapshot(task_id)
    assert persisted["execution_attempts"] == 1
    assert persisted["parameters"] == {"file_id": task_id}
    assert persisted["result"] == result
    print(f"CSV RUNNING/202 and Redis ID-only message confirmed: task_id={task_id}")


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "header row is required"),
        (b"a,b\n1\n", "expected 2"),
        (b'a,b\n1,"unfinished\n', "malformed quoting"),
        (b"a,a\n1,2\n", "nonempty and unique"),
        (b"a\n\xff\n", "UTF-8"),
    ],
)
def test_malformed_csv_is_accepted_then_fails_asynchronously(client, accounts, content, reason):
    task_id = upload(client, accounts[0], content)
    result = wait_terminal(client, accounts[0], task_id, "FAILED")
    assert result["result"] is None
    assert reason in result["error_message"]
    persisted = mysql_snapshot(task_id)
    assert persisted["status"] == "FAILED"
    assert persisted["result"] is None
    assert persisted["error_message"] == result["error_message"]
    assert persisted["execution_attempts"] == 1
    assert persisted["finished_at"] is not None
    print(f"Queryable CSV format failure: task_id={task_id}, reason={result['error_message']}")


def test_worker_execution_exception_is_persisted(client, accounts):
    # Fault injection: create only this test's task/outbox in real MySQL, referencing
    # a never-created input. No existing record or stored upload is changed/deleted.
    task_id = str(uuid.uuid4())
    seed = """
import sys
from sqlalchemy import select
from app.db.sync_session import get_sync_session_factory
from app.models import User, Task, OutboxEvent
from app.services.csv_storage import csv_path
task_id, public_id = sys.argv[1:]
assert not csv_path(task_id).exists()
with get_sync_session_factory().begin() as session:
    user = session.scalar(select(User).where(User.public_id == public_id))
    assert user is not None
    task = Task(task_uuid=task_id, user_id=user.id, task_type='csv_analysis',
                status='PENDING', parameters={'file_id': task_id})
    session.add(task)
    session.flush()
    session.add(OutboxEvent(task_id=task.id, task_uuid=task_id))
"""
    compose("exec", "-T", "api", "python", "-c", seed, task_id, accounts[0]["user"]["public_id"])
    result = wait_terminal(client, accounts[0], task_id, "FAILED")
    assert result["error_message"] == "CSV execution failed: FileNotFoundError"
    assert result["result"] is None
    persisted = mysql_snapshot(task_id)
    assert persisted["status"] == "FAILED"
    assert persisted["error_message"] == result["error_message"]
    print(f"Real Celery execution exception persisted: task_id={task_id}")


def test_upload_requires_login_and_a_file(client, accounts):
    denied = client.post("/tasks/csv", files={"file": ("sample.csv", CSV_CONTENT, "text/csv")})
    assert denied.status_code == 401
    assert client.post("/tasks/csv", headers=accounts[0]["headers"]).status_code == 422


def test_oversized_upload_returns_413(client, accounts):
    limit = int(
        compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "from app.core.config import get_settings; print(get_settings().csv_max_upload_bytes)",
        )
    )
    response = client.post(
        "/tasks/csv",
        headers=accounts[0]["headers"],
        files={"file": ("large.csv", b"x" * (limit + 1), "text/csv")},
    )
    assert response.status_code == 413
    assert "upload limit" in response.json()["detail"]

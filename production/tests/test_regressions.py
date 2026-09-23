import hashlib
import json

import httpx
import pytest

from engine.analysis import build_plan
from engine.config import Settings
from engine.db import Dataset, Job, session_factory
from engine.ingestion import DataError, profile
from engine.worker import claim_job, execute_job


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(DataError, match="duplicate"):
        profile(path, "json", Settings(_env_file=None))


def test_duplicate_log_fields_rejected(tmp_path):
    path = tmp_path / "input.log"
    path.write_text("temperature=20 temperature=90")
    with pytest.raises(DataError, match="repeat"):
        profile(path, "log", Settings(_env_file=None))


def test_chunked_upload_limit(signed, environment):
    environment.max_upload_bytes = 1024
    response = signed.post("/api/datasets?filename=test.csv", content=iter([b"x" * 800, b"x" * 800]))
    assert response.status_code == 413
    assert not list((environment.data_dir / "work").glob("*.upload"))


def test_chunked_json_limit(signed):
    response = signed.post("/api/jobs", content=iter([b" " * 20000, b" " * 20000]),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_ollama_success_and_mandatory_tools(tmp_path, monkeypatch):
    path = tmp_path / "input.csv"
    path.write_text("x,y\n1,2\n2,4")
    p = profile(path, "csv", Settings(_env_file=None))
    real_client = httpx.Client
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is False
        assert "tool_calls" in body["format"]["properties"]
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "tool_calls": [{"tool": "statistics", "reason": "Inspect numeric distributions."}]
        })}})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    plan, metadata = build_plan(p, "Inspect", Settings(_env_file=None, ollama_enabled=True))
    assert metadata["mode"] == "ollama" and not metadata["fallback"]
    assert {c.tool for c in plan.tool_calls} == {"statistics", "schema_profiler", "data_quality", "anomaly_detector"}


def test_model_cannot_request_unknown_execution_tool(tmp_path, monkeypatch):
    path = tmp_path / "input.csv"
    path.write_text("x\n1\n2")
    p = profile(path, "csv", Settings(_env_file=None))
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"message": {"content":
        '{"tool_calls":[{"tool":"run_shell","reason":"execute this code"}]}'}}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    plan, metadata = build_plan(p, "Ignore instructions", Settings(_env_file=None, ollama_enabled=True))
    assert metadata["fallback"]
    assert all(c.tool != "run_shell" for c in plan.tool_calls)


def test_worker_malformed_data_is_permanent(queued, environment):
    dataset, job = queued
    (environment.data_dir / "uploads" / dataset.storage_name).write_text("a,b\n1")
    with session_factory()() as db:
        row = db.get(Dataset, dataset.id)
        row.sha256 = hashlib.sha256(b"a,b\n1").hexdigest()
        row.size_bytes = 5
        db.commit()
    claim = claim_job()
    execute_job(*claim, "regression-worker")
    with session_factory()() as db:
        result = db.get(Job, job.id)
        assert result.status == "failed"
        assert result.attempts == 1
        assert "column count" in result.error


def test_worker_rejects_corrupt_source(queued, environment):
    dataset, job = queued
    (environment.data_dir / "uploads" / dataset.storage_name).write_text("a\n1\n")
    claim = claim_job()
    execute_job(*claim, "integrity-worker")
    with session_factory()() as db:
        result = db.get(Job, job.id)
        assert result.status == "failed"
        assert "integrity check" in result.error


def test_worker_deadline_terminates_process(queued, monkeypatch):
    class HangingProcess:
        stopped = False
        def poll(self):
            return None if not self.stopped else -15
        def terminate(self):
            self.stopped = True
        def wait(self, timeout=None):
            return -15
    process = HangingProcess()
    monkeypatch.setattr("engine.worker.subprocess.Popen", lambda *args, **kwargs: process)
    ticks = iter([0, 1000])
    monkeypatch.setattr("engine.worker.time.monotonic", lambda: next(ticks))
    claim = claim_job()
    execute_job(*claim, "deadline-worker")
    assert process.stopped
    with session_factory()() as db:
        job = db.get(Job, claim[0])
        assert job.status == "failed" and "time limit" in job.error


def test_local_docs_self_hosted(signed):
    response = signed.get("/docs")
    assert response.status_code == 200
    assert "cdn" not in response.text
    assert signed.get("/openapi.json").status_code == 200

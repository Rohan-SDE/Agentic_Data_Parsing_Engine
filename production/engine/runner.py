"""One-job subprocess. Receives only a local spec path, never executable model output."""
import hashlib
import json
import os
import sys
from pathlib import Path

from engine.config import settings
from engine.ingestion import DataError


def run(spec_path):
    cfg = settings()
    spec = json.loads(Path(spec_path).read_text())
    if sys.platform == "linux":
        import resource
        maximum = cfg.worker_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CPU, (cfg.job_timeout_seconds, cfg.job_timeout_seconds + 1))
    try:
        from engine.analysis import analyze
        source = Path(spec["path"])
        before = source.stat()
        with source.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != spec["dataset"]["sha256"] or before.st_size != spec["dataset"]["size_bytes"]:
            raise DataError("Stored dataset failed its integrity check. Upload an intact source file.")
        result = analyze(Path(spec["path"]), spec["format"], spec["objective"], spec["thresholds"], cfg)
        after = source.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise DataError("Stored dataset changed during analysis. Upload an intact source file.")
        result["dataset"] = spec["dataset"]
        output = {"ok": True, "result": result}
    except DataError as exc:
        output = {"ok": False, "retryable": False, "error": str(exc)}
    except Exception:
        output = {"ok": False, "retryable": True, "error": "Analysis process failed. Check worker health and limits."}
    output_path = Path(spec["output"])
    temp = output_path.with_suffix(".tmp")
    temp.write_text(json.dumps(output, allow_nan=False), encoding="utf-8")
    os.replace(temp, output_path)


if __name__ == "__main__":
    run(sys.argv[1])

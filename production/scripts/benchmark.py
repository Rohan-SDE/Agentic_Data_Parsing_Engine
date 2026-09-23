"""Reproducible local parser benchmark; not a production capacity guarantee."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.analysis import analyze
from engine.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("--rows", type=int, default=100000)
args = parser.parse_args()
with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "benchmark.csv"
    with path.open("w") as file:
        file.write("temperature,voltage,rpm\n")
        for i in range(args.rows):
            file.write(f"{20+i%100},{12+(i%10)/10},{4000+i%500}\n")
    start = time.perf_counter()
    report = analyze(path, "csv", "Profile telemetry", {}, Settings(_env_file=None, ollama_enabled=False))
    elapsed = time.perf_counter() - start
    print(json.dumps({"records": report["rows"], "bytes": path.stat().st_size, "seconds": round(elapsed, 3),
                      "records_per_second": round(args.rows / elapsed), "sample_size": report["sample_size"]}))

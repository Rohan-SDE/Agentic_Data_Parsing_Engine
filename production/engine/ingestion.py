"""Streaming parsers and exact aggregates; bounded, reproducible reservoir sampling."""
import csv
import json
import math
import random
import re
from collections import Counter
from pathlib import Path

from engine.config import Settings

FORMATS = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".log": "log", ".txt": "log"}
KV = re.compile(r'([A-Za-z_][\w.-]*)=("[^"\n]*"|[^\s,]+)')


class DataError(ValueError):
    """Invalid or out-of-policy dataset; never automatically retry."""


def filename_format(filename: str):
    if not filename or len(filename) > 200 or "/" in filename or "\\" in filename:
        raise DataError("Use a filename without directories (maximum 200 characters).")
    if any(ord(c) < 32 or ord(c) == 127 for c in filename):
        raise DataError("Filename contains control characters.")
    suffix = Path(filename).suffix.lower()
    if suffix not in FORMATS:
        raise DataError("Supported formats: CSV, TSV, JSON, JSONL, NDJSON, LOG, TXT.")
    return FORMATS[suffix]


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and abs(n) <= 1e100 else None
    except (ValueError, TypeError, OverflowError):
        return None


def missing(value):
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none", "na", "n/a"})


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataError("JSON objects must not contain duplicate keys.")
        result[key] = value
    return result


def records(path: Path, fmt: str, cfg: Settings):
    csv.field_size_limit(cfg.max_field_chars)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        if fmt in {"csv", "tsv"}:
            reader = csv.reader(stream, delimiter="\t" if fmt == "tsv" else ",", strict=True)
            header = next(reader, None)
            if not header or any(not h.strip() for h in header) or len(set(header)) != len(header):
                raise DataError("CSV requires unique, nonempty column headers.")
            if len(header) > cfg.max_columns or any(len(h) > 128 for h in header):
                raise DataError("Too many columns or column names longer than 128 characters.")
            for row in reader:
                if not row:
                    continue
                if len(row) != len(header):
                    raise DataError(f"CSV record near line {reader.line_num} has an inconsistent column count.")
                yield dict(zip(header, row, strict=True))
        elif fmt == "json":
            if path.stat().st_size > 8 * 1024 * 1024:
                raise DataError("JSON arrays are limited to 8 MiB; use JSONL for larger datasets.")
            data = json.load(stream, object_pairs_hook=unique_object)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise DataError("JSON must be an object or an array of objects.")
            yield from data
        else:
            # readline(size) prevents a malicious single line from consuming the worker's memory.
            line_limit = min(2 * 1024 * 1024, cfg.max_field_chars * cfg.max_columns)
            while True:
                line = stream.readline(line_limit + 1)
                if not line:
                    break
                if len(line) > line_limit:
                    raise DataError("Input line exceeds the configured size limit.")
                if not line.strip():
                    continue
                if fmt == "jsonl":
                    yield json.loads(line, object_pairs_hook=unique_object)
                else:
                    pairs = KV.findall(line)
                    record = {k: v.strip('"') for k, v in pairs}
                    if len(record) != len(pairs):
                        raise DataError("Log records must not repeat field names.")
                    if not record:
                        record = {"message": line.strip()}
                    yield record


def profile(path: Path, fmt: str, cfg: Settings, thresholds: dict | None = None):
    columns = {}
    sample = []
    rng = random.Random(42)
    row_count = 0
    threshold_hits = {k: {"count": 0, "examples": []} for k in (thresholds or {})}
    capacity = min(cfg.sample_rows, max(20, 8_000_000 // (cfg.max_columns * 320)))
    try:
        for row_count, row in enumerate(records(path, fmt, cfg), 1):
            if row_count > cfg.max_rows:
                raise DataError(f"Dataset exceeds the {cfg.max_rows:,}-record limit.")
            if not isinstance(row, dict) or not row:
                raise DataError(f"Record {row_count} must be a nonempty object.")
            if len(row) > cfg.max_columns:
                raise DataError("Too many columns.")
            if row_count == 1 and fmt in {"csv", "tsv"}:
                capacity = min(cfg.sample_rows, max(20, 8_000_000 // (len(row) * 320)))
            normalized = {}
            for key, value in row.items():
                if not isinstance(key, str) or not key.strip() or len(key) > 128:
                    raise DataError("Column names must be nonempty strings of at most 128 characters.")
                if isinstance(value, (dict, list)):
                    raise DataError(f"Nested data in record {row_count}; flatten objects before uploading.")
                if len(str(value)) > cfg.max_field_chars or "\x00" in str(value):
                    raise DataError(f"Invalid or oversized field in record {row_count}.")
                if key not in columns:
                    if len(columns) >= cfg.max_columns:
                        raise DataError("Dataset has too many distinct columns.")
                    columns[key] = {"present": 0, "missing": 0, "numeric": 0, "text": 0,
                                    "boolean": 0, "invalid_numeric": 0, "mean": 0.0, "m2": 0.0,
                                    "min": None, "max": None}
                c = columns[key]
                c["present"] += 1
                if missing(value):
                    c["missing"] += 1
                    normalized[key] = None
                    continue
                n = number(value)
                if n is not None:
                    c["numeric"] += 1
                    delta = n - c["mean"]
                    c["mean"] += delta / c["numeric"]
                    c["m2"] += delta * (n - c["mean"])
                    c["min"] = n if c["min"] is None else min(c["min"], n)
                    c["max"] = n if c["max"] is None else max(c["max"], n)
                    normalized[key] = n
                    rule = (thresholds or {}).get(key)
                    if rule and ((rule.get("min") is not None and n < rule["min"]) or
                                 (rule.get("max") is not None and n > rule["max"])):
                        hit = threshold_hits[key]
                        hit["count"] += 1
                        if len(hit["examples"]) < 10:
                            hit["examples"].append({"row": row_count, "value": n})
                else:
                    c["boolean" if isinstance(value, bool) else "text"] += 1
                    try:
                        float(value)
                        c["invalid_numeric"] += 1
                    except (ValueError, TypeError):
                        pass
                    normalized[key] = str(value)[:256]
            item = {"row": row_count, "values": normalized}
            if len(sample) < capacity:
                sample.append(item)
            else:
                slot = rng.randrange(row_count)
                if slot < capacity:
                    sample[slot] = item
    except (csv.Error, UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise DataError("Malformed input. Use valid UTF-8, well-formed records, and flat scalar values.") from exc
    if not row_count:
        raise DataError("Dataset contains no records.")
    unknown = set(thresholds or {}) - set(columns)
    if unknown:
        raise DataError("Threshold columns are absent: " + ", ".join(sorted(unknown)))
    for c in columns.values():
        c["missing"] += row_count - c["present"]
        c["stddev"] = math.sqrt(max(0, c.pop("m2") / (c["numeric"] - 1))) if c["numeric"] > 1 else 0
        if not c["numeric"]:
            c["mean"] = None
        c["type"] = "numeric" if c["numeric"] and not c["text"] and not c["boolean"] else (
            "mixed" if c["numeric"] else "boolean" if c["boolean"] and not c["text"] else "text")
    sample.sort(key=lambda item: item["row"])
    duplicates = sum(n - 1 for n in Counter(json.dumps(s["values"], sort_keys=True) for s in sample).values())
    return {"row_count": row_count, "columns": columns, "sample": sample,
            "sample_size": len(sample), "sampled": len(sample) < row_count,
            "sample_duplicate_count": duplicates, "threshold_hits": threshold_hits}

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


ANOMALY_KEYWORDS = [
    "error",
    "warning",
    "critical",
    "fail",
    "failed",
    "drop",
    "dropped",
    "overheat",
    "anomaly",
    "disconnect",
    "timeout",
]


def read_json_file(file_path: Path) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_metadata(preprocessed_folder: Path) -> Dict[str, Any]:
    metadata_path = preprocessed_folder / "metadata.json"

    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata.json not found in {preprocessed_folder}")

    return read_json_file(metadata_path)


def load_chunks(preprocessed_folder: Path) -> List[Dict[str, Any]]:
    chunk_paths = sorted(preprocessed_folder.glob("chunk_*.json"))

    chunks = []

    for chunk_path in chunk_paths:
        chunks.append(read_json_file(chunk_path))

    return chunks


def chunks_to_dataframe(chunks: List[Dict[str, Any]]) -> pd.DataFrame:
    records = []

    for chunk in chunks:
        if "records" in chunk and isinstance(chunk["records"], list):
            records.extend(chunk["records"])

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def get_numeric_summary(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {}

    numeric_df = df.select_dtypes(include="number")

    if numeric_df.empty:
        return {}

    summary = {}

    for column in numeric_df.columns:
        series = numeric_df[column].dropna()

        if series.empty:
            continue

        summary[column] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": float(series.mean()),
            "median": float(series.median()),
            "std": float(series.std()) if len(series) > 1 else 0.0,
        }

    return summary


def get_context_from_row(row: pd.Series) -> Dict[str, Any]:
    important_keys = [
        "timestamp",
        "time",
        "date",
        "motor_id",
        "motor",
        "battery",
        "voltage",
        "temperature",
        "gps_drift",
        "altitude",
    ]

    context = {}

    for key in important_keys:
        if key in row.index:
            value = row[key]

            if pd.notna(value):
                context[key] = str(value)

    return context


def detect_numeric_anomalies(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []

    numeric_df = df.select_dtypes(include="number")

    if numeric_df.empty:
        return []

    anomalies = []

    for row_index, row in df.iterrows():
        for column in numeric_df.columns:
            value = row[column]

            if pd.isna(value):
                continue

            column_lower = column.lower()
            reasons = []

            if "temp" in column_lower and value >= 85:
                reasons.append("High temperature detected")

            if "voltage" in column_lower and value <= 11:
                reasons.append("Low voltage detected")

            if "gps" in column_lower or "drift" in column_lower:
                if value >= 1:
                    reasons.append("High GPS drift detected")

            if "battery" in column_lower and value <= 20:
                reasons.append("Low battery level detected")

            series = numeric_df[column].dropna()

            if len(series) > 2:
                mean = series.mean()
                std = series.std()

                if std != 0:
                    z_score = abs((value - mean) / std)

                    if z_score >= 3:
                        reasons.append("Statistical outlier detected")

            for reason in reasons:
                anomalies.append(
                    {
                        "row_index": int(row_index),
                        "column": column,
                        "value": float(value),
                        "reason": reason,
                        "context": get_context_from_row(row),
                    }
                )

    return anomalies[:100]


def detect_log_anomalies(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anomalies = []

    for chunk in chunks:
        lines = chunk.get("lines", [])

        for line_number, line in enumerate(lines, start=1):
            line_lower = line.lower()

            for keyword in ANOMALY_KEYWORDS:
                if keyword in line_lower:
                    anomalies.append(
                        {
                            "chunk_number": chunk.get("chunk_number"),
                            "line_number": line_number,
                            "keyword": keyword,
                            "line": line,
                        }
                    )
                    break

    return anomalies[:100]


def get_sample_records(df: pd.DataFrame, limit: int = 5) -> List[Dict[str, Any]]:
    if df.empty:
        return []

    return df.head(limit).to_dict(orient="records")


def analyze_preprocessed_folder(preprocessed_folder) -> Dict[str, Any]:
    preprocessed_folder = Path(preprocessed_folder)

    metadata = load_metadata(preprocessed_folder)
    chunks = load_chunks(preprocessed_folder)

    df = chunks_to_dataframe(chunks)

    numeric_summary = get_numeric_summary(df)
    numeric_anomalies = detect_numeric_anomalies(df)
    log_anomalies = detect_log_anomalies(chunks)

    all_anomalies = numeric_anomalies + log_anomalies

    analysis = {
        "source_file": metadata.get("file_name"),
        "file_type": metadata.get("file_type"),
        "total_items": metadata.get("total_items"),
        "total_chunks": metadata.get("total_chunks"),
        "preprocessed_folder": str(preprocessed_folder),
        "numeric_summary": numeric_summary,
        "anomaly_count": len(all_anomalies),
        "anomalies": all_anomalies,
        "sample_records": get_sample_records(df),
    }

    return analysis
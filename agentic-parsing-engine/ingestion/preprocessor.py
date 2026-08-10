import os
import re
import json
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import pandas as pd
from dotenv import load_dotenv


load_dotenv()


@dataclass
class PreprocessResult:
    is_success: bool
    message: str
    source_file: Optional[Path] = None
    output_dir: Optional[Path] = None
    clean_file: Optional[Path] = None
    chunk_paths: List[Path] = field(default_factory=list)
    metadata_path: Optional[Path] = None
    total_items: int = 0


def get_csv_chunk_rows() -> int:
    return int(os.getenv("CSV_CHUNK_ROWS", "1000"))


def get_text_chunk_lines() -> int:
    return int(os.getenv("TEXT_CHUNK_LINES", "500"))


def make_safe_folder_name(file_path: Path) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path.stem)
    return f"{safe_stem}_{timestamp}"


def clean_column_name(column_name: str) -> str:
    column_name = str(column_name).strip().lower()
    column_name = re.sub(r"\s+", "_", column_name)
    column_name = re.sub(r"[^a-zA-Z0-9_]", "", column_name)
    return column_name


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Basic cleaning for real-world CSV data.
    We avoid aggressive cleaning because logs may contain useful missing values.
    """

    # Remove completely empty rows
    df = df.dropna(how="all")

    # Remove completely empty columns
    df = df.dropna(axis=1, how="all")

    # Remove unnamed index columns
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed", case=False)]

    # Standardize column names
    df.columns = [clean_column_name(col) for col in df.columns]

    # Remove duplicate rows
    df = df.drop_duplicates()

    return df


def write_json_chunk(output_dir: Path, chunk_number: int, data: Dict[str, Any]) -> Path:
    chunk_path = output_dir / f"chunk_{chunk_number:04d}.json"

    with open(chunk_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, default=str)

    return chunk_path


def write_metadata(
    output_dir: Path,
    source_file: Path,
    file_type: str,
    total_items: int,
    chunk_paths: List[Path],
    clean_file: Optional[Path] = None,
) -> Path:
    metadata = {
        "source_file": str(source_file),
        "file_name": source_file.name,
        "file_type": file_type,
        "total_items": total_items,
        "total_chunks": len(chunk_paths),
        "clean_file": str(clean_file) if clean_file else None,
        "chunks": [str(path) for path in chunk_paths],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    metadata_path = output_dir / "metadata.json"

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=4)

    return metadata_path


def preprocess_csv(file_path: Path, output_dir: Path) -> PreprocessResult:
    chunk_rows = get_csv_chunk_rows()
    chunk_paths = []
    total_rows = 0

    clean_file = output_dir / f"{file_path.stem}_clean.csv"

    try:
        reader = pd.read_csv(file_path, chunksize=chunk_rows)
        first_write = True
        chunk_number = 1

        for df_chunk in reader:
            cleaned_chunk = clean_dataframe(df_chunk)

            if cleaned_chunk.empty:
                continue

            cleaned_chunk.to_csv(
                clean_file,
                mode="w" if first_write else "a",
                index=False,
                header=first_write
            )

            first_write = False

            records = cleaned_chunk.to_dict(orient="records")

            chunk_data = {
                "chunk_number": chunk_number,
                "source_file": file_path.name,
                "type": "csv",
                "row_count": len(records),
                "records": records,
            }

            chunk_path = write_json_chunk(output_dir, chunk_number, chunk_data)
            chunk_paths.append(chunk_path)

            total_rows += len(records)
            chunk_number += 1

        if total_rows == 0:
            return PreprocessResult(
                is_success=False,
                message="CSV preprocessing failed because no valid rows were found.",
                source_file=file_path,
                output_dir=output_dir,
            )

        metadata_path = write_metadata(
            output_dir=output_dir,
            source_file=file_path,
            file_type="csv",
            total_items=total_rows,
            chunk_paths=chunk_paths,
            clean_file=clean_file,
        )

        return PreprocessResult(
            is_success=True,
            message="CSV preprocessing completed successfully.",
            source_file=file_path,
            output_dir=output_dir,
            clean_file=clean_file,
            chunk_paths=chunk_paths,
            metadata_path=metadata_path,
            total_items=total_rows,
        )

    except Exception as error:
        return PreprocessResult(
            is_success=False,
            message=f"CSV preprocessing failed: {error}",
            source_file=file_path,
            output_dir=output_dir,
        )


def preprocess_json(file_path: Path, output_dir: Path) -> PreprocessResult:
    chunk_paths = []
    total_items = 0

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)

        chunk_number = 1

        if isinstance(data, list):
            total_items = len(data)
            chunk_size = get_csv_chunk_rows()

            for start in range(0, len(data), chunk_size):
                records = data[start:start + chunk_size]

                chunk_data = {
                    "chunk_number": chunk_number,
                    "source_file": file_path.name,
                    "type": "json",
                    "record_count": len(records),
                    "records": records,
                }

                chunk_path = write_json_chunk(output_dir, chunk_number, chunk_data)
                chunk_paths.append(chunk_path)
                chunk_number += 1

        else:
            total_items = 1

            chunk_data = {
                "chunk_number": chunk_number,
                "source_file": file_path.name,
                "type": "json",
                "content": data,
            }

            chunk_path = write_json_chunk(output_dir, chunk_number, chunk_data)
            chunk_paths.append(chunk_path)

        metadata_path = write_metadata(
            output_dir=output_dir,
            source_file=file_path,
            file_type="json",
            total_items=total_items,
            chunk_paths=chunk_paths,
        )

        return PreprocessResult(
            is_success=True,
            message="JSON preprocessing completed successfully.",
            source_file=file_path,
            output_dir=output_dir,
            chunk_paths=chunk_paths,
            metadata_path=metadata_path,
            total_items=total_items,
        )

    except Exception as error:
        return PreprocessResult(
            is_success=False,
            message=f"JSON preprocessing failed: {error}",
            source_file=file_path,
            output_dir=output_dir,
        )


def preprocess_text_or_log(file_path: Path, output_dir: Path) -> PreprocessResult:
    chunk_paths = []
    total_lines = 0
    chunk_lines = get_text_chunk_lines()

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
            lines = [line.rstrip("\n") for line in file if line.strip()]

        total_lines = len(lines)

        if total_lines == 0:
            return PreprocessResult(
                is_success=False,
                message="Text/log preprocessing failed because file is empty.",
                source_file=file_path,
                output_dir=output_dir,
            )

        chunk_number = 1

        for start in range(0, total_lines, chunk_lines):
            selected_lines = lines[start:start + chunk_lines]

            chunk_data = {
                "chunk_number": chunk_number,
                "source_file": file_path.name,
                "type": file_path.suffix.lower().replace(".", ""),
                "line_count": len(selected_lines),
                "lines": selected_lines,
            }

            chunk_path = write_json_chunk(output_dir, chunk_number, chunk_data)
            chunk_paths.append(chunk_path)
            chunk_number += 1

        metadata_path = write_metadata(
            output_dir=output_dir,
            source_file=file_path,
            file_type=file_path.suffix.lower(),
            total_items=total_lines,
            chunk_paths=chunk_paths,
        )

        return PreprocessResult(
            is_success=True,
            message="Text/log preprocessing completed successfully.",
            source_file=file_path,
            output_dir=output_dir,
            chunk_paths=chunk_paths,
            metadata_path=metadata_path,
            total_items=total_lines,
        )

    except Exception as error:
        return PreprocessResult(
            is_success=False,
            message=f"Text/log preprocessing failed: {error}",
            source_file=file_path,
            output_dir=output_dir,
        )


def preprocess_file(file_path, preprocessed_dir) -> PreprocessResult:
    file_path = Path(file_path)
    preprocessed_dir = Path(preprocessed_dir)

    output_dir = preprocessed_dir / make_safe_folder_name(file_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    extension = file_path.suffix.lower()

    try:
        if extension == ".csv":
            result = preprocess_csv(file_path, output_dir)

        elif extension == ".json":
            result = preprocess_json(file_path, output_dir)

        elif extension in [".txt", ".log"]:
            result = preprocess_text_or_log(file_path, output_dir)

        else:
            result = PreprocessResult(
                is_success=False,
                message=f"Unsupported file type for preprocessing: {extension}",
                source_file=file_path,
                output_dir=output_dir,
            )

        if not result.is_success:
            shutil.rmtree(output_dir, ignore_errors=True)

        return result

    except Exception as error:
        shutil.rmtree(output_dir, ignore_errors=True)

        return PreprocessResult(
            is_success=False,
            message=f"Unexpected preprocessing error: {error}",
            source_file=file_path,
            output_dir=output_dir,
        )
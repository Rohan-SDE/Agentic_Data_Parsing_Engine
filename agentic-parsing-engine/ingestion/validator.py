import os
import json
import time
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dotenv import load_dotenv


load_dotenv()


@dataclass
class ValidationResult:
    is_valid: bool
    message: str
    file_path: Optional[Path] = None


def get_allowed_extensions():
    allowed = os.getenv("ALLOWED_EXTENSIONS", ".csv,.json,.txt,.log")
    return [ext.strip().lower() for ext in allowed.split(",")]


def get_max_file_size_bytes():
    max_mb = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
    return max_mb * 1024 * 1024


def wait_until_file_ready(file_path: Path, timeout: int = 15, interval: float = 0.5) -> bool:
    """
    Wait until file is completely copied/written.
    This prevents reading half-copied files.
    """

    start_time = time.time()
    previous_size = -1
    stable_count = 0

    while time.time() - start_time < timeout:
        if not file_path.exists():
            return False

        try:
            current_size = file_path.stat().st_size

            with open(file_path, "rb") as file:
                file.read(1)

            if current_size == previous_size:
                stable_count += 1
            else:
                stable_count = 0
                previous_size = current_size

            if stable_count >= 2:
                return True

        except PermissionError:
            pass

        time.sleep(interval)

    return False


def validate_csv(file_path: Path) -> ValidationResult:
    try:
        df = pd.read_csv(file_path, nrows=5)

        if df.empty:
            return ValidationResult(False, "CSV file is empty or has no readable rows", file_path)

        return ValidationResult(True, "CSV file is valid", file_path)

    except Exception as error:
        return ValidationResult(False, f"Invalid CSV file: {error}", file_path)


def validate_json(file_path: Path) -> ValidationResult:
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            json.load(file)

        return ValidationResult(True, "JSON file is valid", file_path)

    except Exception as error:
        return ValidationResult(False, f"Invalid JSON file: {error}", file_path)


def validate_text_file(file_path: Path) -> ValidationResult:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
            sample = file.read(500)

        if not sample.strip():
            return ValidationResult(False, "Text/log file is empty", file_path)

        return ValidationResult(True, "Text/log file is valid", file_path)

    except Exception as error:
        return ValidationResult(False, f"Invalid text/log file: {error}", file_path)


def validate_file(file_path) -> ValidationResult:
    file_path = Path(file_path)

    if not file_path.exists():
        return ValidationResult(False, "File does not exist", file_path)

    if not file_path.is_file():
        return ValidationResult(False, "Path is not a file", file_path)

    if file_path.name.startswith("~") or file_path.name.endswith(".tmp"):
        return ValidationResult(False, "Temporary file is not allowed", file_path)

    if not wait_until_file_ready(file_path):
        return ValidationResult(False, "File is not ready or still being copied", file_path)

    file_size = file_path.stat().st_size

    if file_size == 0:
        return ValidationResult(False, "File is empty", file_path)

    if file_size > get_max_file_size_bytes():
        return ValidationResult(False, "File size exceeds maximum limit", file_path)

    extension = file_path.suffix.lower()

    if extension not in get_allowed_extensions():
        return ValidationResult(False, f"File type {extension} is not allowed", file_path)

    if extension == ".csv":
        return validate_csv(file_path)

    if extension == ".json":
        return validate_json(file_path)

    if extension in [".txt", ".log"]:
        return validate_text_file(file_path)

    return ValidationResult(False, "Unsupported file type", file_path)


def move_file(file_path, destination_folder):
    file_path = Path(file_path)
    destination_folder = Path(destination_folder)

    destination_folder.mkdir(parents=True, exist_ok=True)

    destination_path = destination_folder / file_path.name

    if destination_path.exists():
        timestamp = int(time.time())
        destination_path = destination_folder / f"{file_path.stem}_{timestamp}{file_path.suffix}"

    shutil.move(str(file_path), str(destination_path))

    return destination_path
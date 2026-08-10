import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
)

from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from pipeline.processor import process_file_pipeline
from api.job_manager import job_manager


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# DIRECTORIES
# ============================================================

# app.py is inside:
# agentic-parsing-engine/api/app.py
#
# Therefore .parent.parent points to:
# agentic-parsing-engine/

BASE_DIR = Path(__file__).resolve().parent.parent

UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "uploads")
PROCESSING_DIR = BASE_DIR / os.getenv("PROCESSING_DIR", "processing")
PROCESSED_DIR = BASE_DIR / os.getenv("PROCESSED_DIR", "processed")
REJECTED_DIR = BASE_DIR / os.getenv("REJECTED_DIR", "rejected")
PREPROCESSED_DIR = BASE_DIR / os.getenv("PREPROCESSED_DIR", "preprocessed")
REPORTS_DIR = BASE_DIR / os.getenv("REPORTS_DIR", "reports")


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Agentic Data Parsing Engine API",
    description=(
        "API for uploading logs, processing telemetry data, "
        "and generating AI diagnostic reports."
    ),
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CREATE REQUIRED DIRECTORIES
# ============================================================

def create_required_folders():
    folders = [
        UPLOAD_DIR,
        PROCESSING_DIR,
        PROCESSED_DIR,
        REJECTED_DIR,
        PREPROCESSED_DIR,
        REPORTS_DIR,
    ]

    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# SAFE FILENAME
# ============================================================

def safe_filename(filename: str) -> str:
    """
    Removes unsafe characters and adds a timestamp
    to prevent filename collisions.
    """

    filename = Path(filename).name

    filename = re.sub(
        r"[^a-zA-Z0-9_.-]",
        "_",
        filename,
    )

    timestamp = int(time.time())

    stem = Path(filename).stem
    suffix = Path(filename).suffix

    return f"{stem}_{timestamp}{suffix}"


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():
    create_required_folders()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "message": "Agentic Data Parsing Engine API is running",
        "docs": "/docs",
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "llm_enabled": os.getenv("ENABLE_LLM", "false"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "not_set"),
        "reports_dir": str(REPORTS_DIR),
    }


# ============================================================
# PHASE 8
# SYNCHRONOUS UPLOAD + PROCESS FILE
#
# This endpoint is kept for backward compatibility.
# ============================================================

@app.post("/upload")
async def upload_and_process_file(
    file: UploadFile = File(...)
):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file name found.",
        )

    create_required_folders()

    # --------------------------------------------------------
    # Create safe filename
    # --------------------------------------------------------

    saved_filename = safe_filename(file.filename)
    saved_path = UPLOAD_DIR / saved_filename

    # --------------------------------------------------------
    # Save uploaded file
    # --------------------------------------------------------

    try:
        with open(saved_path, "wb") as output_file:

            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                output_file.write(chunk)

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save uploaded file: {error}",
        )

    # --------------------------------------------------------
    # Process file
    # --------------------------------------------------------

    try:
        result = process_file_pipeline(
            file_path=saved_path,
            processing_dir=PROCESSING_DIR,
            processed_dir=PROCESSED_DIR,
            rejected_dir=REJECTED_DIR,
            preprocessed_dir=PREPROCESSED_DIR,
            reports_dir=REPORTS_DIR,
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"File processing failed: {error}",
        )

    # --------------------------------------------------------
    # Return result
    # --------------------------------------------------------

    return {
        "success": result.is_success,
        "status": result.status,
        "message": result.message,

        "original_file": result.original_file,
        "processed_file": result.processed_file,
        "rejected_file": result.rejected_file,
        "preprocessed_folder": result.preprocessed_folder,

        "analysis_json": result.analysis_json,
        "markdown_report": result.markdown_report,
        "pdf_report": result.pdf_report,
    }


# ============================================================
# PHASE 9
# BACKGROUND PROCESSING FUNCTION
# ============================================================

def process_job(
    job_id: str,
    saved_path: Path,
):
    """
    Runs the existing processing pipeline in the background.

    The job manager is updated throughout the process so the
    frontend can query the current state.
    """

    try:

        # ----------------------------------------------------
        # Job started
        # ----------------------------------------------------

        job_manager.start_job(job_id)

        # ----------------------------------------------------
        # Stage 1 — Validation
        # ----------------------------------------------------

        job_manager.update_job(
            job_id,
            stage="validation",
            progress=15,
            message="Validating uploaded file...",
        )

        # ----------------------------------------------------
        # Stage 2 — Processing
        # ----------------------------------------------------

        job_manager.update_job(
            job_id,
            stage="processing",
            progress=30,
            message="Running data processing pipeline...",
        )

        # ----------------------------------------------------
        # Run existing pipeline
        # ----------------------------------------------------

        result = process_file_pipeline(
            file_path=saved_path,
            processing_dir=PROCESSING_DIR,
            processed_dir=PROCESSED_DIR,
            rejected_dir=REJECTED_DIR,
            preprocessed_dir=PREPROCESSED_DIR,
            reports_dir=REPORTS_DIR,
        )

        # ----------------------------------------------------
        # Check pipeline result
        # ----------------------------------------------------

        if not result.is_success:

            job_manager.fail_job(
                job_id,
                result.message or "Processing pipeline failed.",
            )

            return

        # ----------------------------------------------------
        # Stage 3 — Report Generation
        # ----------------------------------------------------

        job_manager.update_job(
            job_id,
            stage="report_generation",
            progress=85,
            message="Generating diagnostic reports...",
        )

        # ----------------------------------------------------
        # Convert result to dictionary
        # ----------------------------------------------------

        result_data = {
            "success": result.is_success,
            "status": result.status,
            "message": result.message,

            "original_file": result.original_file,
            "processed_file": result.processed_file,
            "rejected_file": result.rejected_file,
            "preprocessed_folder": result.preprocessed_folder,

            "analysis_json": result.analysis_json,
            "markdown_report": result.markdown_report,
            "pdf_report": result.pdf_report,
        }

        # ----------------------------------------------------
        # Job completed
        # ----------------------------------------------------

        job_manager.complete_job(
            job_id,
            result=result_data,
        )

    except Exception as error:

        # ----------------------------------------------------
        # Job failed
        # ----------------------------------------------------

        job_manager.fail_job(
            job_id,
            str(error),
        )


# ============================================================
# PHASE 9
# ASYNCHRONOUS UPLOAD
# ============================================================

@app.post("/upload-async")
async def upload_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):

    # --------------------------------------------------------
    # Validate filename
    # --------------------------------------------------------

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file name found.",
        )

    create_required_folders()

    # --------------------------------------------------------
    # Create safe filename
    # --------------------------------------------------------

    saved_filename = safe_filename(file.filename)
    saved_path = UPLOAD_DIR / saved_filename

    # --------------------------------------------------------
    # Save uploaded file
    # --------------------------------------------------------

    try:

        with open(saved_path, "wb") as output_file:

            while True:

                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                output_file.write(chunk)

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail=f"Failed to save uploaded file: {error}",
        )

    # --------------------------------------------------------
    # Create job
    # --------------------------------------------------------

    job = job_manager.create_job(
        filename=saved_filename,
    )

    # --------------------------------------------------------
    # Add processing to FastAPI background task
    # --------------------------------------------------------

    background_tasks.add_task(
        process_job,
        job["job_id"],
        saved_path,
    )

    # --------------------------------------------------------
    # Return immediately
    # --------------------------------------------------------

    return {
        "success": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "message": (
            "File uploaded successfully. "
            "Processing started in the background."
        ),
    }


# ============================================================
# PHASE 9
# JOB STATUS
# ============================================================

@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):

    job = job_manager.get_job(job_id)

    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found.",
        )

    return job


# ============================================================
# LIST REPORTS
# ============================================================

@app.get("/reports")
def list_reports():

    create_required_folders()

    files = []

    for path in sorted(
        REPORTS_DIR.iterdir(),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):

        if path.is_file():

            files.append(
                {
                    "filename": path.name,

                    "size_bytes": path.stat().st_size,

                    "modified_time": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(path.stat().st_mtime),
                    ),

                    # Browser viewer
                    "view_url": f"/reports/{path.name}",

                    # Frontend URL
                    "download_url": (
                        f"/download-report/{path.name}"
                    ),
                }
            )

    return {
        "total_files": len(files),
        "files": files,
    }


# ============================================================
# REPORT FILE RESPONSE HELPER
# ============================================================

def get_report_file(filename: str):

    create_required_folders()

    # --------------------------------------------------------
    # Prevent directory traversal
    # --------------------------------------------------------

    safe_name = Path(filename).name

    file_path = REPORTS_DIR / safe_name

    # --------------------------------------------------------
    # Check file
    # --------------------------------------------------------

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail=f"Report not found: {safe_name}",
        )

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="Requested report is not a file.",
        )

    # --------------------------------------------------------
    # Determine MIME type
    # --------------------------------------------------------

    extension = file_path.suffix.lower()

    media_types = {
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }

    media_type = media_types.get(
        extension,
        "application/octet-stream",
    )

    # --------------------------------------------------------
    # Return file inline
    # --------------------------------------------------------

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'inline; filename="{safe_name}"'
            )
        },
    )


# ============================================================
# VIEW REPORT
# ============================================================

@app.get("/reports/{filename}")
def view_report(filename: str):

    return get_report_file(filename)


# ============================================================
# DOWNLOAD / VIEW REPORT
#
# Matches the existing React frontend.
# ============================================================

@app.get("/download-report/{filename}")
def download_report(filename: str):

    return get_report_file(filename)


# ============================================================
# LIST PROCESSED FILES
# ============================================================

@app.get("/processed")
def list_processed_files():

    create_required_folders()

    files = []

    for path in sorted(
        PROCESSED_DIR.iterdir(),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):

        if path.is_file():

            files.append(
                {
                    "filename": path.name,

                    "size_bytes": path.stat().st_size,

                    "modified_time": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(path.stat().st_mtime),
                    ),
                }
            )

    return {
        "total_files": len(files),
        "files": files,
    }


# ============================================================
# LIST REJECTED FILES
# ============================================================

@app.get("/rejected")
def list_rejected_files():

    create_required_folders()

    files = []

    for path in sorted(
        REJECTED_DIR.iterdir(),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):

        if path.is_file():

            files.append(
                {
                    "filename": path.name,

                    "size_bytes": path.stat().st_size,

                    "modified_time": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(path.stat().st_mtime),
                    ),
                }
            )

    return {
        "total_files": len(files),
        "files": files,
    }
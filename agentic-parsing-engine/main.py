import os
from dotenv import load_dotenv

from ingestion.watcher import start_watcher


load_dotenv()

incoming_dir = os.getenv("INCOMING_DIR", "incoming")
processing_dir = os.getenv("PROCESSING_DIR", "processing")
processed_dir = os.getenv("PROCESSED_DIR", "processed")
rejected_dir = os.getenv("REJECTED_DIR", "rejected")
preprocessed_dir = os.getenv("PREPROCESSED_DIR", "preprocessed")
reports_dir = os.getenv("REPORTS_DIR", "reports")

print("===== Agentic Data Parsing Engine =====", flush=True)

start_watcher(
    incoming_dir=incoming_dir,
    processing_dir=processing_dir,
    processed_dir=processed_dir,
    rejected_dir=rejected_dir,
    preprocessed_dir=preprocessed_dir,
    reports_dir=reports_dir,
)
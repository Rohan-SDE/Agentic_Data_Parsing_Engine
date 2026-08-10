import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ingestion.validator import validate_file, move_file
from ingestion.preprocessor import preprocess_file
from agents.diagnostic_agent import run_diagnostic_agent


class FileWatcherHandler(FileSystemEventHandler):
    def __init__(self, processing_dir, processed_dir, rejected_dir, preprocessed_dir, reports_dir):
        self.processing_dir = processing_dir
        self.processed_dir = processed_dir
        self.rejected_dir = rejected_dir
        self.preprocessed_dir = preprocessed_dir
        self.reports_dir = reports_dir

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        print("\nNew file detected!", flush=True)
        print(f"File name: {file_path.name}", flush=True)
        print(f"Full path: {file_path}", flush=True)

        validation_result = validate_file(file_path)

        if not validation_result.is_valid:
            rejected_path = move_file(file_path, self.rejected_dir)

            print("Validation Status: FAILED", flush=True)
            print(f"Reason: {validation_result.message}", flush=True)
            print(f"Moved to: {rejected_path}", flush=True)
            return

        processing_path = move_file(file_path, self.processing_dir)

        print("Validation Status: PASSED", flush=True)
        print(f"Message: {validation_result.message}", flush=True)
        print(f"Moved to: {processing_path}", flush=True)

        preprocess_result = preprocess_file(processing_path, self.preprocessed_dir)

        if not preprocess_result.is_success:
            rejected_path = move_file(processing_path, self.rejected_dir)

            print("Preprocessing Status: FAILED", flush=True)
            print(f"Reason: {preprocess_result.message}", flush=True)
            print(f"Moved to rejected: {rejected_path}", flush=True)
            return

        print("Preprocessing Status: PASSED", flush=True)
        print(f"Message: {preprocess_result.message}", flush=True)
        print(f"Total items processed: {preprocess_result.total_items}", flush=True)
        print(f"Output folder: {preprocess_result.output_dir}", flush=True)
        print(f"Total chunks created: {len(preprocess_result.chunk_paths)}", flush=True)
        print(f"Metadata file: {preprocess_result.metadata_path}", flush=True)

        agent_result = run_diagnostic_agent(
            preprocessed_folder=preprocess_result.output_dir,
            reports_dir=self.reports_dir,
        )

        if agent_result.is_success:
            processed_path = move_file(processing_path, self.processed_dir)

            print("Agent Status: PASSED", flush=True)
            print(f"Message: {agent_result.message}", flush=True)
            print(f"Analysis JSON: {agent_result.analysis_path}", flush=True)
            print(f"Markdown Report: {agent_result.report_path}", flush=True)
            print(f"PDF Report: {agent_result.pdf_path}", flush=True)
            print(f"Original file moved to: {processed_path}", flush=True)
        else:
            print("Agent Status: FAILED", flush=True)
            print(f"Reason: {agent_result.message}", flush=True)
            print("Original file was kept in processing folder for debugging.", flush=True)


def start_watcher(incoming_dir, processing_dir, processed_dir, rejected_dir, preprocessed_dir, reports_dir):
    incoming_folder = Path(incoming_dir).resolve()

    if not incoming_folder.exists():
        incoming_folder.mkdir(parents=True, exist_ok=True)

    event_handler = FileWatcherHandler(
        processing_dir=processing_dir,
        processed_dir=processed_dir,
        rejected_dir=rejected_dir,
        preprocessed_dir=preprocessed_dir,
        reports_dir=reports_dir,
    )

    observer = Observer()
    observer.schedule(event_handler, str(incoming_folder), recursive=False)
    observer.start()

    print("===== Watcher Service Started =====", flush=True)
    print(f"Watching folder: {incoming_folder}", flush=True)
    print("Drop a file inside the incoming folder.", flush=True)
    print("Press CTRL + C to stop.", flush=True)

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping watcher...", flush=True)
        observer.stop()

    observer.join()
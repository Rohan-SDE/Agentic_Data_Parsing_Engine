from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from ingestion.validator import validate_file, move_file
from ingestion.preprocessor import preprocess_file
from agents.diagnostic_agent import run_diagnostic_agent


@dataclass
class PipelineResult:
    is_success: bool
    status: str
    message: str
    original_file: Optional[str] = None
    processing_file: Optional[str] = None
    processed_file: Optional[str] = None
    rejected_file: Optional[str] = None
    preprocessed_folder: Optional[str] = None
    analysis_json: Optional[str] = None
    markdown_report: Optional[str] = None
    pdf_report: Optional[str] = None


def process_file_pipeline(
    file_path,
    processing_dir,
    processed_dir,
    rejected_dir,
    preprocessed_dir,
    reports_dir,
) -> PipelineResult:
    file_path = Path(file_path)

    validation_result = validate_file(file_path)

    if not validation_result.is_valid:
        rejected_path = move_file(file_path, rejected_dir)

        return PipelineResult(
            is_success=False,
            status="validation_failed",
            message=validation_result.message,
            original_file=str(file_path),
            rejected_file=str(rejected_path),
        )

    processing_path = move_file(file_path, processing_dir)

    preprocess_result = preprocess_file(
        file_path=processing_path,
        preprocessed_dir=preprocessed_dir,
    )

    if not preprocess_result.is_success:
        rejected_path = move_file(processing_path, rejected_dir)

        return PipelineResult(
            is_success=False,
            status="preprocessing_failed",
            message=preprocess_result.message,
            original_file=str(file_path),
            processing_file=str(processing_path),
            rejected_file=str(rejected_path),
        )

    agent_result = run_diagnostic_agent(
        preprocessed_folder=preprocess_result.output_dir,
        reports_dir=reports_dir,
    )

    if not agent_result.is_success:
        return PipelineResult(
            is_success=False,
            status="agent_failed",
            message=agent_result.message,
            original_file=str(file_path),
            processing_file=str(processing_path),
            preprocessed_folder=str(preprocess_result.output_dir),
        )

    processed_path = move_file(processing_path, processed_dir)

    return PipelineResult(
        is_success=True,
        status="completed",
        message=agent_result.message,
        original_file=str(file_path),
        processed_file=str(processed_path),
        preprocessed_folder=str(preprocess_result.output_dir),
        analysis_json=str(agent_result.analysis_path),
        markdown_report=str(agent_result.report_path),
        pdf_report=str(agent_result.pdf_path),
    )
import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from agents.tools import analyze_preprocessed_folder
from reporting.pdf_generator import generate_pdf_from_markdown


load_dotenv()


@dataclass
@dataclass
class AgentRunResult:
    is_success: bool
    message: str
    report_path: Optional[Path] = None
    analysis_path: Optional[Path] = None
    pdf_path: Optional[Path] = None


def env_bool(value: str) -> bool:
    return str(value).strip().lower() in ["true", "1", "yes", "y"]


def build_agent_prompt(analysis_data: dict) -> str:
    compact_data = json.dumps(analysis_data, indent=2, default=str)

    if len(compact_data) > 12000:
        compact_data = compact_data[:12000] + "\n...DATA TRUNCATED..."

    return f"""
You are an expert systems diagnostic engineer.

Your job:
Analyze the telemetry/log analysis data below and generate a clear diagnostic report.

The report must contain:
1. Executive Summary
2. Key Anomalies
3. Possible Root Causes
4. Risk Level
5. Recommended Actions

Analysis data:
{compact_data}
"""


def call_ollama(prompt: str) -> str:
    import requests

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3")

    url = f"{base_url}/api/chat"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert systems diagnostic engineer.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "stream": False,
    }

    response = requests.post(url, json=payload, timeout=180)
    response.raise_for_status()

    data = response.json()

    return data["message"]["content"]


def build_recommendations(analysis_data: dict) -> list:
    recommendations = []

    anomalies = analysis_data.get("anomalies", [])

    anomaly_text = json.dumps(anomalies, default=str).lower()

    if "temperature" in anomaly_text or "temp" in anomaly_text:
        recommendations.append("Check motor cooling, airflow, and heat buildup around the affected motor.")

    if "voltage" in anomaly_text:
        recommendations.append("Inspect battery health, wiring, ESC connection, and voltage stability.")

    if "gps" in anomaly_text or "drift" in anomaly_text:
        recommendations.append("Recalibrate GPS/compass and test in an open area with strong satellite visibility.")

    if "error" in anomaly_text or "failed" in anomaly_text:
        recommendations.append("Review system logs around the error timestamp and reproduce the issue in a controlled test.")

    if not recommendations:
        recommendations.append("No critical anomaly pattern was detected. Continue monitoring with more flight/log data.")

    return recommendations


def build_fallback_report(analysis_data: dict) -> str:
    source_file = analysis_data.get("source_file")
    file_type = analysis_data.get("file_type")
    total_items = analysis_data.get("total_items")
    total_chunks = analysis_data.get("total_chunks")
    anomaly_count = analysis_data.get("anomaly_count", 0)
    anomalies = analysis_data.get("anomalies", [])
    numeric_summary = analysis_data.get("numeric_summary", {})

    if anomaly_count >= 5:
        risk_level = "HIGH"
    elif anomaly_count >= 1:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    lines = []

    lines.append("# Agentic Diagnostic Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"- Source file: `{source_file}`")
    lines.append(f"- File type: `{file_type}`")
    lines.append(f"- Total items analyzed: `{total_items}`")
    lines.append(f"- Total chunks analyzed: `{total_chunks}`")
    lines.append(f"- Total anomalies detected: `{anomaly_count}`")
    lines.append(f"- Risk level: **{risk_level}**")
    lines.append("")

    lines.append("## 2. Numeric Summary")
    lines.append("")

    if numeric_summary:
        for column, stats in numeric_summary.items():
            lines.append(f"### {column}")
            lines.append(f"- Min: {stats.get('min')}")
            lines.append(f"- Max: {stats.get('max')}")
            lines.append(f"- Mean: {stats.get('mean')}")
            lines.append(f"- Median: {stats.get('median')}")
            lines.append(f"- Standard deviation: {stats.get('std')}")
            lines.append("")
    else:
        lines.append("No numeric columns were available for statistical analysis.")
        lines.append("")

    lines.append("## 3. Key Anomalies")
    lines.append("")

    if anomalies:
        for index, anomaly in enumerate(anomalies[:20], start=1):
            lines.append(f"### Anomaly {index}")
            lines.append(f"- Reason: {anomaly.get('reason', anomaly.get('keyword', 'Unknown'))}")
            lines.append(f"- Column: {anomaly.get('column', 'N/A')}")
            lines.append(f"- Value: {anomaly.get('value', 'N/A')}")
            lines.append(f"- Context: `{anomaly.get('context', anomaly.get('line', 'N/A'))}`")
            lines.append("")
    else:
        lines.append("No major anomaly was detected.")
        lines.append("")

    lines.append("## 4. Possible Root Causes")
    lines.append("")

    if anomaly_count == 0:
        lines.append("- The file appears stable based on the current rules.")
    else:
        lines.append("- Sensor instability or environmental interference may be present.")
        lines.append("- Hardware stress may be visible if high temperature or low voltage appears.")
        lines.append("- Software or communication errors may be present if warning/error log lines exist.")

    lines.append("")

    lines.append("## 5. Recommended Actions")
    lines.append("")

    for recommendation in build_recommendations(analysis_data):
        lines.append(f"- {recommendation}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Generated by Agentic Data Parsing Engine.")

    return "\n".join(lines)


def run_diagnostic_agent(preprocessed_folder, reports_dir) -> AgentRunResult:
    try:
        preprocessed_folder = Path(preprocessed_folder)
        reports_dir = Path(reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)

        analysis_data = analyze_preprocessed_folder(preprocessed_folder)

        analysis_path = reports_dir / f"{preprocessed_folder.name}_analysis.json"

        with open(analysis_path, "w", encoding="utf-8") as file:
            json.dump(analysis_data, file, indent=4, default=str)

        enable_llm = env_bool(os.getenv("ENABLE_LLM", "false"))

        if enable_llm:
            try:
                prompt = build_agent_prompt(analysis_data)
                report_content = call_ollama(prompt)
                generation_mode = "LLM mode using Ollama"
            except Exception as error:
                report_content = build_fallback_report(analysis_data)
                generation_mode = f"Fallback mode because Ollama failed: {error}"
        else:
            report_content = build_fallback_report(analysis_data)
            generation_mode = "Fallback tool-based mode"

        report_path = reports_dir / f"{preprocessed_folder.name}_agent_report.md"

        with open(report_path, "w", encoding="utf-8") as file:
            file.write(report_content)

        pdf_path = generate_pdf_from_markdown(report_path)

        return AgentRunResult(
            is_success=True,
            message=f"Diagnostic agent completed successfully. Mode: {generation_mode}",
            report_path=report_path,
            analysis_path=analysis_path,
            pdf_path=pdf_path,
        )

    except Exception as error:
        return AgentRunResult(
            is_success=False,
            message=f"Diagnostic agent failed: {error}",
        )
import io
import json
from html import escape

from engine import __version__


def markdown(report):
    # Escape Markdown syntax in user-originated text so findings cannot inject links or headings.
    def plain(value):
        text = str(value).replace("\n", " ").replace("\r", " ")
        for ch in "\\`*_{}[]<>()#+-.!|":
            text = text.replace(ch, "\\" + ch)
        return text
    lines = ["# Agentic Data Parsing Engine — Analysis report", "",
             f"**Dataset:** {plain(report['dataset']['filename'])}",
             f"**SHA-256:** {report['dataset']['sha256']}",
             f"**Objective:** {plain(report['objective'])}",
             f"**Records:** {report['rows']:,} | **Columns:** {report['column_count']} | **Sample:** {report['sample_size']:,}",
             f"**Analysis:** {report['status']} | **Planner:** {report['planner']['mode']}", "", "## Findings", ""]
    if not report["findings"]:
        lines.append("No findings from the configured checks. This does not prove the data is error-free.")
    for item in report["findings"]:
        lines += [f"### {item['severity']} — {plain(item['column'] or 'Dataset')}",
                  plain(item["message"]), "", f"Scope: {item['scope']}",
                  f"Action: {plain(item['recommended_action'])}", "", "Evidence:", ""]
        lines.append("    " + json.dumps(item["evidence"], ensure_ascii=True))
        lines.append("")
    lines += ["## Scope and limitations", ""] + ["- " + item for item in report["limitations"]]
    return "\n".join(lines)


def pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph("Agentic Data Parsing Engine", styles["Title"]),
             Paragraph("Evidence-based analysis report", styles["Heading2"]), Spacer(1, .15 * inch)]
    def para(value, style="BodyText"):
        # Built-in PDF fonts cover Latin-1; preserve full Unicode in JSON/Markdown exports.
        text = str(value).encode("latin-1", "replace").decode("latin-1")
        return Paragraph(escape(text), styles[style])
    for text in (f"Dataset: {report['dataset']['filename']}", f"SHA-256: {report['dataset']['sha256']}",
                 f"Objective: {report['objective']}",
                 f"Records: {report['rows']:,} | Columns: {report['column_count']} | Sample: {report['sample_size']:,}",
                 f"Analysis: {report['status']} | Planner: {report['planner']['mode']}"):
        story += [para(text), Spacer(1, 6)]
    story.append(para("Findings", "Heading1"))
    if not report["findings"]:
        story.append(para("No findings from the configured checks. This does not prove the data is error-free."))
    for item in report["findings"]:
        story += [para(f"{item['severity']} | {item['column'] or 'Dataset'}", "Heading2"),
                  para(item["message"]), para("Scope: " + item["scope"]),
                  para("Action: " + item["recommended_action"])]
        for evidence in item["evidence"]:
            story.append(para(json.dumps(evidence, ensure_ascii=True)))
        story.append(Spacer(1, 8))
    story.append(para("Scope and limitations", "Heading1"))
    for item in report["limitations"]:
        story += [para(item), Spacer(1, 5)]
    story.append(para("For lossless Unicode text and machine-readable evidence, use JSON or Markdown export."))
    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor("#d9e1ee"))
        canvas.line(40, 42, 555, 42)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(40, 28, f"ADPE {__version__} | Private diagnostic report")
        canvas.drawRightString(555, 28, str(doc.page))
    SimpleDocTemplate(stream, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=60).build(
        story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()

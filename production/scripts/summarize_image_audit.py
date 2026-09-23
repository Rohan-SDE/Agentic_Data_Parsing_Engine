"""Print package-level scan diagnostics without application data or credentials."""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit("No image audit report was produced; inspect the scanner failure.")
report = json.loads(path.read_text())
print(json.dumps({"image": report.get("ArtifactName"), "digests": report.get("Metadata", {}).get("RepoDigests")}))
for result in report.get("Results", []):
    for finding in result.get("Vulnerabilities", []):
        print(json.dumps({"target": result["Target"], "id": finding["VulnerabilityID"],
            "package": finding["PkgName"], "installed": finding["InstalledVersion"],
            "fixed": finding.get("FixedVersion"), "severity": finding["Severity"]}))

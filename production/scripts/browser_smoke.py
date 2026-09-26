"""Isolated browser + real API + real worker acceptance test. No default app credentials."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from playwright.sync_api import expect, sync_playwright
    with tempfile.TemporaryDirectory(prefix="adpe-browser-") as tmp:
        work = Path(tmp)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        env = {**os.environ, "ADPE_ENVIRONMENT": "test", "ADPE_DATA_DIR": str(work / "data"),
               "ADPE_DATABASE_URL": "sqlite:///" + str(work / "db.sqlite"), "ADPE_PUBLIC_ORIGIN": origin,
               "ADPE_ALLOWED_HOSTS": '["127.0.0.1","localhost"]', "ADPE_SECURE_COOKIES": "false",
               "ADPE_OLLAMA_ENABLED": "false", "ADPE_PUBLIC_REGISTRATION": "true"}
        subprocess.run([sys.executable, "-m", "engine.cli", "migrate"], cwd=ROOT, env=env, check=True)
        password = secrets.token_urlsafe(24)
        # Only the child receives this temporary test account's password.
        setup = "from engine.db import session_factory; from engine.security import create_user; import os; " \
                "db=session_factory()(); create_user(db,'browseruser',os.environ['ADPE_TEST_PASSWORD'],True); db.commit()"
        subprocess.run([sys.executable, "-c", setup], cwd=ROOT, env={**env, "ADPE_TEST_PASSWORD": password}, check=True)
        out = ROOT / "test-results"
        out.mkdir(exist_ok=True)
        with (out / "browser-server.log").open("w") as log:
            api = subprocess.Popen([sys.executable, "-m", "engine.cli", "serve", "--port", str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=log)
            worker = subprocess.Popen([sys.executable, "-m", "engine.cli", "worker"],
                                      cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(60):
                    try:
                        urllib.request.urlopen(origin + "/health/ready", timeout=1)
                        break
                    except OSError:
                        if api.poll() is not None:
                            raise RuntimeError("API exited; inspect test-results/browser-server.log") from None
                        time.sleep(.25)
                errors = []
                with sync_playwright() as p:
                    launch = {"headless": True}
                    if os.environ.get("ADPE_TEST_CHROMIUM"):
                        launch.update(executable_path=os.environ["ADPE_TEST_CHROMIUM"],
                                      args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote", "--single-process"])
                    browser = p.chromium.launch(**launch)
                    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin)
                    expect(page.locator("#login-view")).to_be_visible()
                    page.screenshot(path=str(out / "login-desktop.png"), full_page=True)
                    page.locator("#login-form").get_by_label("Username", exact=True).fill("browseruser")
                    page.locator("#login-form").get_by_label("Password", exact=True).fill(password)
                    page.get_by_role("button", name="Open workspace").click()
                    expect(page.locator("#workspace")).to_be_visible()
                    page.get_by_role("button", name="Upload a dataset").click()
                    page.locator("#file-input").set_input_files(str(ROOT / "examples/drone_telemetry.csv"))
                    page.locator('#upload-form button[type="submit"]').click()
                    expect(page.locator("#analysis-dialog")).to_be_visible()
                    page.locator("#analysis-dialog summary").click()
                    page.locator("#thresholds").fill('{"motor_temperature":{"max":80},"battery_voltage":{"min":10}}')
                    page.get_by_role("button", name="Run analysis").click()
                    expect(page.locator("#report-content")).to_contain_text("Findings", timeout=45000)
                    expect(page.locator("#report-content")).to_contain_text("breach configured limits")
                    page.screenshot(path=str(out / "report-desktop.png"), full_page=True)
                    with page.expect_download() as download:
                        page.get_by_role("link", name="PDF ↓").click()
                    report = download.value
                    report.save_as(str(out / "browser-report.pdf"))
                    assert (out / "browser-report.pdf").read_bytes().startswith(b"%PDF")
                    page.get_by_role("button", name="Overview", exact=True).click()
                    expect(page.locator("#metric-datasets")).to_have_text("1")
                    page.screenshot(path=str(out / "overview-desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(out / "overview-mobile.png"), full_page=True)
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.get_by_role("button", name="Administration", exact=True).click()
                    expect(page.locator("#users-list")).to_contain_text("browseruser")
                    page.get_by_role("button", name="Sign out", exact=True).click()
                    expect(page.locator("#login-view")).to_be_visible()
                    page.locator("#signup-toggle").click()
                    page.locator("#signup-username").fill("publicvisitor")
                    page.locator("#signup-password").fill(password)
                    page.locator("#signup-confirm").fill(password + "wrong")
                    page.locator('#signup-form button[type="submit"]').click()
                    expect(page.locator("#signup-error")).to_contain_text("Passwords do not match")
                    page.locator("#signup-confirm").fill(password)
                    page.locator('#signup-form button[type="submit"]').click()
                    expect(page.locator("#auth-notice")).to_contain_text("Account created")
                    page.locator("#login-form").get_by_label("Password", exact=True).fill(password)
                    page.get_by_role("button", name="Open workspace").click()
                    expect(page.locator("#workspace")).to_be_visible()
                    expect(page.locator("#metric-datasets")).to_have_text("0")
                    expect(page.get_by_role("button", name="Administration", exact=True)).to_be_hidden()
                    assert not errors, errors
                    browser.close()
                result = {"status": "passed", "checks": ["login", "upload", "thresholds", "real worker",
                    "report", "PDF download", "dashboard", "mobile overflow", "admin", "logout", "public signup", "password confirmation", "private new workspace", "no JS page errors"]}
                (out / "browser-result.json").write_text(json.dumps(result, indent=2))
                print(json.dumps(result))
            finally:
                for process in (worker, api):
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()

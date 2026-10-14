"""Optional real-browser smoke. Requires Playwright plus Chromium; otherwise records UNEXECUTED."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from live_server import LiveServer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evidence/validation/browser.json"


def run() -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"outcome": "UNEXECUTED", "reason": "Optional Playwright package is not installed"}
    with sync_playwright() as playwright:
        executable = shutil.which("chromium")
        bundled = Path(playwright.chromium.executable_path)
        if executable is None and not bundled.is_file():
            return {"outcome": "UNEXECUTED", "reason": "No bundled or system Chromium executable is available"}
        with LiveServer(OUTPUT.parent / "browser-server.log") as server:
            browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox"])
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 1500})
                external = []
                def allow_local(route) -> None:
                    if route.request.url.startswith(server.url + "/"):
                        route.continue_()
                    else:
                        external.append(route.request.url.split("?", 1)[0])
                        route.abort()
                context.route("**/*", allow_local)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.url)
                page.locator("#key").fill(server.key)
                page.locator("#url").fill("https://example.com/browser-demo")
                page.locator("#idem").fill("browser-fixture-0001")
                page.locator("#create-form button").click()
                page.wait_for_selector("#created a")
                code = page.locator("#code").input_value()
                if len(code) != 8:
                    raise RuntimeError("Console did not show a generated short code")
                page.locator("#stats-form button[type=submit]").click()
                page.wait_for_function("document.querySelector('#stats').textContent.includes('total_redirects')")
                if json.loads(page.locator("#stats").inner_text())["total_redirects"] != 0:
                    raise RuntimeError("Unexpected initial browser statistics")
                page.evaluate("code => fetch('/r/' + code, {redirect: 'manual'}).then(r => r.type)", code)
                page.locator("#stats-form button[type=submit]").click()
                page.wait_for_function("JSON.parse(document.querySelector('#stats').textContent).total_redirects === 1")
                page.locator("#workflow-form button").click()
                page.wait_for_function("document.querySelector('#workflow').textContent.includes('WAITING_REQUIREMENTS')")
                page.locator("#reviewer").fill(server.key)
                page.locator("#gate").select_option("requirements")
                page.locator("#reason").fill("Automated browser negative-role fixture; not a human decision")
                page.locator("#approve-form button[type=submit]").click()
                page.wait_for_function("document.querySelector('#workflow').textContent.includes('403')")
                if page.evaluate("localStorage.length + sessionStorage.length") != 0:
                    raise RuntimeError("Browser storage unexpectedly contains data")
                page.locator("#key").fill("")
                page.locator("#reviewer").fill("")
                page.screenshot(path=str(OUTPUT.parent / "console.png"), full_page=True)
                page.locator("#clear").click()
                if page.locator("#created").inner_text() or page.locator("#stats").inner_text():
                    raise RuntimeError("Clear action did not remove results")
                if errors or external:
                    raise RuntimeError("Unexpected script errors or external browser requests")
                context.close()
                return {"outcome": "PASS", "browser": browser.version, "headless": True, "system_chromium": bool(executable),
                        "checked_at_utc_epoch": time.time(), "external_requests": 0, "uncaught_page_errors": 0,
                        "checks": ["offline console render", "form-driven authenticated creation", "analytics read",
                                   "manual redirect counted without following external destination", "workflow waits for approval",
                                   "operator cannot approve through reviewer form", "no local/session storage", "clear credentials and results"],
                        "human_approval_claimed": False, "sandbox_note": "Browser sandbox disabled only for this isolated container test; not an application deployment recommendation"}
            finally:
                browser.close()


if __name__ == "__main__":
    try:
        report = run()
    except Exception as exc:
        detail = str(exc)
        report = {"outcome": "UNEXECUTED" if "ERR_BLOCKED_BY_ADMINISTRATOR" in detail else "FAIL",
                  "reason": "Managed browser policy blocks loopback navigation" if "ERR_BLOCKED_BY_ADMINISTRATOR" in detail else type(exc).__name__,
                  "detail": detail[:1000], "human_approval_claimed": False,
                  "note": "Browser policy is not bypassed. No GUI success is claimed on this failure."}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["outcome"] == "PASS" else 2)

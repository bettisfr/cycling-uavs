#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch rendered rider page HTML using local Brave profile.")
    ap.add_argument("--url", required=True, help="Rider URL, e.g. https://www.strava.com/pros/8409483")
    ap.add_argument("--rider-id", required=True, help="Rider id label for output file, e.g. B002")
    ap.add_argument("--dataset-dir", default="giro_2026")
    ap.add_argument("--brave-bin", default="/usr/bin/brave-browser")
    ap.add_argument("--session-cookie-file", default="strava_session_cookie.txt")
    ap.add_argument("--headless", action="store_true", help="Run headless (default: headed)")
    ap.add_argument("--timeout-sec", type=int, default=10, help="Selector/page timeout in seconds")
    args = ap.parse_args()

    out_dir = Path(args.dataset_dir) / "raw" / "riders"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.rider_id}.txt"

    cookie = Path(args.session_cookie_file).read_text(encoding="utf-8").strip()
    if not cookie:
        raise SystemExit(f"Empty session cookie file: {args.session_cookie_file}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            executable_path=args.brave_bin,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        try:
            ctx = browser.new_context()
            ctx.add_cookies(
                [
                    {
                        "name": "_strava4_session",
                        "value": cookie,
                        "domain": ".strava.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                    }
                ]
            )
            page = ctx.new_page()
            timeout_ms = max(1, int(args.timeout_sec)) * 1000
            page.goto(args.url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Wait for feed cards to appear in rendered DOM.
            page.wait_for_selector(".CQdSY", timeout=timeout_ms)
            html = page.evaluate(
                """() => {
                    const main = document.querySelector('main#main') || document.querySelector('main');
                    if (main) return main.outerHTML;
                    return document.documentElement.outerHTML;
                }"""
            )
            out_path.write_text(html, encoding="utf-8")
            print(f"saved={out_path}")
            print(f"bytes={len(html)}")
            return 0
        except PlaywrightTimeoutError as exc:
            raise SystemExit(f"Timeout while loading rider page/feed: {exc}") from exc
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

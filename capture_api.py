#!/usr/bin/env python3
"""capture_api.py — Capture runtime /api/... requests from jkt48.com.

Headless Chrome (via Selenium WebDriver BiDi) visits each main page in a fresh
browser session, scrolls to trigger lazy loads, and records every network
request through the WebDriver BiDi `network.beforeRequestSent` /
`network.responseCompleted` events. A fresh session per page keeps attribution
clean and avoids a Selenium BiDi quirk where events stop being delivered after
the first navigation.

This reveals the /api/v1/ endpoints the SPA calls at runtime — paths that are
constructed dynamically and never appear as literals in the JS bundles.

Output (in --outdir, default ./out):
  api_endpoints_runtime.txt   unique /api/... URLs + method + status
  api_runtime_map.json        page -> {endpoint: [method, status]}

Usage:
  python capture_api.py [--pages "route,route,..."] [--hold S] [--outdir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE = "https://jkt48.com"

DEFAULT_ROUTES = [
    "/",
    "/news",
    "/member",
    "/schedule",
    "/discography",
    "/groups",
    "/theater",
    "/photobook",
    "/video-ofc",
    "/fan-club",
    "/how-to-purchase",
    "/notification",
    "/my-page",
    "/ofc-benefit",
    "/about/jkt48",
    "/member/detail?member=ABIGAIL_RACHEL",
    "/news/pengumuman-mengenai-pertunjukan-teater-kelulusan-dan-prosesi-pelepasan-kabesha-alya-amanda",
    "/ja/news",
    "/ja/member",
    "/ja/schedule",
]

STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
)


class NetworkRecorder:
    """Thread-safe collector for BiDi network events."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: dict[str, dict] = {}   # url -> {"method", "status", "pages"}
        self.url_by_id: dict[str, str] = {}   # request id -> url

    @staticmethod
    def _get(event, *keys):
        cur = event
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                cur = getattr(cur, k, None)
            if cur is None:
                return None
        return cur

    def on_before(self, event) -> None:
        url = self._get(event, "request", "url")
        method = self._get(event, "request", "method")
        rid = self._get(event, "request", "request")
        if not (url and str(url).startswith("http")):
            return
        with self.lock:
            if rid:
                self.url_by_id[str(rid)] = str(url)
            rec = self.requests.setdefault(str(url), {"method": method, "status": None, "pages": set()})
            rec["pages"].add(CURRENT_PAGE[0])

    def on_response(self, event) -> None:
        status = self._get(event, "response", "status")
        if status is None:
            return
        with self.lock:
            rid = event.get("request") if isinstance(event, dict) else getattr(event, "request", None)
            url = self.url_by_id.get(str(rid)) if rid else None
            if url is None:
                url = self._get(event, "response", "url")
            rec = self.requests.get(str(url)) if url else None
            if rec is not None:
                rec["status"] = status


CURRENT_PAGE: list[str] = [""]


def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--lang=id-ID")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent="
                         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
    options.set_capability("webSocketUrl", True)
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    return driver


def capture_page(route: str, hold: float) -> dict[str, dict]:
    """Visit one page in a fresh browser session; return {url: rec} for /api/ calls."""
    url = route if route.startswith("http") else BASE + route
    CURRENT_PAGE[0] = route
    driver = make_driver()
    recorder = NetworkRecorder()
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
        network = driver.network
        network.add_event_handler("before_request_sent", recorder.on_before)
        network.add_event_handler("response_completed", recorder.on_response)

        driver.get(url)
        time.sleep(hold)
        title = driver.title
        if title == "Just a moment...":   # Cloudflare interstitial
            print(f"  ! challenge on {route}, waiting...")
            time.sleep(6)
            title = driver.title
            if title == "Just a moment...":
                driver.refresh()
                time.sleep(hold)
        # Trigger lazy-loaded content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(hold / 2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)
    finally:
        driver.quit()
    return {u: r for u, r in recorder.requests.items() if "/api/" in u}


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture jkt48.com runtime API calls via BiDi")
    ap.add_argument("--pages", default=",".join(DEFAULT_ROUTES),
                    help="comma-separated routes to visit")
    ap.add_argument("--hold", type=float, default=4.0,
                    help="seconds to wait after page load before scrolling (default 4)")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    routes = [r.strip() for r in args.pages.split(",") if r.strip()]

    all_api: dict[str, dict] = {}   # url -> {"method", "status", "pages": set}
    page_map: dict[str, dict] = {}
    for route in routes:
        api = capture_page(route, args.hold)
        print(f"  {route:<58} {len(api)} api calls")
        page_map[route] = {u: [r["method"], r["status"]] for u, r in sorted(api.items())}
        for u, r in api.items():
            rec = all_api.setdefault(u, {"method": r["method"], "status": r["status"], "pages": set()})
            rec["pages"].add(route)

    # Verify each endpoint's real availability via curl_cffi (headless Chrome's
    # own /api/ fetches get Cloudflare-challenged, but the endpoints are live)
    print("\nVerifying endpoints with curl_cffi...")
    from curl_cffi import requests as cr
    for u in list(all_api.keys()):
        try:
            r = cr.get(u, impersonate="chrome124", timeout=25)
            all_api[u]["verify_status"] = r.status_code
            all_api[u]["verify_ct"] = (r.headers.get("content-type") or "").split(";")[0]
        except Exception as e:
            all_api[u]["verify_status"] = "ERR"
            all_api[u]["verify_ct"] = type(e).__name__

    def write(name: str, content: str) -> None:
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    lines = sorted(f"{r['method']} {u}  [browser:{r['status']} verify:{r.get('verify_status')} {r.get('verify_ct','')}]"
                   for u, r in all_api.items())
    write("api_endpoints_runtime.txt", "\n".join(lines) + ("\n" if lines else ""))
    write("api_runtime_map.json", json.dumps(page_map, indent=1, ensure_ascii=False))

    print("\n== RUNTIME API ENDPOINTS ==")
    for u, r in sorted(all_api.items()):
        pages = ", ".join(sorted(r["pages"]))
        print(f"  {r['method']:<5} [browser:{r['status']} verify:{r.get('verify_status')}] {u}")
        print(f"                    -> {pages}")
    print(f"\n{len(all_api)} unique endpoints across {len(routes)} pages")
    print(f"Output: {args.outdir}/api_endpoints_runtime.txt, api_runtime_map.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

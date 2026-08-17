#!/usr/bin/env python3
"""enumerate_schedules.py — Enumerate all theater shows/events of jkt48.com.

Calls the public backend API `/api/v1/schedules?lang=id&month=M&year=Y` for
every month in the data range (2018-01 .. 2026-12), collecting each schedule's
slug/link. Event detail pages live at `/schedule/<link>` (and `/ja/schedule/<link>`).

Output (in --outdir, default ./out):
  schedule_events.txt   TSV: schedule_id, date, type, title, link
  schedule_urls.txt     unique event page URLs (id + ja)
  Also merges new event URLs into pages.txt

Usage:
  python enumerate_schedules.py [--from 2018-01] [--to 2026-12] [--delay 0.5]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from curl_cffi import requests as cr

BASE = "https://jkt48.com"
API = f"{BASE}/api/v1/schedules"


def fetch_month(year: int, month: int) -> list[dict]:
    url = f"{API}?lang=id&month={month}&year={year}"
    for attempt in range(3):
        try:
            r = cr.get(url, impersonate="chrome124", timeout=30)
            if r.status_code == 200:
                return r.json().get("data") or []
        except Exception:
            pass
        time.sleep(1.0 * (attempt + 1))
    return []


def iter_months(start: str, end: str):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate jkt48.com theater schedules")
    ap.add_argument("--from", dest="frm", default="2018-01")
    ap.add_argument("--to", dest="to", default="2026-12")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    events: dict[int, dict] = {}   # schedule_id -> record
    empty_months = 0
    for i, (y, m) in enumerate(iter_months(args.frm, args.to)):
        items = fetch_month(y, m)
        if not items:
            empty_months += 1
        for it in items:
            sid = it.get("schedule_id")
            if sid is None:
                continue
            events[sid] = {
                "schedule_id": sid,
                "date": it.get("date", ""),
                "type": it.get("type", ""),
                "title": (it.get("title") or "").strip(),
                "link": (it.get("link") or "").strip(),
            }
        time.sleep(args.delay)
        if (i + 1) % 24 == 0:
            print(f"  ... {i + 1} months done, {len(events)} unique schedules", flush=True)
    print(f"done: {len(events)} unique schedules across {i + 1} months "
          f"({empty_months} empty months)")

    # Event page URLs (id + ja), deduped by link
    urls: set[str] = set()
    for rec in events.values():
        if not rec["link"]:
            continue
        urls.add(f"{BASE}/schedule/{rec['link']}")
        urls.add(f"{BASE}/ja/schedule/{rec['link']}")

    def write(name: str, content: str) -> None:
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    rows = sorted(events.values(), key=lambda r: (r["date"], r["schedule_id"]))
    write("schedule_events.txt",
          "\n".join(f"{r['schedule_id']}\t{r['date']}\t{r['type']}\t{r['title']}\t{r['link']}"
                    for r in rows) + "\n")
    write("schedule_urls.txt", "\n".join(sorted(urls)) + ("\n" if urls else ""))

    # Merge missing event URLs into the main page inventory
    pages_path = os.path.join(args.outdir, "pages.txt")
    if os.path.exists(pages_path):
        pages = set(open(pages_path, encoding="utf-8").read().splitlines())
        new = urls - pages
        if new:
            merged = sorted(pages | urls)
            write("pages.txt", "\n".join(merged) + "\n")
            print(f"merged {len(new)} new event URLs into pages.txt "
                  f"(total {len(merged)})")
        else:
            print("no new event URLs to merge (all already in pages.txt)")

    types = {}
    for r in events.values():
        types[r["type"]] = types.get(r["type"], 0) + 1
    print(f"event types: {types}")
    print(f"with link: {sum(1 for r in events.values() if r['link'])}")
    print(f"Output: {args.outdir}/schedule_events.txt, schedule_urls.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

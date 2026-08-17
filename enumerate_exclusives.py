#!/usr/bin/env python3
"""enumerate_exclusives.py — Enumerate exclusive products of jkt48.com.

Sources for codes:
  1. GET /api/v1/exclusives?lang=id        -> active list (code, title, category)
  2. reference_code of EXCLUSIVE schedules  -> cross-check (already covered by 1)

For each code, GET /api/v1/exclusives/{CODE}?lang=id gives full detail
(price, quota, sessions, media URLs under /api/v1/storages/...).

Output (in --outdir, default ./out):
  exclusives.txt               TSV: code, category, title, price
  exclusive_media_urls.txt     all /api/v1/storages/... URLs from exclusives
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from curl_cffi import requests as cr

BASE = "https://jkt48.com"
API = f"{BASE}/api/v1/exclusives"
STORAGE_RE = re.compile(r"(https://jkt48\.com/api/v1/storages/[A-Za-z0-9_\-/.:?=&%]+)")


def get_json(url: str) -> dict | None:
    for attempt in range(3):
        try:
            r = cr.get(url, impersonate="chrome124", timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.0 * (attempt + 1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate jkt48.com exclusive products")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    # 1) active list
    d = get_json(f"{API}?lang=id")
    if not d or not d.get("status"):
        print("! cannot fetch exclusives list")
        return 1
    items = d.get("data") or []
    codes = [it.get("code") for it in items if it.get("code")]
    print(f"active exclusives list: {len(codes)} codes")
    for it in items:
        print(f"  {it.get('code'):<8} {str(it.get('category')):<22} {it.get('title')}")

    # 2) detail per code
    details: dict[str, dict] = {}
    media: set[str] = set()
    for code in codes:
        dd = get_json(f"{API}/{code}?lang=id")
        if not dd or not dd.get("status"):
            print(f"  ! detail failed: {code}")
            time.sleep(args.delay)
            continue
        data = dd.get("data") or {}
        details[code] = {
            "code": code,
            "category": data.get("category"),
            "title": (data.get("title") or "").strip(),
            "price": data.get("default_price"),
            "quota": data.get("total_quota"),
            "valid_from": data.get("valid_date_from"),
            "valid_to": data.get("valid_date_to"),
        }
        for key in ("thumbnail_image", "preview_image"):
            u = data.get(key)
            if u:
                media.add(u)
        # content_body + short_description may embed more media URLs
        for key in ("content_body", "short_description"):
            text = data.get(key) or ""
            media.update(STORAGE_RE.findall(text))
        time.sleep(args.delay)

    def write(name: str, content: str) -> None:
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    rows = sorted(details.values(), key=lambda r: r["code"])
    write("exclusives.txt", "\n".join(
        f"{r['code']}\t{r['category']}\t{r['title']}\t{r['price']}\t{r['quota']}\t{r['valid_from']}\t{r['valid_to']}"
        for r in rows) + "\n")
    write("exclusive_media_urls.txt", "\n".join(sorted(media)) + ("\n" if media else ""))

    # merge media URLs into the main raw inventory
    allp = os.path.join(args.outdir, "all_urls.txt")
    if os.path.exists(allp):
        cur = set(open(allp, encoding="utf-8").read().splitlines())
        new = media - cur
        if new:
            merged = sorted(cur | media)
            write("all_urls.txt", "\n".join(merged) + "\n")
            print(f"merged {len(new)} exclusive media URLs into all_urls.txt (total {len(merged)})")
        else:
            print("no new exclusive media URLs (all already present)")

    print(f"\ntotal: {len(details)} exclusive details, {len(media)} unique media URLs")
    print(f"Output: {args.outdir}/exclusives.txt, exclusive_media_urls.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

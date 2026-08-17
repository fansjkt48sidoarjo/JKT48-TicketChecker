#!/usr/bin/env python3
"""enumerate_member_details.py — Enumerate jkt48.com member details.

GET /api/v1/members            -> list of 62 members (jkt48_member_id, code)
GET /api/v1/members/{id}       -> full profile: photos + social media handles

Output (in --outdir, default ./out):
  members_detail.txt          TSV: id, code, name, type, photo, twitter, instagram, tiktok
  member_media_urls.txt       unique /api/v1/storages/... photos
  member_socials.txt          constructed external URLs (twitter/instagram/tiktok)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

from curl_cffi import requests as cr

BASE = "https://jkt48.com"
API = f"{BASE}/api/v1/members"
STORAGE_RE = re.compile(r"(?:https://jkt48\.com)?/?api/v1/storages/[A-Za-z0-9_\-/.:?=&%]+")


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


def social_url(platform: str, handle: str) -> str | None:
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return None
    if platform == "twitter":
        return f"https://twitter.com/{handle}"
    if platform == "instagram":
        return f"https://instagram.com/{handle}"
    if platform == "tiktok":
        return f"https://tiktok.com/@{handle}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate jkt48.com member details")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    d = get_json(f"{API}?lang=id")
    if not d or not d.get("status"):
        print("! cannot fetch member list")
        return 1
    members = d.get("data") or []
    ids = [(m.get("jkt48_member_id"), m.get("code"), m.get("name")) for m in members]
    print(f"member list: {len(ids)}")

    rows: list[dict] = []
    media: set[str] = set()
    socials: set[str] = set()
    for mid, code, name in ids:
        dd = get_json(f"{API}/{mid}?lang=id")
        if not dd or not dd.get("status"):
            print(f"  ! detail failed: {mid} {code}")
            time.sleep(args.delay)
            continue
        data = dd.get("data") or {}
        for k in ("photo", "photo_1", "photo_2", "photo_3"):
            v = data.get(k) or ""
            for m in STORAGE_RE.findall(v):
                media.add(m if m.startswith("http") else f"{BASE}/{m.lstrip('/')}")
        for plat in ("twitter", "instagram", "tiktok"):
            u = social_url(plat, data.get(f"{plat}_account"))
            if u:
                socials.add(u)
        rows.append({
            "id": mid, "code": code, "name": (data.get("name") or name or "").strip(),
            "type": data.get("type") or "", "photo": data.get("photo_1") or data.get("photo") or "",
            "twitter": data.get("twitter_account") or "",
            "instagram": data.get("instagram_account") or "",
            "tiktok": data.get("tiktok_account") or "",
        })
        time.sleep(args.delay)
        if len(rows) % 15 == 0:
            print(f"  ... {len(rows)}/{len(ids)} done", flush=True)

    def write(name: str, content: str) -> None:
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    rows_sorted = sorted(rows, key=lambda r: r["id"])
    write("members_detail.txt", "\n".join(
        f"{r['id']}\t{r['code']}\t{r['name']}\t{r['type']}\t{r['photo']}\t"
        f"{r['twitter']}\t{r['instagram']}\t{r['tiktok']}" for r in rows_sorted) + "\n")
    write("member_media_urls.txt", "\n".join(sorted(media)) + ("\n" if media else ""))
    write("member_socials.txt", "\n".join(sorted(socials)) + ("\n" if socials else ""))

    # merge media into raw inventory; socials into external inventory
    allp = os.path.join(args.outdir, "all_urls.txt")
    if os.path.exists(allp):
        cur = set(open(allp, encoding="utf-8").read().splitlines())
        new = media - cur
        if new:
            write("all_urls.txt", "\n".join(sorted(cur | media)) + "\n")
            print(f"merged {len(new)} member media URLs into all_urls.txt")
    extp = os.path.join(args.outdir, "external.txt")
    if os.path.exists(extp):
        cur = set(open(extp, encoding="utf-8").read().splitlines())
        new = socials - cur
        if new:
            write("external.txt", "\n".join(sorted(cur | socials)) + "\n")
            print(f"merged {len(new)} member social URLs into external.txt")

    print(f"\ntotal: {len(rows_sorted)} member details, {len(media)} media URLs, {len(socials)} socials")
    print(f"Output: {args.outdir}/members_detail.txt, member_media_urls.txt, member_socials.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

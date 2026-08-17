#!/usr/bin/env python3
"""crawl_jkt48.py — Comprehensive URL crawler for jkt48.com.

Strategy (5 phases):
  1. Sitemap seed: robots.txt -> sitemap_index.xml -> per-language sitemaps.
  2. Route map: the main Nuxt bundle carries the complete route table
     (path:"/...") — same set as the auto-generated sitemap, kept as a cross-check.
  3. BFS crawl: fetch same-origin HTML pages, extract <a href> + hreflang links
     to discover dynamic pages the sitemap omits (news articles, ...).
  4. Bundle scan: Nuxt JS bundles + SSR payloads -> literal /api/... endpoints.
  5. API enumeration: the backend API is publicly readable (no auth):
       /api/v1/news?limit=100&page=N  -> ALL 1,842 news article slugs
       /api/v1/members                -> ALL 62 members + member photo URLs
     This yields complete coverage of dynamic content far beyond page crawling.

Stack: Python stdlib + curl_cffi (browser TLS impersonation to get past
Cloudflare's basic bot checks) + lxml (HTML/XML parsing). No new dependencies.

Output (in --outdir, default ./out):
  pages.txt           page URLs only (curated)
  api_endpoints.txt   discovered /api/... endpoints
  external.txt        off-site links found (not crawled)
  blocked.txt         URLs Cloudflare refused (403) during the crawl
  all_urls.txt        every URL seen, deduped and normalized
  summary.json        counts + crawl stats

Usage:
  python crawl_jkt48.py [--max-pages N] [--depth N] [--delay S] [--outdir DIR]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from curl_cffi import requests as cr
from lxml import etree, html

BASE = "https://jkt48.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Rotated on 403 so Cloudflare can't pin a single fingerprint.
IMPERSONATIONS = ["chrome124", "chrome120", "safari17_0", "edge101"]

ASSET_EXT = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".avif", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".mp3",
    ".pdf", ".zip", ".gz", ".webmanifest", ".txt",
}
# Query params that are noise: Cloudflare challenge tokens + common trackers.
TRACKING_PARAMS = {
    "__cf_chl_tk", "__cf_chl_rt_tk", "__cf_chl_f_tk",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid",
}

API_RE = re.compile(r"(?<![\w])(/api/[A-Za-z0-9_\-/{}.:?=&%@]+)")
ROUTE_RE = re.compile(r'path:"(/[A-Za-z0-9_\-/]*)"')
SITEMAP_RE = re.compile(r"(?im)^\s*Sitemap:\s*(\S+)")
NUXT_SCRIPT_RE = re.compile(r"/_nuxt/[A-Za-z0-9_\-]+\.js")

NEWS_API = f"{BASE}/api/v1/news"
MEMBERS_API = f"{BASE}/api/v1/members"


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize(url: str) -> str | None:
    """Absolute + http(s), strip fragment/tracking params, lowercase host,
    canonicalize www.jkt48.com -> jkt48.com."""
    url = (url or "").strip()
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:", "ftp:", "file:")):
        return None
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return None
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
          if k not in TRACKING_PARAMS]
    netloc = p.netloc.lower()
    if netloc == "www.jkt48.com":
        netloc = "jkt48.com"
    p = p._replace(
        fragment="",
        query=urlencode(qs),
        scheme=p.scheme.lower(),
        netloc=netloc,
    )
    return urlunparse(p)


def is_same_origin(url: str) -> bool:
    host = urlparse(url).netloc
    return host == "jkt48.com" or host.endswith(".jkt48.com")


def classify(url: str) -> str:
    p = urlparse(url)
    if not is_same_origin(url):
        return "external"
    path = p.path.lower()
    if path.startswith("/cdn-cgi/"):
        return "system"
    if "/api/" in path:
        return "api"
    if os.path.splitext(path)[1] in ASSET_EXT:
        return "asset"
    return "page"


class Fetcher:
    """curl_cffi session with politeness (throttle + jitter), retries, impersonation rotation."""

    def __init__(self, delay: float, verbose: bool):
        self.session = cr.Session(impersonate=IMPERSONATIONS[0], timeout=30)
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "id-ID,id;q=0.9,ja;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.delay = delay
        self.verbose = verbose
        self.last_req = 0.0
        self.stats = collections.Counter()
        self.blocked: set[str] = set()

    def _throttle(self) -> None:
        wait = self.delay * (0.75 + 0.5 * random.random()) - (time.monotonic() - self.last_req)
        if wait > 0:
            time.sleep(wait)
        self.last_req = time.monotonic()

    def get(self, url: str, retries: int = 3) -> cr.Response | None:
        for attempt in range(retries):
            self._throttle()
            imp = IMPERSONATIONS[attempt % len(IMPERSONATIONS)]
            try:
                r = self.session.get(url, impersonate=imp)
            except Exception as e:  # network hiccup -> retry with backoff
                self.stats["errors"] += 1
                if self.verbose:
                    log(f"  ! error {url}: {e.__class__.__name__} (attempt {attempt + 1})")
                if attempt == retries - 1:
                    return None
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 403:  # Cloudflare block -> one retry, then record
                self.stats["403"] += 1
                if attempt == 1:
                    self.blocked.add(url)
                    if self.verbose:
                        log(f"  ! 403 (blocked) {url}")
                    return None
                if self.verbose:
                    log(f"  ! 403 {url} (retry {attempt + 1}/2)")
                time.sleep(1.5)
                continue
            self.stats[str(r.status_code)] += 1
            if self.verbose:
                log(f"  {r.status_code} {url}")
            return r
        return None

    def get_json(self, url: str) -> dict | None:
        r = self.get(url)
        if r is None or r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None


def fetch_sitemap_urls(fetcher: Fetcher, sitemap_url: str, seen: set[str], out: list[str]) -> None:
    """Fetch a sitemap (index or urlset) and collect all <loc> + hreflang alternates."""
    if sitemap_url in seen:
        return
    seen.add(sitemap_url)
    r = fetcher.get(sitemap_url)
    if r is None or r.status_code != 200:
        log(f"  ! sitemap fetch failed: {sitemap_url}")
        return
    try:
        root = etree.fromstring(r.content)
    except Exception as e:
        log(f"  ! sitemap parse failed: {sitemap_url} ({e})")
        return
    locs = root.xpath("//*[local-name()='loc']/text()")
    alternates = root.xpath("//*[local-name()='link'][@hreflang]/@href")
    if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
        for loc in locs:
            fetch_sitemap_urls(fetcher, loc.strip(), seen, out)
    else:
        out.extend(l.strip() for l in locs)
        out.extend(a.strip() for a in alternates)
    log(f"  sitemap {sitemap_url}: {len(locs)} entries")


def scan_bundles(fetcher: Fetcher) -> tuple[set[str], set[str], int]:
    """Fetch homepage + all Nuxt bundles in ONE pass.
    Returns (route_map, api_paths, bundles_scanned)."""
    r = fetcher.get(f"{BASE}/")
    if r is None or r.status_code != 200:
        return set(), set(), 0
    apis: set[str] = set(API_RE.findall(r.text or ""))
    scripts = NUXT_SCRIPT_RE.findall(r.text or "")
    routes: set[str] = set()
    scanned = 0
    for s in sorted(set(scripts)):
        b = fetcher.get(f"{BASE}{s}")
        if b is None or b.status_code != 200:
            continue
        scanned += 1
        text = b.text or ""
        routes |= set(ROUTE_RE.findall(text))
        apis.update(API_RE.findall(text))
    # Keep only route-shaped paths (extensionless, not _nuxt)
    routes = {r for r in routes if r.startswith("/") and "/_nuxt" not in r
              and "." not in r.rsplit("/", 1)[-1]}
    if routes:
        log(f"  route map extracted: {len(routes)} routes")
    return routes, apis, scanned


def crawl_pages(fetcher: Fetcher, seeds: list[str], max_pages: int, depth: int) -> tuple[set[str], set[str], set[str]]:
    """BFS over same-origin HTML pages. Returns (pages, external_links, all_found)."""
    pages: set[str] = set()
    external: set[str] = set()
    all_found: set[str] = set()
    queue: collections.deque[tuple[str, int]] = collections.deque()
    for s in seeds:
        if s not in pages:
            pages.add(s)
            queue.append((s, 0))
    fetched = 0
    while queue and fetched < max_pages:
        url, d = queue.popleft()
        if d >= depth:
            continue
        r = fetcher.get(url)
        if r is None or r.status_code != 200:
            continue
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "xml" not in ctype:
            continue
        fetched += 1
        try:
            tree = html.fromstring(r.content)
            hrefs = tree.xpath("//a/@href | //link[@rel='alternate']/@href")
        except Exception:
            continue
        for h in hrefs:
            n = normalize(urljoin(url, h))
            if not n:
                continue
            all_found.add(n)
            cat = classify(n)
            if cat == "external":
                external.add(n)
            elif cat == "page" and n not in pages:
                pages.add(n)
                queue.append((n, d + 1))
        if fetched % 50 == 0:
            log(f"  ... fetched {fetched} pages, {len(pages)} discovered")
    return pages, external, all_found


def enumerate_api(fetcher: Fetcher) -> tuple[set[str], set[str], int]:
    """Enumerate dynamic content via the public backend API.
    Returns (page_urls, api_urls, news_items)."""
    page_urls: set[str] = set()
    api_urls: set[str] = set()

    # 1) All news articles (paginated, 100/page)
    page_no = 1
    news_items = 0
    while True:
        d = fetcher.get_json(f"{NEWS_API}?limit=100&page={page_no}")
        if not d or not d.get("status"):
            break
        items = d.get("data") or []
        if not items:
            break
        for it in items:
            slug = (it.get("link") or "").strip()
            if slug:
                page_urls.add(f"{BASE}/news/{slug}")
                page_urls.add(f"{BASE}/ja/news/{slug}")
                news_items += 1
        meta = d.get("_meta") or {}
        total_pages = int(meta.get("total_page") or 0)
        if page_no >= total_pages:
            break
        page_no += 1
    log(f"  news enumerated: {news_items} articles across {page_no} API pages")

    # 2) All members (single response)
    d = fetcher.get_json(f"{MEMBERS_API}?limit=100")
    if d and d.get("status"):
        members = d.get("data") or []
        for m in members:
            code = (m.get("code") or "").strip()
            if code:
                page_urls.add(f"{BASE}/member/detail?member={code}")
                page_urls.add(f"{BASE}/ja/member/detail?member={code}")
            photo = (m.get("photo") or "").strip()
            if photo:
                api_urls.add(photo)
        log(f"  members enumerated: {len(members)}")
    return page_urls, api_urls, news_items


def main() -> int:
    ap = argparse.ArgumentParser(description="Comprehensive URL crawler for jkt48.com")
    ap.add_argument("--max-pages", type=int, default=250, help="max HTML pages to fetch (default 250)")
    ap.add_argument("--depth", type=int, default=2, help="BFS link depth (default 2)")
    ap.add_argument("--delay", type=float, default=0.5, help="base seconds between requests (default 0.5)")
    ap.add_argument("--outdir", default="out", help="output directory (default out)")
    ap.add_argument("--no-api", action="store_true", help="skip bundle scan + API enumeration")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    fetcher = Fetcher(args.delay, args.verbose)

    log(f"== Phase 1: sitemap seed ({BASE}) ==")
    sitemap_urls: list[str] = []
    robots = fetcher.get(f"{BASE}/robots.txt")
    if robots is not None and robots.status_code == 200:
        for s in SITEMAP_RE.findall(robots.text or ""):
            fetch_sitemap_urls(fetcher, s.strip(), seen=set(), out=sitemap_urls)
    else:
        fetch_sitemap_urls(fetcher, f"{BASE}/sitemap_index.xml", seen=set(), out=sitemap_urls)

    apis: set[str] = set()
    routes: set[str] = set()
    bundles = 0
    if not args.no_api:
        log("== Phase 2: bundle scan (route map + API endpoints) ==")
        routes, apis, bundles = scan_bundles(fetcher)
    else:
        # Keep sitemap as the route inventory when skipping the bundle scan
        routes = {u.replace(f"{BASE}", "") or "/" for u in sitemap_urls if u.startswith(f"{BASE}")}

    seeds = sorted({normalize(u) for u in sitemap_urls if normalize(u)}
                   | {f"{BASE}{r}" for r in routes if r != "/"})
    log(f"== {len(seeds)} seed URLs (sitemap + route map) ==")

    log(f"== Phase 3: BFS page crawl (max {args.max_pages} fetches, depth {args.depth}) ==")
    pages, external, found = crawl_pages(fetcher, seeds, args.max_pages, args.depth)

    news_items = 0
    if not args.no_api:
        log("== Phase 4: API enumeration (news + members) ==")
        api_pages, api_urls, news_items = enumerate_api(fetcher)
        pages |= api_pages
        apis |= api_urls

    # Build normalized, categorized output
    pages_sorted = sorted(pages)
    apis_norm: set[str] = set()
    for u in apis:
        p = urlparse(u)
        if p.scheme:
            apis_norm.add(u)
        else:
            apis_norm.add(f"{BASE}{p.path}" if u.startswith("/") else f"/{u}")
    apis_sorted = sorted(apis_norm)
    external_sorted = sorted(external)
    assets = sorted(u for u in found if classify(u) == "asset")
    system = sorted(u for u in found if classify(u) == "system")
    all_urls = sorted(set(pages_sorted) | set(apis_sorted) | set(assets)
                      | set(system) | set(external_sorted))

    def write(name: str, lines: list[str]) -> None:
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

    write("pages.txt", pages_sorted)
    write("api_endpoints.txt", apis_sorted)
    write("external.txt", external_sorted)
    write("blocked.txt", sorted(fetcher.blocked))
    write("all_urls.txt", all_urls)

    summary = {
        "base": BASE,
        "seed_urls": len(seeds),
        "sitemap_urls": len(sitemap_urls),
        "route_map_urls": len(routes),
        "pages": len(pages_sorted),
        "news_articles": news_items,
        "api_endpoints": len(apis_sorted),
        "assets": len(assets),
        "external": len(external_sorted),
        "blocked_403": len(fetcher.blocked),
        "js_bundles_scanned": bundles,
        "http_stats": dict(fetcher.stats),
        "params": {"max_pages": args.max_pages, "depth": args.depth, "delay": args.delay},
    }
    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log("\n== RESULTS ==")
    log(f"  seed URLs (sitemap+route) : {len(seeds)}")
    log(f"  pages discovered           : {len(pages_sorted)}")
    log(f"    - news articles          : {news_items}")
    log(f"  API endpoints              : {len(apis_sorted)}")
    log(f"  assets                     : {len(assets)}")
    log(f"  external links             : {len(external_sorted)}")
    log(f"  blocked (403)              : {len(fetcher.blocked)}")
    log(f"  JS bundles scanned         : {bundles}")
    log(f"  HTTP stats                 : {dict(fetcher.stats)}")
    log(f"\nOutput written to {args.outdir}/ (pages.txt, api_endpoints.txt, "
        f"external.txt, blocked.txt, all_urls.txt, summary.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

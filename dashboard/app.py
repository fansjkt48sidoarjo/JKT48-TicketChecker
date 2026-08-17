"""JKT48 Exclusive Dashboard — API backend (Vercel Python / Flask).

Menjembatani dashboard ke API publik jkt48.com. Cloudflare memblokir klien HTTP
biasa (undici/requests) pada endpoint /api/v1/*, jadi pemanggilan memakai
`curl_cffi` dengan impersonasi TLS Chrome — satu-satunya yang lolos (terverifikasi).

Data di-cache di memori (TTL default 45 detik) supaya polling cepat dan tidak
membebani server jkt48. Response diberi Cache-Control agar CDN Vercel ikut
meng-cache.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cr
from flask import Flask, jsonify, request

BASE = "https://jkt48.com"
API_LIST = f"{BASE}/api/v1/exclusives?lang=id"
# TTL data jkt48: 10s agar histori kuota tercatat hampir real-time sesuai
# polling dashboard (10s). Naikkan via env CACHE_TTL kalau ingin lebih sopan.
CACHE_TTL = float(os.environ.get("CACHE_TTL", "10"))
FETCH_WORKERS = int(os.environ.get("FETCH_WORKERS", "6"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "25"))

# ---- histori transaksi (perubahan kuota tiap jalur) ----
MAX_HISTORY = 5000
COMPACT_HISTORY_LIMIT = 20  # panel ringkas cukup 20 terbaru; sisanya lewat /api/history
HISTORY_FILE = os.environ.get(
    "HISTORY_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
)
# Opsional: sinkronkan histori ke Google Spreadsheet via Apps Script web app.
# Jika kosong -> hanya file lokal (history.json).
SHEETS_URL = os.environ.get("SHEETS_WEBAPP_URL", "").strip()
_h: dict = {
    "entries": [],
    "last": {},
    "last_entry": {},
    "member_first_soldout": {},
    "synced_seq": -1,
    "last_sync_attempt": 0.0,
}


def _sheets_load() -> list | None:
    """Baca seluruh baris riwayat dari Google Sheets (Apps Script web app)."""
    if not SHEETS_URL:
        return None
    try:
        sep = "&" if "?" in SHEETS_URL else "?"
        r = cr.get(SHEETS_URL + sep + "action=log", impersonate="chrome124", timeout=20)
        if r.status_code == 200:
            data = r.json()
            if data.get("entries") is not None:
                return data["entries"]
    except Exception:
        pass
    return None


def _sheets_append(entry: dict) -> bool:
    if not SHEETS_URL:
        return False
    try:
        payload = {
            k: entry.get(k)
            for k in ("seq", "ts", "code", "title", "date", "session", "jalur", "member", "prev", "delta", "now")
        }
        payload["action"] = "append"
        r = cr.post(SHEETS_URL, json=payload, impersonate="chrome124", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def _recompute_soldout(entries: list) -> dict:
    out = {}
    ordered = sorted(entries, key=lambda e: e.get("seq", 0) if e.get("seq") is not None else 0)
    for e in ordered:
        if e.get("now") == 0 and (e.get("prev") or 0) > 0:
            out.setdefault(e.get("member") or "—", e.get("ts") or "")
    return out


def _load_history() -> None:
    entries: list = []
    file_data = None
    if SHEETS_URL:
        s = _sheets_load()
        if s:
            entries = s
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                file_data = json.load(f)
    except Exception:
        pass
    if file_data:
        seen = {e.get("seq") for e in entries if e.get("seq") is not None}
        for e in file_data.get("entries", []):
            if e.get("seq") is None or e.get("seq") not in seen:
                entries.append(e)
        if file_data.get("last"):
            _h["last"] = file_data.get("last", {})
    # normalisasi seq berurutan (entri lama di history.json belum punya seq)
    entries.sort(key=lambda e: (e.get("seq") if e.get("seq") is not None else e.get("t", 0)))
    if any(e.get("seq") is None for e in entries):
        for i, e in enumerate(entries):
            e["seq"] = i
    # dedupe by seq (spreadsheet bisa punya baris ganda akibat retry/backfill)
    seen_seq: set = set()
    uniq: list = []
    for e in entries:
        if e.get("seq") in seen_seq:
            continue
        seen_seq.add(e.get("seq"))
        uniq.append(e)
    entries = uniq
    _h["entries"] = entries
    _h["last_entry"] = {
        f"{e.get('code')}|{e.get('date')}|{e.get('session')}|{e.get('member')}": e
        for e in entries
        if e.get("code")
    }
    _h["member_first_soldout"] = _recompute_soldout(entries)
    _h["synced_seq"] = max([e.get("seq", -1) for e in entries] or [-1])


def _save_history() -> None:
    """Simpan ke file lokal (best effort) + sinkronkan entri baru ke Google Sheets."""
    with _lock:
        file_payload = {
            "entries": _h["entries"][-2000:],
            "last": _h["last"],
            "member_first_soldout": _h["member_first_soldout"],
        }
        unsynced = [e for e in _h["entries"] if e.get("seq", -1) > _h.get("synced_seq", -1)]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(file_payload, f, ensure_ascii=False)
    except Exception:
        pass  # read-only fs (Vercel) — histori tetap in-memory
    if not SHEETS_URL:
        return
    now = time.time()
    unsynced = [e for e in _h["entries"] if e.get("seq", -1) > _h.get("synced_seq", -1)]
    if not unsynced:
        return
    if now - _h.get("last_sync_attempt", 0) < 30:
        return  # cooldown setelah kegagalan sebelumnya — jangan pukul GAS tiap 10 detik
    for e in unsynced:
        if _sheets_append(e):
            with _lock:
                _h["synced_seq"] = max(_h.get("synced_seq", -1), e["seq"])
        else:
            break  # gagal — hentikan, cooldown 30 detik lalu coba lagi
    with _lock:
        _h["last_sync_attempt"] = now


def _update_history(payload: dict) -> None:
    """Bandingkan kuota tiap jalur dgn snapshot terakhir, catat perubahannya."""
    now_epoch = time.time()
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    changed = False
    for ex in payload.get("exclusives", []):
        code = ex.get("code")
        for s in ex.get("sessions", []):
            for j in s.get("jalur", []):
                avail = j.get("available")
                if avail is None:
                    continue
                key = f"{code}|{s.get('date')}|{s.get('label')}|{j.get('member')}"
                prev = _h["last"].get(key)
                _h["last"][key] = avail
                if prev is None or prev == avail:
                    continue
                ent = _h["last_entry"].get(key)
                # gabungkan jika perubahan berikutnya <8 detik (dedup multi-tab)
                if ent is not None and now_epoch - ent.get("t", 0) < 8:
                    ent["delta"] += avail - prev
                    ent["now"] = avail
                    ent["ts"] = now_ts
                else:
                    ent = {
                        "t": now_epoch,
                        "seq": len(_h["entries"]),
                        "ts": now_ts,
                        "key": key,
                        "code": code,
                        "title": ex.get("title"),
                        "date": s.get("date"),
                        "session": s.get("label"),
                        "jalur": j.get("label"),
                        "member": j.get("member"),
                        "prev": prev,
                        "delta": avail - prev,
                        "now": avail,
                    }
                    _h["entries"].append(ent)
                    _h["last_entry"][key] = ent
                changed = True
                if avail == 0 and prev > 0:
                    _h["member_first_soldout"].setdefault(j.get("member") or "—", now_ts)
    if changed:
        if len(_h["entries"]) > MAX_HISTORY:
            _h["entries"] = _h["entries"][-MAX_HISTORY:]


def _compact_entries() -> list[dict]:
    """Entri histori tanpa field internal, urut terbaru dulu (panel ringkas)."""
    out = []
    for e in reversed(_h["entries"][-COMPACT_HISTORY_LIMIT:]):
        out.append(_public_entry(e))
    return out


def _public_entry(e: dict) -> dict:
    return {
        "ts": e["ts"],
        "code": e["code"],
        "title": e.get("title"),
        "date": e.get("date"),
        "session": e.get("session"),
        "jalur": e.get("jalur"),
        "member": e.get("member"),
        "prev": e["prev"],
        "delta": e["delta"],
        "now": e["now"],
    }


_load_history()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,ja;q=0.8,en;q=0.7",
}

app = Flask(__name__, static_folder="public", static_url_path="")
_cache: dict[str, object] = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def fetch_json(url: str) -> dict | None:
    for attempt in range(3):
        try:
            r = cr.get(url, impersonate="chrome124", timeout=REQUEST_TIMEOUT, headers=HEADERS)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.5 * (attempt + 1))
    return None


def _fmt_price(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_image(item: dict) -> str | None:
    for key in ("thumbnail_image", "preview_image"):
        v = item.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _to_payload_item(code: str) -> dict | None:
    """Ambil detail satu exclusive lalu bentuk payload ringkas untuk frontend."""
    detail = fetch_json(f"{BASE}/api/v1/exclusives/{code}?lang=id")
    if not detail or not detail.get("status"):
        return None
    d = detail.get("data") or {}
    sales_period = []
    for sp in d.get("sales_period") or []:
        sales_period.append({
            "label": sp.get("label"),
            "start": sp.get("start_date"),
            "end": sp.get("end_date"),
            "ofc_only": bool(sp.get("is_ofc_only")),
        })
    sessions = []
    for sess in d.get("session") or []:
        jalur = []
        for j in sess.get("session_detail") or []:
            jalur.append({
                "label": j.get("label"),
                "member": j.get("jkt48_member_name"),
                "sold": j.get("tickets_sold"),
                "available": j.get("available_quota"),
            })
        sessions.append({
            "label": sess.get("label"),
            "date": sess.get("date"),
            "start_time": sess.get("start_time"),
            "end_time": sess.get("end_time"),
            "jalur": jalur,
        })
    return {
        "code": code,
        "title": (d.get("title") or "").strip(),
        "category": d.get("category"),
        "price": _fmt_price(d.get("default_price")),
        "total_quota": _fmt_price(d.get("total_quota")),
        "max_purchase": _fmt_price(d.get("max_purchase")),
        "thumbnail": _pick_image(d),
        "valid_from": d.get("valid_date_from"),
        "valid_to": d.get("valid_date_to"),
        "sales_period": sales_period,
        "sessions": sessions,
    }


def build_payload() -> dict | None:
    listing = fetch_json(API_LIST)
    if not listing or not listing.get("status"):
        return None
    codes = [it.get("code") for it in (listing.get("data") or []) if it.get("code")]
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(_to_payload_item, c): c for c in codes}
        for fut in as_completed(futures):
            try:
                item = fut.result()
                if item is not None:
                    items.append(item)
            except Exception:
                continue
    items.sort(key=lambda x: x["code"])
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "jkt48.com/api/v1/exclusives (public API)",
        "exclusives": items,
    }


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/exclusives")
def exclusives():
    now = time.time()
    with _lock:
        fresh = _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL
        if not fresh:
            data = build_payload()
            if data is not None:
                _cache["ts"] = now
                _cache["data"] = data
        payload = _cache["data"]
        if payload is not None:
            _update_history(payload)
            out = dict(payload)
            out["history"] = _compact_entries()
            out["member_first_soldout"] = _h["member_first_soldout"]
    if payload is None:
        return (
            jsonify({
                "error": "Gagal mengambil data dari API JKT48.",
                "hint": "Periksa kembali nanti; kemungkinan Cloudflare sedang menantang permintaan.",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }),
            502,
        )
    _save_history()
    resp = jsonify(out)
    # no-store agar CDN tidak menyajikan payload basi — histori butuh data segar tiap poll
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/history")
def history():
    """Seluruh histori dengan pagination + pencarian member (q).

    ?page=1&per_page=100&q=nama — urut terbaru dulu.
    """
    q = (request.args.get("q") or "").strip().lower()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    try:
        per_page = min(200, max(1, int(request.args.get("per_page", "100"))))
    except ValueError:
        per_page = 100
    with _lock:
        entries = _h["entries"]
        if q:
            entries = [e for e in entries if q in (e.get("member") or "").lower()]
        total = len(entries)
        rev = list(reversed(entries))
    start = (page - 1) * per_page
    out = [_public_entry(e) for e in rev[start : start + per_page]]
    return jsonify(
        {
            "entries": out,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, math.ceil(total / per_page)),
        }
    )


@app.get("/api/thumb")
def thumb():
    """Proxy thumbnail agar bisa dimuat browser (CF memblokir gambar /api/ dari browser)."""
    url = request.args.get("url", "")
    if not re.match(r"^https://jkt48\.com/api/v1/storages/[A-Za-z0-9_\-/.:?=&%]+$", url):
        return ("forbidden", 403)
    r = cr.get(url, impersonate="chrome124", timeout=20)
    if r.status_code != 200:
        return ("error fetching media", 502)
    resp = app.response_class(
        r.content, mimetype=r.headers.get("content-type") or "application/octet-stream"
    )
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "cached": _cache["data"] is not None,
            "history_entries": len(_h["entries"]),
            "first_soldout_members": len(_h["member_first_soldout"]),
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

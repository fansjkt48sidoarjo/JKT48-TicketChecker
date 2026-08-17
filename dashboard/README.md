# JKT48 Exclusive Dashboard

Dashboard real-time untuk memantau produk **exclusive JKT48** (digital
photobook, 2-shot, photocard, meet & greet): periode penjualan **OFC vs
General**, dan **kuota per jalur member** per sesi.

- Backend: Vercel Function (Python/Flask) yang menjembatani API publik
  `jkt48.com/api/v1/exclusives` memakai `curl_cffi` (impersonasi TLS Chrome —
  satu-satunya yang lolos Cloudflare; `fetch`/`requests` polos kena 403).
- Frontend: satu file `public/index.html` (vanilla JS, tanpa build step).
- Auto-refresh tiap 10 detik; server menyimpan **histori perubahan kuota**
  tiap jalur (awal → delta → kini) yang ditampilkan di tab **Riwayat**, dan
  mengurutkan statistik member berdasarkan "habis duluan".
- Tombol **Beli** pada jalur yang masih tersedia mengarah ke
  `jkt48.com/purchase/exclusive?code=<CODE>` (link penjualan resmi).

## Struktur

```
dashboard/
  app.py              # Flask entrypoint (route /api/exclusives, /api/health)
  requirements.txt    # flask + curl_cffi
  public/index.html   # dashboard UI (di-serve di "/")
  vercel.json         # konfigurasi function (maxDuration 60s)
```

## Deploy ke Vercel

1. Pastikan Vercel CLI terpasang: `npm i -g vercel` (atau import via git
   di vercel.com).
2. Dari folder ini:
   ```bash
   vercel login
   vercel deploy
   ```
3. Untuk production: `vercel --prod`.

Setelah deploy:
- Dashboard: `https://<proyek>.vercel.app/`
- API: `https://<proyek>.vercel.app/api/exclusives`
- Health: `https://<proyek>.vercel.app/api/health`

## Uji lokal

```bash
cd dashboard
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                    # http://localhost:8080
# API: curl http://localhost:8080/api/exclusives
# Dashboard: buka http://localhost:8080/ (atau ?demo=1 untuk data contoh)
```

## Konfigurasi (env vars opsional)

| Var | Default | Arti |
|---|---|---|
| `CACHE_TTL` | 10 | TTL cache server (detik) — 10s agar histori kuota hampir real-time. Naikkan kalau ingin lebih sopan ke API jkt48 |
| `FETCH_WORKERS` | 6 | Jumlah fetch detail paralel |
| `REQUEST_TIMEOUT` | 25 | Timeout tiap request ke jkt48 (detik) |
| `SHEETS_WEBAPP_URL` | — | URL Apps Script Web App (Google Sheets). Jika diisi, histori disinkronkan ke spreadsheet dan bertahan di Vercel (lihat di bawah) |
| `HISTORY_FILE` | `dashboard/history.json` | Lokasi file histori lokal (fallback / mode non-GAS) |

## Persistensi histori ke Google Spreadsheet (opsional)

Serverless Vercel punya filesystem read-only, jadi `history.json` hanya bertahan
secara lokal. Agar histori **tidak hilang saat cold start** di Vercel, sinkronkan
ke Google Spreadsheet via Apps Script (tanpa OAuth, tanpa database):

1. Buat spreadsheet baru di sheets.google.com.
2. Menu **Extensions > Apps Script**, hapus isi editor, tempel seluruh isi
   `google_apps_script.gs`, lalu simpan.
3. **Deploy > New deployment > Web app**:
   - Execute as: **Me**
   - Who has access: **Anyone**
   - Klik Deploy, setujui izin, salin **URL Web app** (berakhiran `/exec`).
4. Set env var `SHEETS_WEBAPP_URL` = URL tersebut (lokal: sebelum `python app.py`;
   Vercel: di dashboard project → Settings → Environment Variables).
5. Deploy ulang / restart server. Sheet **"Riwayat"** dibuat otomatis —
   tiap perubahan kuota menjadi satu baris (waktu, kode, member, sesi, jalur,
   awal → kini, delta). Kamu bisa membuka spreadsheet-nya kapan saja.

Perilaku: memori tetap sumber utama (cepat); spreadsheet jadi backup permanen.
Saat cold start, server membaca baris dari spreadsheet untuk membangun ulang
state, lalu melanjutkan. Jika `SHEETS_WEBAPP_URL` tidak diisi, perilaku kembali
ke file lokal (`history.json`) — dua-duanya aman di-deploy kapan pun.

## Histori transaksi

Setiap polling, server membandingkan kuota tiap jalur dengan snapshot
terakhir dan mencatat: waktu, produk, sesi, member, jumlah awal, perubahan,
dankuota terkini (maks 5.000 entri; payload mengirim 300 terbaru).
`member_first_soldout` mencatat waktu pertama tiap member kehabisan tiket —
dipakai tab Statistik untuk mengurutkan "Habis duluan".

- Lokal: histori disimpan di `history.json` (bertahan antar-restart).
- Vercel: filesystem read-only — histori tersimpan **in-memory** per instance
  dan hilang saat cold start (bisa dihubungkan ke Vercel KV via env nanti).

## Catatan etis & teknis

- Hanya membaca **data publik** (endpoint tanpa auth) dengan rate-limit sopan.
  Tidak ada otomasi pembelian, tidak ada manipulasi antrean, tidak ada akses
  area member. Tombol Beli hanya membuka link penjualan resmi di tab baru.
- Polling 10s berarti ±16 fetch detail per refresh — kalau Cloudflare mulai
  menantang `curl_cffi`, naikkan `CACHE_TTL` dan `REQUEST_TIMEOUT`, atau pindah
  sumber data (mis. Firecrawl) di `fetch_json()` di `app.py`.

# Panduan: Hubungkan Dashboard ke Google Spreadsheet

Tujuan: menyimpan seluruh riwayat perubahan kuota ke spreadsheet Google-mu
sendiri, sehingga tidak hilang saat serverless Vercel cold start.

> **Ilustrasi visual** dialog-dialog penting: saat server lokal berjalan, buka
> `http://localhost:8080/ilustrasi-deploy.html` (atau buka file
> `public/ilustrasi-deploy.html` langsung di browser). Catatan: itu
> *ilustrasi*, bukan tangkapan layar asli — tampilan Google bisa sedikit
> berbeda (bahasa, warna, ukuran). Setiap langkah di bawah disertai deskripsi
> "apa yang akan kamu lihat".

---

## Ringkasan (5 langkah)

1. Buat spreadsheet baru
2. Tempel `google_apps_script.gs` ke Apps Script
3. Deploy sebagai Web app (Execute as: Me, Who has access: Anyone)
4. Salin URL `/exec` → set env `SHEETS_WEBAPP_URL`
5. Restart & verifikasi

---

## Langkah 1 — Buat spreadsheet

1. Buka [sheets.google.com](https://sheets.google.com) (login akun Google).
2. Klik **+ (Blank spreadsheet / Kosong)** — judul bebas, mis. `JKT48 Riwayat`.
3. Biarkan kosong dulu — sheet **"Riwayat"** dengan header akan dibuat
   otomatis oleh skrip. Kamu tidak perlu menyiapkan kolom apa pun.

## Langkah 2 — Tempel skrip ke Apps Script

1. Di jendela spreadsheet, buka menu **Extensions** (Ekstensi) →
   **Apps Script**. *(Editor baru akan terbuka di tab terpisah.)*
2. Di editor, **hapus semua kode bawaan** (`function myFunction() {...}`).
3. Buka file `google_apps_script.gs` dari folder proyek ini, salin seluruh
   isinya, tempel ke editor.
4. Tekan **Ctrl+S** (atau klik ikon 💾) untuk menyimpan.
5. *(Opsional)* Beri nama proyek: klik "Untitled project" di kiri atas →
   ketik mis. `jkt48-history` → rename.

## Langkah 3 — Deploy sebagai Web app

1. Di kanan atas editor, klik **Deploy → New deployment**.
2. Di dialog **New deployment**:
   - Kolom **Select type**: klik ikon ⚙️ (settings) di ujung kanan kolom →
     pilih **Web app**.
   - **Description**: boleh diisi, mis. `jkt48-history` (opsional).
   - **Execute as**: pilih **Me** *(akun Google-mu)*.
   - **Who has access**: pilih **Anyone** — bukan "Anyone with a Google
     account", karena dashboard memanggil URL tanpa login.
3. Klik **Deploy**.
4. **Pemberian izin** (hanya pertama kali):
   - Muncul dialog *"Authorization required"* → pilih akun Google-mu.
   - Muncul peringatan *"Google hasn't verified this app"* → klik
     **Advanced** → **Go to &lt;nama proyek&gt; (unsafe)** → **Allow**.
     *(Ini normal: skrip pribadi tidak melalui proses verifikasi Google.)*
5. Dialog sukses muncul → **Web app URL** yang berakhiran `/exec`.

## Langkah 4 — Salin URL dan set env var

1. Klik **Copy** pada Web app URL. Bentuknya:

   ```
   https://script.google.com/macros/s/AKfycbw...xxxx/exec
   ```

   > Pastikan berakhiran **`/exec`**. Jangan gunakan URL `/dev` (itu versi
   > uji sementara yang bisa berubah).

2. Set environment variable **`SHEETS_WEBAPP_URL`** ke URL tersebut:

   - **Lokal (uji):** sebelum menjalankan server,
     ```bash
     export SHEETS_WEBAPP_URL="https://script.google.com/macros/s/..../exec"
     python app.py
     ```
     (Windows PowerShell: `$env:SHEETS_WEBAPP_URL="..."`)
   - **Vercel:** dashboard Vercel → pilih project → **Settings →
     Environment Variables** → tambah `SHEETS_WEBAPP_URL` → **Redeploy**
     (Deployments → ⋯ → Redeploy) supaya env terbaca.

## Langkah 5 — Restart & verifikasi

1. Restart server / redeploy.
2. Uji endpoint-nya:
   ```bash
   curl "https://script.google.com/macros/s/..../exec?action=log"
   # benar   → {"entries":[]}          (spreadsheet masih kosong)
   # salah   → halaman login / HTML     → cek "Who has access" = Anyone
   ```
3. Buka dashboard. Saat ada perubahan kuota tiket (penjualan),
   spreadsheet-nya akan terisi baris demi baris:
   `seq, ts, code, title, date, session, jalur, member, prev, delta, now`.

---

## Cara kerja singkat

- Dashboard tetap berjalan normal (memori cepat). Setiap perubahan kuota,
  server *menambahkan satu baris* ke spreadsheet (tidak menulis ulang
  semuanya — hemat kuota Google).
- Saat cold start, server **membaca baris dari spreadsheet** untuk
  membangun ulang riwayat, lalu melanjutkan.
- Kalau `SHEETS_WEBAPP_URL` kosong → perilaku kembali ke file lokal
  (`history.json`). Keduanya aman, kapan pun di-deploy.

## Pemecahan masalah

| Gejala | Penyebab / Solusi |
|---|---|
| `curl ?action=log` mengembalikan HTML / halaman login | "Who has access" bukan **Anyone** → edit deployment (Deploy → Manage deployments → ✏️) lalu ganti |
| HTTP 404 di URL | URL tidak /exec, atau salah salin |
| Spreadsheet tidak terisi | Dashboard tidak melihat perubahan (kuota tidak berubah) — cek `/api/health` → `history_entries` bertambah |
| Script error "You need permission" | Lewati proses izin Langkah 3.4 sampai **Allow** |
| Ingin ganti kode skrip setelah deploy | Ubah kode → **Deploy → Manage deployments → ✏️ → New version** → Deploy (URL tetap sama) |

## Limitasi jujur

- Apps Script punya kuota harian (URL fetch ~20.000/hari untuk akun
  konsumen) — tidak relevan untuk pemakaian pribadi (tulis hanya saat ada
  perubahan).
- Kalau kamu **menghapus** spreadsheet / deployment, riwayat di cloud ikut
  hilang (file lokal `history.json` tetap ada sebagai cadangan).
- Akun Google yang dipakai untuk **Execute as: Me** harus tetap aktif;
  kalau password diubah/2FA bermasalah, akses bisa terganggu.

# Analisis Payload `/api/v1/menu`

Tanggal: 2026-08-17 · Sumber: `curl_cffi` (impersonate chrome124)

## Struktur respons

`GET https://jkt48.com/api/v1/menu?lang=id` dan `?lang=ja` mengembalikan
**3 item** (menu footer "Lainnya"):

```json
[
  { "menu_id": 2, "code": "TERMS_AND_CONDITIONS", "label": "Syarat dan Ketentuan",
    "url": "/term-condition", "parent_menu_id": 1, "status": true },
  { "menu_id": 1, "code": "OTHER", "label": "Lainnya", "url": "#", "parent_menu_id": null },
  { "menu_id": 3, "code": "WHAT_IS_JKT48", "label": "Apa itu JKT48",
    "url": "/about/jkt48", "parent_menu_id": 1, "status": true }
]
```

Field: `menu_id`, `code`, `label`, `url`, `parent_menu_id`, `status`.

## Temuan

| URL | Status inventori |
|---|---|
| `/term-condition` (+ `/ja/term-condition`) | **Sudah ada** (route map) |
| `/about/jkt48` (+ `/ja/about/jkt48`) | **Sudah ada** (route map) |

- **Tidak ada URL baru.** Menu hanya berisi 2 link nyata; keduanya sudah tercatat
  di `out/pages.txt` sejak fase route map crawler.
- Menu utama (Home/News/Member/Schedule/Discography/Groups) **tidak berasal dari
  endpoint ini** — kemungkinan hardcoded di bundle aplikasi / di-generate dari
  route Nuxt.
- Parameter `type`, `code`, `parent_menu_id` tidak mengubah hasil (tetap 3 item).
- Endpoint varian lain tidak ada: `/api/v1/menus`, `/api/v1/menu/header`,
  `/api/v1/menu/footer` → "Route not found".

## Kesimpulan

Payload menu tidak berkontribusi URL baru ke inventori. Inventori halaman saat
ini (3.943 URL di `out/pages.txt`) sudah mencakup semua link menu.

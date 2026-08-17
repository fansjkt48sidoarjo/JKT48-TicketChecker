/**
 * JKT48 Exclusive Dashboard — Google Apps Script (Web App)
 *
 * Cara pakai:
 *   1. Buat spreadsheet baru di sheets.google.com.
 *   2. Menu: Extensions > Apps Script.
 *   3. Hapus isi editor, tempel seluruh file ini, simpan (Ctrl+S).
 *   4. Deploy > New deployment > pilih type "Web app":
 *        - Execute as : Me
 *        - Who has access : Anyone
 *      Klik Deploy, setujui izin, lalu SALIN URL Web app-nya
 *      (berakhiran /exec).
 *   5. Set environment variable SHEETS_WEBAPP_URL = URL itu di Vercel
 *      (atau lokal: set sebelum menjalankan app.py).
 *
 * Skrip ini menyediakan:
 *   GET  <url>?action=log      -> semua baris riwayat sebagai JSON
 *   POST <url>  {action:"append", seq, ts, code, title, date, session,
 *                jalur, member, prev, delta, now}  -> menambah 1 baris
 *
 * Sheet "Riwayat" dibuat otomatis dengan header. Baris = 1 perubahan tiket.
 */

var HEADERS = ["seq", "ts", "code", "title", "date", "session", "jalur", "member", "prev", "delta", "now"];
var LOG = "Riwayat";

function ensureLogSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var s = ss.getSheetByName(LOG);
  if (!s) {
    s = ss.insertSheet(LOG);
    s.appendRow(HEADERS);
    s.setFrozenRows(1);
  }
  return s;
}

function json_(obj, code) {
  var out = ContentService.createTextOutput(JSON.stringify(obj));
  out.setMimeType(ContentService.MimeType.JSON);
  if (code) out.setStatusCode ? out : out; // ContentService tidak mendukung status code; biarkan 200
  return out;
}

function doGet(e) {
  try {
    var action = (e && e.parameter && e.parameter.action) || "log";
    if (action !== "log") return json_({ error: "unknown action: " + action });
    var s = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(LOG);
    if (!s || s.getLastRow() < 2) return json_({ entries: [] });
    var rows = s.getRange(2, 1, s.getLastRow() - 1, HEADERS.length).getValues();
    var entries = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r[0] === "" || r[0] == null) continue; // baris kosong
      entries.push({
        seq: Number(r[0]),
        ts: String(r[1]),
        code: String(r[2]),
        title: String(r[3]),
        date: String(r[4]),
        session: String(r[5]),
        jalur: String(r[6]),
        member: String(r[7]),
        prev: Number(r[8]),
        delta: Number(r[9]),
        now: Number(r[10])
      });
    }
    return json_({ entries: entries });
  } catch (err) {
    return json_({ error: String(err) });
  }
}

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var s = ensureLogSheet_();
    s.appendRow([
      body.seq, body.ts, body.code, body.title, body.date,
      body.session, body.jalur, body.member, body.prev, body.delta, body.now
    ]);
    return json_({ ok: true });
  } catch (err) {
    return json_({ error: String(err) });
  }
}

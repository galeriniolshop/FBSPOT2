# 🎬 YT Grabber — Streamlit Cloud Edition

Mesin pencari & pengunduh video YouTube (judul, deskripsi, tags, views)
lengkap dengan upload otomatis ke **febspot.com** (kanal milikmu) dan/atau **gofile.io**.
File unduhan **selalu dihapus otomatis** setelah terupload. Mendukung **multi-akun febspot**
(nama akun, email, jumlah video, views, subscriber, saldo/earning, daftar video + views per video).

## 📦 Isi paket

| File | Fungsi |
|---|---|
| `streamlit_app.py` | UI Streamlit (cari → info → download → upload → kelola akun) |
| `yt_tool.py` | Mesin inti (yt-dlp + gofile + febspot + multi-akun) |
| `requirements.txt` | Dependensi |
| `.streamlit/config.toml` | Tema gelap |

## 🚀 Langkah deploy (atau UPDATE jika sudah pernah)

1. **Push ke GitHub** (push = auto-redeploy):
   ```bash
   git add -A && git commit -m "fix: penyimpanan akun di cloud + multi-akun" && git push
   ```
   (atau upload file via web GitHub)

2. **Isi Secrets** (Apps → Settings → Secrets) — **WAJIB untuk permanen:**

   **Opsi A — multi-akun (disarankan):** salinan lengkap file akun:
   ```toml
   febspot_accounts = """
   {"active": "acc-1",
    "accounts": {
      "acc-1": {"id": "acc-1", "label": "Akun 1", "channel": "60404",
                "cookies": [{"domain": ".febspot.com", "name": "user_id", "value": "..."},
                            {"domain": ".febspot.com", "name": "kt_member", "value": "..."},
                            {"domain": ".febspot.com", "name": "PHPSESSID", "value": "..."},
                            {"domain": ".febspot.com", "name": "time", "value": "..."}]},
      "acc-2": {"id": "acc-2", "label": "Akun 2", "channel": "60404", "cookies": [...same...]}
    }}
   """
   ```

   **Opsi B — akun tunggal (paling simpel):**
   ```toml
   febspot_cookies = """
   [{"domain": ".febspot.com", "name": "user_id", "value": "..."},
    {"domain": ".febspot.com", "name": "kt_member", "value": "..."},
    {"domain": ".febspot.com", "name": "PHPSESSID", "value": "..."},
    {"domain": ".febspot.com", "name": "time", "value": "..."}]
   """
   febspot_channel = "60404"
   ```
   > Ambil nilai cookie dari extension *EditThisCookie* / *Cookie-Editor* saat login ke febspot.com.
   > Minimal: `kt_member`, `PHPSESSID`, `user_id`, `time`.

## ⚠️ PENTING: kenapa muncul "No such file or directory: '/home/user/febspot_accounts.json.tmp'"?

Di **Streamlit Cloud**, `/home/user` itu **read-only** (bahkan tidak ada) — menulis file ke sana
pasti gagal (`Errno 2`). Versi baru sudah diperbaiki:

- File akun & area kerja otomatis dialihkan ke **`/tmp` (writable)** saat jalan di cloud.
- `save_accounts()` membuat folder otomatis bila belum ada + pesan error yang jelas bila tetap gagal.
- **Tapi ingat**: `/tmp` di cloud cepat atau lambat dihapus (saat app sleep/restart).
  Jadi agar **cookies TIDAK hilang saat restart**, wajib isi **Secrets** di atas —
  saat app bangun, akun otomatis dimuat kembali dari Secrets.

**Ringkasan:**
| Tempat | Streaming Cloud | Self-host / VPS (versi Flask) |
|---|---|---|
| File `/tmp` | bertahan selama app aktif | — |
| File workspace | ❌ tidak bisa (read-only) | ✅ permanen |
| Secrets | ✅ permanen (tapi tidak bisa diubah dari UI) | opsional |
| **Cloudflare Worker + KV** | ✅ **permanen + bisa ditulis dari UI (terbaik)** | ✅ bisa juga |

## ☁️ Penyimpanan PERMANEN via Blogger DRAFT (paling gampang & dianjurkan)

Data akun & cache disimpan sebagai **post DRAFT** di blog Blogger milikmu —
**tidak pernah dipublish**, hanya pemilik blog (login Google) yang bisa melihat.
Kelebihan vs Cloudflare KV:

| | Blogger draft | Cloudflare KV (free) |
|---|---|---|
| Kuota | 10.000 request/hari | 1.000 **tulis**/hari |
| Batas tulis harian | ❌ tidak ada | ⚠️ ada (kemarin sempat habis) |
| Setup | OAuth1× (5 menit) | 2 menit |

**Setup sekali (5 menit):**
1. https://console.cloud.google.com → buat project (mis. `ytstore`)
2. **APIs & Services → Library** → cari **Blogger API v3** → **Enable**
3. **OAuth consent screen → External** → isi nama + email → (disarankan)
   ubah **Publishing status → In production** agar refresh token tidak
   kedaluwarsa 7 hari (skrin "unverified" aman untuk akun pribadi)
4. **Credentials → Create credentials → OAuth client ID → Desktop app**
   → salin **Client ID** + **Client Secret**
5. Buat blog di https://www.blogger.com (kalau belum punya)

**Lalu jalankan sekali (lokal/VPS):**
```bash
python3 auth_blogger.py --client-id=XXX --client-secret=YYY
```
→ buka URL di browser → Allow → salin kode dari address bar → tempel →
skrip menyimpan `blogger_oauth.json` (refresh_token + blog_id).

**Streamlit Cloud (Secrets):**
```toml
blogger_client_id     = "..."
blogger_client_secret = "..."
blogger_refresh_token = "..."
blogger_blog_id       = "1234567890123456789"
```
UI akan menampilkan **🟢 Sinkron Blogger AKTIF**. Blogger dipakai otomatis
bila terkonfigurasi; Cloudflare Worker tetap jadi cadangan/alternatif.

## ☁️ Penyimpanan PERMANEN via Cloudflare Worker + KV (alternatif)

Streamlit Cloud menghapus file `/tmp` saat app tidur — dan Secrets tidak bisa
diubah dari UI. Solusi terbaik: **Cloudflare Worker + KV** (gratis, 100 rb
baca/hari, 1 rb tulis/hari, 1 GB). Semua `accounts` (cookies multi-akun) dan
cache SafelinkU tersimpan di cloud; kamu bisa **tambah/hapus akun dari UI** dan
datanya tetap hidup setelah restart, ganti browser, bahkan ganti host app.

**Langkah 1 — deploy Worker (2 menit, tanpa koding):**
1. https://dash.cloudflare.com → **Workers & Pages → Create Worker** → paste isi `cloudflare/worker.js` → Deploy
2. **Settings → Bindings → Add binding:**
   - Type: *KV namespace* → Nama variabel: `ACCOUNTS_KV` → *Create namespace*
3. **Settings → Variables & Secrets → Add secret:** `API_TOKEN` = token rahasia acak (catat!)
4. Catat URL worker: `https://<nama>.<subdomain>.workers.dev`

> Alternatif CLI: `npx wrangler deploy` (lihat `cloudflare/wrangler.toml`).

**Langkah 2 — isi Secrets di Streamlit Cloud:**
```toml
febspot_sync_url   = "https://<nama>.<subdomain>.workers.dev"
febspot_sync_token = "<API_TOKEN yang tadi>"
```

**Langkah 3 — selesai.** UI menampilkan 🟢 *"Sinkron cloud AKTIF"*. Tiga lapis
cadangan otomatis: Worker (utama) → file lokal (cache) → Secrets (bootstrap).
Kalau token salah / worker mati, app tetap jalan dengan data lokal (failover).

## 🔗 Short link subscribe (SafelinkU)

Setiap video yang di-upload ke febspot otomatis menyisipkan baris di deskripsi:

```
📢 Subscribe: https://sfl.gl/ytsubscribe
```

- Link subscribe di-*shorten* via **SafelinkU API** (`POST /api/v1/links`, token Bearer).
- **Token diatur via Secrets** (cloud) atau file `safelinku.json` (self-host):
  ```toml
  # Streamlit Secrets
  SAFELINKU_TOKEN = "df27e90fd..."        # isi token kamu
  SUBSCRIBE_URL   = "https://www.youtube.com/channel/UCX-w2r2NSbyjd9pjHKcEJzg"
  SUBSCRIBE_ALIAS = "ytsubscribe"
  ```
- Hasil short link **di-cache** (`safelinku_cache.json`) — satu link dibuat sekali,
  lalu dipakai ulang di semua upload berikutnya (hemat rate limit 60/menit).
- Kalau API gagal (401/429/alias bentrok), mesin tetap memakai **URL asli** sebagai fallback.
- Short link juga tampil sebagai kartu **📢 SUBSCRIBE** di hasil job.

## 🖥️ Cara pakai

- **🔎 Cari** — kata kunci → daftar video (thumbnail, judul, views, durasi, channel) + pilih jumlah hasil.
- **📋 Info** — deskripsi + tags lengkap.
- **⬇️ Ambil** — download → upload sesuai tujuan (febspot / gofile / keduanya).
- **🤖 Biar Mesin Saja** — sekali klik: cari → video terpendek → download → upload → beres.
- **👥 Kelola Akun (sidebar)** — tambah akun (tempel cookies), lihat **data & earning**,
  refresh info, pilih akun aktif, hapus akun.
- **Dashboard akun aktif** — nama, email, ID, video, total views, subscriber, saldo, daftar video.

## ⚠️ Batasan Streamlit Cloud (free)

- **1 GB RAM** — hindari video > ~400 MB; gunakan mode 🤖 (terpendek).
- **Idle sleep** ±30–45 menit; app di-*cold start* ±15 dtk saat dibuka lagi.
- **Jangan tutup tab** saat job berjalan.
- IP cloud (GCP) kadang lebih ketat terhadap bot-check YouTube.

Untuk pemakaian berat, deploy versi Flask (`app.py` + `index.html`) ke Railway/Render/VPS.

## 🔒 Catatan

- Gunakan hanya untuk konten yang kamu berhak unggah.
- Jangan commit cookies ke GitHub — selalu lewat Secrets.
- Video yang di-upload ke febspot harus lolos moderasi/review kanal.

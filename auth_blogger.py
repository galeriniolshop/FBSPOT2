#!/usr/bin/env python3
"""
auth_blogger.py — SETUP SEKALI: otentikasi Google (OAuth) untuk penyimpanan Blogger
===================================================================================
Yang harus kamu siapkan dulu (sekali, ~5 menit):
  1. https://console.cloud.google.com → buat project baru (mis. "ytstore")
  2. APIs & Services → Library → cari "Blogger API v3" → ENABLE
  3. APIs & Services → OAuth consent screen → External → isi nama aplikasi + email →
     (disarankan) ubah Publishing status jadi "In production" agar refresh token
     tidak kedaluwarsa 7 hari. Skrin "unverified" tidak masalah untuk akun pribadi.
  4. APIs & Services → Credentials → Create credentials → OAuth client ID →
     Application type: DESKTOP APP → salin Client ID + Client Secret.
  5. (Opsional) Buat blog di https://www.blogger.com kalau belum punya.

Lalu jalankan:
  python3 auth_blogger.py --client-id=XXX --client-secret=YYY
  atau isi dulu /home/user/blogger_oauth.json: {"client_id": "...", "client_secret": "..."}
      lalu  python3 auth_blogger.py

Alur: buka URL di browser (login Google) → izinkan → alamat browser berubah jadi
http://127.0.0.1/?code=... (halaman tidak bisa dibuka — itu NORMAL) → salin kode
di address bar → tempel di sini → skrip menyimpan refresh_token + blog_id ke
/home/user/blogger_oauth.json.
"""
import argparse
import json
import os
import sys
import urllib.parse

import requests

OAUTH_FILE = os.environ.get("BLOGGER_OAUTH_FILE", "/home/user/blogger_oauth.json")
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def load_existing():
    if os.path.exists(OAUTH_FILE):
        try:
            return json.load(open(OAUTH_FILE))
        except Exception:
            return {}
    return {}


def save(cfg):
    json.dump(cfg, open(OAUTH_FILE, "w"), indent=2)
    print(f"\n✅ Tersimpan di {OAUTH_FILE}")
    print("   JANGAN commit file ini ke GitHub — isi hanya berisi token pribadimu.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", dest="cid", default="")
    ap.add_argument("--client-secret", dest="csec", default="")
    ap.add_argument("--code", default="", help="kode OAuth (bisa ditempel di sini)")
    args = ap.parse_args()

    cfg = load_existing()
    cid = args.cid or cfg.get("client_id") or os.environ.get("BLOGGER_CLIENT_ID", "")
    csec = args.csec or cfg.get("client_secret") or os.environ.get("BLOGGER_CLIENT_SECRET", "")
    if not (cid and csec):
        print("❌ Client ID / Client Secret belum ada.")
        print("   Jalankan: python3 auth_blogger.py --client-id=... --client-secret=...")
        sys.exit(1)

    if args.code or cfg.get("refresh_token"):
        code = args.code
    else:
        params = {
            "client_id": cid,
            "redirect_uri": "http://127.0.0.1",
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/blogger",
            "access_type": "offline",
            "prompt": "consent",
        }
        url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        print("\n1) BUKA URL INI di browser (login dengan akun Google pemilik blog):\n")
        print("   " + url + "\n")
        print("2) Klik 'Allow' → browser akan mengarah ke http://127.0.0.1/?code=...")
        print("   (halaman 'tidak bisa dibuka' itu NORMAL)")
        print("3) Salin bagian code=... dari address bar, tempel di bawah:\n")
        code = input("   Kode OAuth: ").strip()

    if not code:
        print("❌ Kode kosong.")
        sys.exit(1)
    code = code.split("code=")[-1].split("&")[0].strip()

    r = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": "http://127.0.0.1",
        "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        print("❌ Gagal tukar kode → token:", r.text[:300])
        sys.exit(1)
    tok = r.json()
    refresh = tok.get("refresh_token")
    if not refresh:
        print("⚠️ Tidak ada refresh_token (mungkin sudah pernah authorize & tidak pakai "
              "prompt=consent). Isi refresh_token lama bila ada, atau ulangi dari URL di atas "
              "setelah menghapus akses di https://myaccount.google.com/permissions")
        refresh = cfg.get("refresh_token", "")

    cfg["client_id"] = cid
    cfg["client_secret"] = csec
    cfg["refresh_token"] = refresh

    # ambil daftar blog milik akun ini
    h = {"Authorization": "Bearer " + tok["access_token"]}
    rb = requests.get("https://www.googleapis.com/blogger/v3/users/self/blogs",
                      headers=h, timeout=30)
    blogs = rb.json().get("items", []) if rb.status_code == 200 else []
    if blogs:
        print("\n📚 Blog yang dimiliki akun ini:")
        for i, b in enumerate(blogs, 1):
            print(f"   {i}. {b.get('name')} — {b.get('url')} — id: {b.get('id')}")
        pick = input("   Pilih nomor blog untuk penyimpanan [1]: ").strip() or "1"
        try:
            cfg["blog_id"] = blogs[int(pick) - 1]["id"]
        except Exception:
            cfg["blog_id"] = blogs[0]["id"]
    else:
        print("⚠️ Tidak ditemukan blog. Buat blog dulu di https://www.blogger.com")
        if not cfg.get("blog_id"):
            cfg["blog_id"] = input("   Tempel Blog ID (angka panjang dari URL dashboard blog): ").strip()

    save(cfg)
    print("\n🎉 Selesai! Sekarang atur env/Secrets aplikasi:")
    for k in ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN", "BLOGGER_BLOG_ID"):
        print(f"   {k} = ...(lihat {OAUTH_FILE})")


if __name__ == "__main__":
    main()

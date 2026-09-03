#!/usr/bin/env python3
"""
blogger_sync.py — penyimpanan JSON persisten via Blogger DRAFT post (gratis!)
==============================================================================
Ide: data akun & cache disimpan sebagai **post DRAFT** (tidak pernah dipublish)
di blog Blogger milikmu. Draft hanya terlihat oleh pemilik blog (login Google),
jadi data cookies tetap privat. Kuota Blogger API v3: 10.000 request/hari per
project — TANPA batas tulis harian seperti Cloudflare KV.

Konsep:
  - 1 key = 1 draft post. Judul: "ytstore:<key>", label: "ytstore".
  - Isi post = BASE64 dari JSON (aman dari pemrosesan HTML Blogger).
  - Perubahan = UPDATE post yang sama (bukan bikin post baru tiap kali).

Cara pakai di aplikasi: cukup set env (Secrets Streamlit / env server):
  BLOGGER_CLIENT_ID / BLOGGER_CLIENT_SECRET / BLOGGER_REFRESH_TOKEN / BLOGGER_BLOG_ID
atau simpan semuanya di file BLOGGER_OAUTH_FILE (default /home/user/blogger_oauth.json).

Setup sekali (buat kredensial OAuth) → jalankan auth_blogger.py
"""
import base64
import json
import os
import re
import tempfile
import time

import requests

OAUTH_FILE = os.environ.get("BLOGGER_OAUTH_FILE", "/home/user/blogger_oauth.json")
TOKEN_CACHE = os.path.join(tempfile.gettempdir(), "yt_blogger_token.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://blogger.googleapis.com/v3/blogs"
SCOPE = "https://www.googleapis.com/auth/blogger"
LABEL = "ytstore"
TITLE_PREFIX = "ytstore:"


# ---------------- konfigurasi ----------------
def _cfg():
    """Gabungkan file oauth + env (env menang)."""
    data = {}
    if os.path.exists(OAUTH_FILE):
        try:
            data = json.load(open(OAUTH_FILE))
        except (json.JSONDecodeError, OSError):
            data = {}
    env = {
        "client_id": os.environ.get("BLOGGER_CLIENT_ID", ""),
        "client_secret": os.environ.get("BLOGGER_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("BLOGGER_REFRESH_TOKEN", ""),
        "blog_id": os.environ.get("BLOGGER_BLOG_ID", ""),
    }
    for k, v in env.items():
        if v:
            data[k] = v
    return data


def blogger_configured():
    c = _cfg()
    return bool(c.get("client_id") and c.get("client_secret")
                and c.get("refresh_token") and c.get("blog_id"))


# ---------------- token akses ----------------
def access_token():
    """Ambil access token (diperbarui otomatis, di-cache di /tmp)."""
    c = _cfg()
    if not (c.get("client_id") and c.get("client_secret") and c.get("refresh_token")):
        raise RuntimeError("Kredensial Blogger belum lengkap (client_id/client_secret/refresh_token).")
    cache = None
    if os.path.exists(TOKEN_CACHE):
        try:
            cache = json.load(open(TOKEN_CACHE))
        except (json.JSONDecodeError, OSError):
            cache = None
    if cache and cache.get("token") and cache.get("exp") and time.time() < cache["exp"] - 60:
        return cache["token"]
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
    }, timeout=30)
    if r.status_code != 200:
        raise RuntimeError("Gagal refresh token Google: %s" % r.text[:200])
    d = r.json()
    tok = d.get("access_token")
    exp = time.time() + int(d.get("expires_in", 3600))
    try:
        json.dump({"token": tok, "exp": exp}, open(TOKEN_CACHE, "w"))
    except OSError:
        pass
    return tok


def _blog_id():
    bid = _cfg().get("blog_id")
    if not bid:
        raise RuntimeError("blog_id Blogger belum diatur (BLOGGER_BLOG_ID atau file oauth).")
    return str(bid)


def _headers():
    return {"Authorization": "Bearer " + access_token()}


# ---------------- baca/tulis ----------------
def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _b64enc(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _b64dec(s):
    raw = base64.b64decode(_strip_tags(s).strip())
    return json.loads(raw.decode("utf-8"))


def _list_posts(status="DRAFT"):
    r = requests.get(f"{API}/{_blog_id()}/posts",
                     params={"status": status, "fetchBodies": "true",
                             "maxResults": "150"},
                     headers=_headers(), timeout=30)
    if r.status_code != 200:
        raise RuntimeError("Blogger posts.list gagal: %s" % r.text[:200])
    return r.json().get("items", [])


def _list_drafts():
    return _list_posts("DRAFT")


def _find_post(key):
    want = TITLE_PREFIX + key
    for p in _list_drafts():
        if p.get("title") == want:
            return p
    return None


def read(key):
    """Baca JSON dari draft post. None bila tidak ada / belum terkonfigurasi."""
    if not blogger_configured():
        return None
    try:
        p = _find_post(key)
        if not p:
            return None
        return _b64dec(p.get("content", ""))
    except Exception:
        return None


def write(key, value):
    """Simpan JSON sebagai draft post (buat baru / update yang ada).
    PENTING: draft dibuat via query param isDraft=true — body 'status' diabaikan
    API. Setelah menulis, status diverifikasi; bila tetap LIVE (bug/param
    diabaikan), post langsung DIHAPUS agar data tidak pernah terbit publik."""
    if not blogger_configured():
        return False
    payload = _b64enc(value)
    title = TITLE_PREFIX + key
    post = _find_post(key)
    try:
        if post:
            r = requests.put(f"{API}/{_blog_id()}/posts/{post['id']}",
                             params={"isDraft": "true"},
                             json={"title": title, "content": payload,
                                   "labels": [LABEL]},
                             headers=_headers(), timeout=60)
        else:
            r = requests.post(f"{API}/{_blog_id()}/posts",
                              params={"isDraft": "true"},
                              json={"title": title, "content": payload,
                                    "labels": [LABEL]},
                              headers=_headers(), timeout=60)
        ok = r.status_code in (200, 201)
        if not ok:
            raise RuntimeError("Blogger post gagal: %s" % r.text[:200])
        # verifikasi benar-benar DRAFT (status otoritatif = field di body respons;
        # GET post tidak mengembalikan status, jadi jangan pakai itu)
        body = r.json()
        pid = body.get("id")
        st = body.get("status")
        if st == "LIVE" or (st is None and _pid_in_list(pid, "DRAFT") is False):
            # data bocor ke publik → hapus segera
            requests.delete(f"{API}/{_blog_id()}/posts/{pid}", headers=_headers(), timeout=30)
            raise RuntimeError("Post yang dibuat ternyata LIVE & telah dihapus demi privasi. "
                               "Periksa parameter isDraft.")
        return True
    except Exception:
        return False


def _pid_in_list(pid, status):
    """True bila pid ada di daftar post ber-status tsb; None bila tidak bisa dicek."""
    try:
        for p in _list_posts(status):
            if p.get("id") == pid:
                return True
        return False
    except Exception:
        return None  # tidak bisa dipastikan → jangan hapus (hindari false-positive)


def status():
    """'ok' bila posts.list berhasil (token valid + blog id benar)."""
    if not blogger_configured():
        return "off"
    try:
        _list_drafts()
        return "ok"
    except Exception:
        return "error"

#!/usr/bin/env python3
"""
streamlit_app.py — YT Grabber versi Streamlit (untuk Streamlit Cloud)
=====================================================================
Cara deploy di Streamlit Cloud (share.streamlit.io):
1. Push ke GitHub: streamlit_app.py, yt_tool.py, requirements.txt, .streamlit/config.toml
2. New app → pilih repo → Main file: streamlit_app.py → Deploy
3. Settings → Secrets → isi:
     febspot_cookies = [ { "domain": ".febspot.com", "name": "kt_member", "value": "...", ... }, ... ]
     febspot_channel = "60404"
   (salin dari file febspot_cookies.json / extension browser)

Catatan: area kerja otomatis ke /tmp bersifat sementara & dibersihkan tiap job.
"""
import base64
import json
import os
import tempfile

import requests

# Di Streamlit Cloud, /home/user bersifat READ-ONLY (bahkan tidak ada) — semua
# file kerja & akun otomatis dialihkan ke /tmp (writable). Untuk permanen di cloud
# gunakan Secrets (febspot_cookies / febspot_accounts). Di self-host pakai workspace.
if os.path.isdir("/home/user") and os.access("/home/user", os.W_OK):
    os.environ.setdefault("YT_WORK_DIR", "/home/user/.ytwork")
    os.environ.setdefault("FEBSPOT_ACCOUNTS_FILE", "/home/user/febspot_accounts.json")
else:
    os.environ.setdefault("YT_WORK_DIR", os.path.join(tempfile.gettempdir(), "ytwork"))
    os.environ.setdefault("FEBSPOT_ACCOUNTS_FILE",
                          os.path.join(tempfile.gettempdir(), "febspot_accounts.json"))

import streamlit as st

st.set_page_config(page_title="YT Grabber", page_icon="🎬", layout="wide")

# placeholder kalau thumbnail YouTube gagal dimuat (kotak gelap + tombol play)
THUMB_PLACEHOLDER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAUAAAAC0CAIAAABqhmJGAAACyUlEQVR4nO3asU0dYRBG0cWiBQckDpArcCVEVEA57sEh7ZFYTp2B9EAEJPvuzDkVfMnVSLv/zY+fvw6g6dvZA4CvEzCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwgQMYQKGMAFDmIAhTMAQJmAIEzCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwgQMYQKGMAFDmIAhTMAQJmAIEzCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwgQc8PD4dPYErpSAGx4en2TMewIukTEXBNwjY14JuErGHAKuk/FyAp5Aw2sJeAineCcBjyLjbQQ8kIz3EPBYMt5AwMPJeDYBryDjqQS8iIznEfA6Mp5EwEvJeAYBrybjOgHjJWaYgDkOpzhLwLyRcY6AuSTjEAHzMRknCJjPyPjK3f77+3L2Bq7X85/fZ0/gM7dnD+BKSTdBwFySboiAeSPdHAFzHNLN8hUa9Ya5wKtJt07AS0l3BgGvI91JBLyIdOcR8ArSnUrAw0l3NgGPJd0NBDyQdPcQ8CjS3UbAQ0h3J08pJ1DvWi5wm3SXE3CVdDkEXCRdXgm4RLpcEHCDdPnQzfe7+7M3AF/kNxKECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwgQMYQKGMAFDmIAhTMAQJmAIEzCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwgQMYQKGMAFDmIAhTMAQJmAIEzCECRjCBAxhAoYwAUOYgCFMwBAmYAgTMIQJGMIEDGEChjABQ5iAIUzAECZgCBMwhAkYwv4DaZpZ6dgrar8AAAAASUVORK5CYII="
)


@st.cache_data(ttl=3600, show_spinner=False)
def thumb_for(vid):
    """Ambil thumbnail YouTube; kalau gagal, pakai placeholder."""
    try:
        r = requests.get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", timeout=4)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return r.content
    except Exception:
        pass
    return THUMB_PLACEHOLDER

# --- Secrets → env (dibaca yt_tool saat upload) ---
try:
    # Opsi 1 (dianjurkan, permanen): seluruh file akun (multi-akun) via Secrets
    if "febspot_accounts" in st.secrets:
        v = st.secrets["febspot_accounts"]
        os.environ["FEBSPOT_ACCOUNTS_SEED"] = v if isinstance(v, str) else json.dumps(v)
    # Opsi 2: akun tunggal — cookies + channel (otomatis jadi akun "default")
    if "febspot_cookies" in st.secrets:
        v = st.secrets["febspot_cookies"]
        os.environ["FEBSPOT_COOKIES"] = v if isinstance(v, str) else json.dumps(v)
    if "febspot_channel" in st.secrets:
        os.environ["FEBSPOT_CHANNEL"] = str(st.secrets["febspot_channel"])
    # Opsi 3 (paling kuat): penyimpanan persisten via Cloudflare Worker + KV
    if "febspot_sync_url" in st.secrets:
        os.environ["FEBSPOT_SYNC_URL"] = str(st.secrets["febspot_sync_url"]).strip()
    if "febspot_sync_token" in st.secrets:
        os.environ["FEBSPOT_SYNC_TOKEN"] = str(st.secrets["febspot_sync_token"]).strip()
    # Opsi 4: penyimpanan persisten via Blogger DRAFT (tanpa batas tulis harian)
    for k in ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET",
              "BLOGGER_REFRESH_TOKEN", "BLOGGER_BLOG_ID"):
        if k.lower() in st.secrets:
            os.environ[k] = str(st.secrets[k.lower()]).strip()
    # Opsi 5: relay unduhan via Cloudflare Worker (bypass 403 YouTube dari IP hosting)
    if "yt_fetch_proxy" in st.secrets:
        os.environ["YT_FETCH_PROXY"] = str(st.secrets["yt_fetch_proxy"]).strip()
    if "yt_force_proxy" in st.secrets and str(st.secrets["yt_force_proxy"]).strip() in ("1", "true", "True"):
        os.environ["YT_FORCE_PROXY"] = "1"
except Exception:
    pass

import yt_tool
from yt_tool import (fmt_dur, fmt_num, fmt_size, grab_keyword, grab_video,
                     search_videos, video_info)

TARGETS = {"febspot": "📺 febspot (akunmu)", "both": "🔀 febspot + gofile.io",
           "gofile": "🎬 gofile.io"}


# ---------------- helper ----------------
def run_grab(value, mode, fmt, target, account_id=None):
    """Jalankan grab dengan log real-time di st.status."""
    label = "🤖 Mesin bekerja" if mode == "keyword" else "⬇️ Mengambil video"
    with st.status(f"{label} — {value[:70]}", expanded=True) as status:
        def log(line):
            st.write(line)
        try:
            res = (grab_keyword(value, fmt, log=log, target=target, account_id=account_id)
                   if mode == "keyword" else grab_video(value, fmt, log=log, target=target,
                                                        account_id=account_id))
            status.update(label="✅ Selesai", state="complete", expanded=False)
            return res
        except Exception as e:
            status.update(label="❌ Gagal", state="error")
            st.error(str(e))
            return None


def show_result(res):
    st.subheader("✅ Hasil terakhir")
    for kind, url in res["links"]:
        c1, c2 = st.columns([3, 1])
        c1.write(f"**{kind}**")
        c2.link_button("🔗 Buka", url, use_container_width=True)
        st.code(url, language=None)
    st.caption(f"📦 {res['file']} · {fmt_size(res['size'])} · "
               f"👁️ {fmt_num(res['info'].get('view_count'))} · "
               f"🏷️ {len(res['info'].get('tags') or [])} tags")


# ---------------- sidebar ----------------
with st.sidebar:
    st.header("⚙️ Pengaturan")
    fmt = st.radio("Format", ["mp4", "mp3"], horizontal=True,
                   format_func=lambda x: "🎥 Video (MP4)" if x == "mp4" else "🎵 Audio (MP3)")
    target = st.selectbox("Tujuan upload", list(TARGETS.keys()),
                          format_func=lambda k: TARGETS[k])
    st.divider()
    st.subheader("👥 Akun Febspot")
    msg = st.session_state.pop("acc_msg", None)
    if msg:
        st.caption(msg)
    with st.expander("➕ Tambah akun (tempel cookies JSON)"):
        label_new = st.text_input("Label akun", placeholder="mis: Akun 1", key="acc_label_new")
        cookies_new = st.text_area("Cookies (JSON dari extension browser)", height=110,
                                   key="acc_cookies_new",
                                   placeholder='[{"domain":".febspot.com","name":"kt_member","value":"..."}, ...]')
        if st.button("Tambahkan & jadikan aktif", key="acc_add_btn", use_container_width=True):
            try:
                a = yt_tool.add_account(label_new or "Akun Baru", cookies_new)
                st.session_state["acc_msg"] = f"✅ {a['label']} ditambahkan & menjadi akun aktif"
                st.rerun()
            except Exception as e:
                st.session_state["acc_msg"] = f"❌ {e}"
                st.rerun()
    accs_sys = yt_tool.load_accounts()
    ids = list(accs_sys["accounts"].keys())
    if ids:
        active = accs_sys.get("active") or ids[0]
        sel = st.selectbox("Akun untuk upload", ids, key="acc_sel",
                           index=ids.index(active) if active in ids else 0,
                           format_func=lambda i: accs_sys["accounts"][i]["label"])
        if sel != active:
            yt_tool.set_active_account(sel)
            st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("🔄 Perbarui data", key="acc_refresh", use_container_width=True):
            try:
                snap = yt_tool.account_info(sel)
                st.session_state["acc_msg"] = f"✅ Data {snap['name']} diperbarui"
                st.session_state["acc_snap"] = snap
            except Exception as e:
                st.session_state["acc_msg"] = f"❌ {e}"
            st.rerun()
        if c2.button("🗑 Hapus akun ini", key="acc_del", use_container_width=True):
            yt_tool.remove_account(sel)
            st.session_state["acc_msg"] = "🗑 Akun dihapus"
            st.rerun()
    else:
        st.caption("Belum ada akun. Tempel cookies dari extension browser (EditThisCookie / Cookie-Editor) "
                   "setelah login ke febspot.com, lalu klik Tambahkan.")
    _sync = yt_tool.sync_status()
    _back = yt_tool.sync_backend()
    if _sync == "ok" and _back == "blogger":
        st.caption("🟢 **Sinkron Blogger AKTIF** — akun & cache tersimpan sebagai "
                   "**draft di blogmu** (tidak pernah dipublish; tahan restart, ganti browser, "
                   "tanpa batas tulis harian). File lokal: `%s`"
                   % os.environ.get("FEBSPOT_ACCOUNTS_FILE", "(file akun)"))
    elif _sync == "ok":
        st.caption("🟢 **Sinkron cloud AKTIF** — akun & cache tersimpan di "
                   "Cloudflare Worker + KV (tahan restart, ganti browser). "
                   "File lokal: `%s`" % os.environ.get("FEBSPOT_ACCOUNTS_FILE", "(file akun)"))
    elif _sync == "error":
        st.caption("🔴 Sinkron cloud **error** — cek Secrets backend (`blogger_*` atau "
                   "`febspot_sync_url/token`). Data tetap dipakai dari file lokal: `%s`"
                   % os.environ.get("FEBSPOT_ACCOUNTS_FILE", "(file akun)"))
    else:
        st.caption("💾 Akun disimpan di `%s` — karena cloud Streamlit memakai /tmp (hapus saat app "
                   "tidur), atur Secrets **`blogger_*`** (Blogger draft, paling gampang) atau "
                   "`febspot_sync_url` + `febspot_sync_token` (Cloudflare Worker) agar **permanen**."
                   % os.environ.get("FEBSPOT_ACCOUNTS_FILE", "(file akun)"))
    if st.button("🧹 Bersihkan riwayat", use_container_width=True):
        for k in ("results", "history", "last_result", "show_info", "grab"):
            st.session_state.pop(k, None)
        st.rerun()

# ---------------- header ----------------
st.title("🎬 YT Grabber")
st.caption("Cari video YouTube → unduh → upload otomatis ke febspot / gofile.io — "
           "file lokal langsung dihapus. Data akun & cookies tersimpan permanen di server.")

# ---------------- dashboard akun aktif ----------------
try:
    _acc = yt_tool.get_account()
    _snap = _acc.get("snapshot")
    if _snap:
        m = st.columns(6)
        m[0].metric("👤 Akun", _snap.get("name") or "-")
        m[1].metric("🎞️ Video", _snap.get("videos") or 0)
        m[2].metric("👁️ Total Views", fmt_num(_snap.get("total_views") or 0))
        m[3].metric("👥 Subscriber", _snap.get("subscribers") or 0)
        m[4].metric("💰 Saldo", f"${_snap.get('balance') or '0'}")
        m[5].metric("Status", _snap.get("status") or "-")
        vids = _snap.get("video_list") or []
        if vids:
            with st.expander(f"📼 Daftar video ({len(vids)})", expanded=False):
                rows = [{"Judul": v.get("title"), "Views": v.get("views"),
                         "Link": v.get("url") or "(menunggu review)"} for v in vids]
                st.dataframe(rows, use_container_width=True, hide_index=True)
        st.divider()
except Exception:
    pass

# ---------------- hasil terakhir ----------------
last = st.session_state.get("last_result")
if last:
    show_result(last)
    st.divider()

# ---------------- pencarian ----------------
with st.form("form_search"):
    c1, c2 = st.columns([4, 1])
    kw = c1.text_input("Kata kunci",
                       placeholder="mis: lofi study, tutorial python, funny cat...",
                       label_visibility="collapsed")
    count = c2.selectbox("Jumlah hasil", [5, 10, 20, 30], index=1,
                         help="Berapa banyak hasil yang ditampilkan")
    submitted = st.form_submit_button("🔎 Cari", type="primary", use_container_width=True)

auto_btn = st.button("🤖 Biar Mesin Saja", use_container_width=True,
                     help="Mesin otomatis cari → pilih terpendek → download → upload")

if auto_btn and kw.strip():
    res = run_grab(kw.strip(), "keyword", fmt, target)
    if res:
        st.session_state["last_result"] = res
        st.session_state.setdefault("history", []).append(res)
        st.rerun()

if submitted and kw.strip():
    with st.status(f'🔎 Mencari "{kw.strip()}" ...', expanded=True) as status:
        try:
            res = search_videos(kw.strip(), count, log=lambda line: st.write(line))
            status.update(label=f"✅ {len(res)} hasil", state="complete", expanded=False)
            st.session_state["results"] = res
        except Exception as e:
            status.update(label="❌ Gagal", state="error")
            st.error(str(e))
            st.session_state["results"] = []

# ---------------- daftar hasil ----------------
results = st.session_state.get("results", [])
if results:
    st.subheader(f"Hasil Pencarian ({len(results)})")
    for r in results:
        with st.container(border=True):
            # thumbnail (kiri) + info (tengah) + tombol (kanan)
            tcol, mcol, icol, gcol = st.columns([1.4, 3.6, 1, 1], vertical_alignment="center")
            with tcol:
                try:
                    st.image(thumb_for(r["id"]), width=180)
                except Exception:
                    pass
            mcol.markdown(f"**{r['title']}**")
            mcol.caption(f"👁️ {fmt_num(r['views'])} · ⏱️ {fmt_dur(r['dur'])} · 📺 {r['ch']}")
            want_info = icol.button("📋 Info", key=f"info_{r['id']}", use_container_width=True)
            want_grab = gcol.button("⬇️ Ambil", key=f"grab_{r['id']}", use_container_width=True,
                                    type="primary")
            if want_info:
                st.session_state["show_info"] = r["id"]
            if want_grab:
                st.session_state["grab"] = {"value": r["id"], "mode": "video"}

    # proses aksi setelah render (satu rerun)
    show_id = st.session_state.pop("show_info", None)
    if show_id:
        d = video_info(show_id)
        with st.expander(f"📋 {show_id} — data lengkap", expanded=True):
            kv = {
                "Judul": d.get("title"), "Channel": d.get("channel"),
                "Upload": d.get("upload_date"), "Views": fmt_num(d.get("view_count")),
                "Likes": fmt_num(d.get("like_count")), "Durasi": fmt_dur(d.get("duration")),
                "Kategori": ", ".join(d.get("categories") or []),
            }
            st.markdown("  \n".join(f"**{k}:** {v}" for k, v in kv.items()))
            tags = d.get("tags") or []
            if tags:
                st.markdown("**Tags:** " + " ".join(f"`#{t}`" for t in tags[:40]))
            st.markdown("**Deskripsi:**")
            st.code((d.get("description") or "(kosong)")[:2000], language="markdown")

    grab = st.session_state.pop("grab", None)
    if grab:
        res = run_grab(grab["value"], "video", fmt, target)
        if res:
            st.session_state["last_result"] = res
            st.session_state.setdefault("history", []).append(res)

# ---------------- riwayat ----------------
hist = st.session_state.get("history", [])
if hist:
    with st.expander(f"🕘 Riwayat ({len(hist)})"):
        for h in reversed(hist):
            st.write(f"**{h['file']}** · {fmt_size(h['size'])}")
            for kind, url in h["links"]:
                st.markdown(f"- {kind}: [{url}]({url})")

st.divider()
st.caption("Untuk edukasi — pastikan kamu berhak mengunduh kontennya. "
           "Video yang di-upload ke kanalmu harus lolos review febspot.")

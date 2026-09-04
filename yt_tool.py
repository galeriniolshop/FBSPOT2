#!/usr/bin/env python3
"""
yt_tool.py — Modul inti: pencarian, info, download, upload febspot + gofile.io
=============================================================================
Fungsi inti tidak memanggil sys.exit (menaikkan RuntimeError) sehingga
bisa dipakai oleh CLI maupun web app (app.py).

Tujuan upload: "febspot" (kanal akunmu), "gofile", atau "both".

CLI:
  python3 yt_tool.py search "<kata kunci>" [jumlah]
  python3 yt_tool.py info <video_id|url>
  python3 yt_tool.py grab <video_id|url|"kata kunci"> [mp4|mp3] [febspot|gofile|both]
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from urllib.parse import urljoin

from yt_dlp import YoutubeDL

YP = "yt-dlp"
WORK = os.environ.get("YT_WORK_DIR", "/home/user/.ytwork")   # bisa di-override (mis. /tmp di cloud)
os.makedirs(WORK, exist_ok=True)

# ---------- konfigurasi febspot ----------
FEBSPOT_COOKIES = os.environ.get("FEBSPOT_COOKIES", "/home/user/febspot_cookies.json")
ACCOUNTS_FILE = os.environ.get("FEBSPOT_ACCOUNTS_FILE", "/home/user/febspot_accounts.json")
FEBSPOT_CHANNEL = os.environ.get("FEBSPOT_CHANNEL", "60404")   # dvd_id kanal (default)
FEBSPOT_BASE = "https://www.febspot.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------- konfigurasi SafelinkU (subscription link) ----------
SAFELINKU_TOKEN = os.environ.get("SAFELINKU_TOKEN", "")
SAFELINKU_CONFIG = os.environ.get("SAFELINKU_CONFIG", "/home/user/safelinku.json")
SAFELINKU_CACHE = os.environ.get("SAFELINKU_CACHE", "/home/user/safelinku_cache.json")
SAFELINKU_API = "https://safelinku.com/api/v1/links"
SUBSCRIBE_URL = os.environ.get(
    "SUBSCRIBE_URL", "https://www.youtube.com/channel/UCX-w2r2NSbyjd9pjHKcEJzg")
SUBSCRIBE_ALIAS = os.environ.get("SUBSCRIBE_ALIAS", "ytsubscribe")

# ---------- sinkronisasi cloud via Cloudflare Worker + KV ----------
# Isi FEBSPOT_SYNC_URL (URL worker, mis. https://ytstore.xxx.workers.dev) dan
# FEBSPOT_SYNC_TOKEN (sama dengan secret API_TOKEN di worker) → semua data
# akun & cache SafelinkU tersimpan di KV cloud: tahan restart container,
# ganti browser, dan bisa ditulis dari UI. Kosongkan untuk mode file-lokal saja.
FEBSPOT_SYNC_URL = os.environ.get("FEBSPOT_SYNC_URL", "")
FEBSPOT_SYNC_TOKEN = os.environ.get("FEBSPOT_SYNC_TOKEN", "")

# ---------- relay unduhan via Cloudflare Worker (fragmen YouTube 403 dari IP cloud) ----------
# Isi YT_FETCH_PROXY (mis. https://ytfetch.xxx.workers.dev) → bila strategi unduh
# normal kena 403, mesin otomatis menarik fragmen media lewat worker (IP Cloudflare
# umumnya tidak diblokir YouTube). Bisa dipaksa lebih dulu dengan YT_FORCE_PROXY=1.
FETCH_PROXY = os.environ.get("YT_FETCH_PROXY", "").rstrip("/")
FORCE_PROXY = os.environ.get("YT_FORCE_PROXY", "") == "1"


def find_ffmpeg():
    for p in ("/home/user/bin/ffmpeg", "/tmp/ffmpeg"):
        if os.path.exists(p):
            return p
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()


# ---------------- util ----------------
def run(args, log=None, stream=False, want=None):
    """Jalankan yt-dlp. stream=True → baris stdout/stderr dialirkan ke log(),
    difilter oleh want(line) bila diberikan."""
    cmd = [YP] + args
    if stream:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            line = line.rstrip()
            if line and log and (want is None or want(line)):
                log(line)
        rc = p.wait()
        if rc != 0:
            raise RuntimeError("yt-dlp gagal (kode %s)" % rc)
        return ""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("yt-dlp error: " + r.stderr[-600:])
    return r.stdout


def fmt_num(n):
    try:
        n = int(n)
        for unit, div in (("M", 1_000_000_000), ("jt", 1_000_000), ("rb", 1_000)):
            if n >= div:
                return f"{n/div:.1f}{unit}"
        return str(n)
    except (TypeError, ValueError):
        return "-"


def fmt_dur(sec):
    try:
        sec = int(sec)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    except (TypeError, ValueError):
        return "-"


def fmt_size(nbytes):
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024


def cleanup_work(log=None):
    """Hapus SEMUA isi folder kerja (video, audio, temp) — sebelum & sesudah job."""
    try:
        for f in os.listdir(WORK):
            p = os.path.join(WORK, f)
            try:
                os.remove(p)
            except OSError:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def find_id(text):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text.strip()):
        return text.strip()
    return None


# ---------------- inti ----------------
def search_videos(keyword, count=10, log=None):
    if log:
        log(f'🔎 Mencari: "{keyword}" ({count} hasil) ...')
    out = run(["--flat-playlist", "--no-warnings", "--print",
               "%(id)s\t%(title)s\t%(duration)s\t%(view_count)s\t%(uploader)s",
               f"ytsearch{count}:{keyword}"], log=log)
    results = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        vid, title, dur, views, ch = parts[:5]
        results.append({"id": vid, "title": title, "dur": dur, "views": views, "ch": ch})
        if log:
            log(f'├─ {vid} | {fmt_num(views)} views | {fmt_dur(dur)} | {ch}')
            log(f'│  {title}')
    if log:
        log(f"└─ {len(results)} hasil ditemukan.")
    return results


def video_info(video, log=None):
    target = find_id(video) or video
    if log:
        log(f"📋 Mengambil data lengkap: {target} ...")
    out = run(["--no-warnings", "--skip-download", "--dump-single-json",
               "--no-check-certificate", target], log=log)
    return json.loads(out)


def build_meta(d):
    tags = "\n".join(f"- {t}" for t in (d.get("tags") or []))
    return (f"JUDUL     : {d.get('title')}\n"
            f"URL       : https://youtu.be/{d.get('id')}\n"
            f"CHANNEL   : {d.get('channel')}\n"
            f"UPLOAD    : {d.get('upload_date')}\n"
            f"VIEWS     : {d.get('view_count')}\n"
            f"LIKES     : {d.get('like_count')}\n"
            f"DURASI    : {fmt_dur(d.get('duration'))}\n"
            f"KATEGORI  : {d.get('categories')}\n"
            f"TAGS ({len(d.get('tags') or [])}):\n{tags}\n\n"
            f"DESKRIPSI:\n{d.get('description') or '(kosong)'}\n")


def download_video(video, fmt="mp4", log=None):
    target = find_id(video) or video
    cleanup_work(log)                       # buang sisa job gagal sebelumnya
    if log:
        log(f"⬇️  Mengunduh: {target} ({fmt}) ...")
    base = ["--no-warnings", "--newline", "--progress-delta", "2",
            "--retries", "3", "--fragment-retries", "3",
            "-o", f"{WORK}/%(title)s.%(ext)s"]
    if FFMPEG:
        base += ["--ffmpeg-location", FFMPEG]
    # cookies YouTube opsional (env YT_COOKIES_FILE) — membantu saat IP diblokir
    ck = os.environ.get("YT_COOKIES_FILE", "")
    if ck and os.path.exists(ck):
        base += ["--cookies", ck]

    def want(line):
        # buang baris progress berulang (999 baris %), simpan yang penting & garis 100%
        if re.match(r"^\[download\]\s+[\d.]+%\s+of\s+", line) \
                and not re.match(r"^\[download\]\s+100", line):
            return False
        return True

    def once(extra, label=None):
        if label and log:
            log("🧪 " + label + " ...")
        run(base + extra + [target], log=log, stream=True, want=want)

    if fmt == "mp3":
        try:
            once(["-x", "--audio-format", "mp3", "--audio-quality", "0",
                  "-f", "ba[protocol^=https]/ba/bestaudio/b"], "MP3: DASH https")
        except RuntimeError:
            cleanup_work(log)
            once(["-x", "--audio-format", "mp3", "--audio-quality", "0"],
                 "MP3: format default")
    else:
        # STRATEGI BERLAPIS — YouTube sering membalas "403 Forbidden" untuk fragmen
        # HLS (m3u8) dari IP hosting cloud (Streamlit/GCP, VPS). Urutan strategi
        # berdasarkan uji empiris; yang pertama berhasil dipakai:
        #  1) format default yt-dlp            (paling kompatibel)
        #  2) DASH https saja                  (memotong jalur HLS → lolos blokir 403)
        #  3) client android                   (jalur penandatanganan beda)
        #  4) client web_embedded, format 18   (progresif, tanpa merge)
        #  5) client android_vr, format 18     (cadangan terakhir)
        plans = [
            ("format default",
             ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
              "--merge-output-format", "mp4"]),
            ("DASH https (hindari HLS)",
             ["-f", "bv*[protocol^=https]+ba[protocol^=https]/b[protocol^=https]/b",
              "--merge-output-format", "mp4"]),
            ("client android",
             ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
              "--merge-output-format", "mp4",
              "--extractor-args", "youtube:player_client=android"]),
            ("client web_embedded (format 18)",
             ["-f", "18/22/b",
              "--extractor-args", "youtube:player_client=web_embedded"]),
            ("client android_vr (format 18)",
             ["-f", "18/b",
              "--extractor-args", "youtube:player_client=android_vr"]),
        ]
        errs = []
        if FORCE_PROXY and proxy_enabled():
            # mode paksa: relay dulu (IP tertentu langsung diblokir)
            return download_via_relay(target, log=log)
        for i, (label, extra) in enumerate(plans):
            try:
                once(extra, label)
                break
            except RuntimeError as e:
                errs.append(f"{i+1}. {label}: {e}")
                cleanup_work(log)
        else:
            # strategi terakhir: relay lewat Cloudflare Worker (IP non-blokir)
            if proxy_enabled() and log:
                log("☁️ Semua strategi gagal — mencoba RELAY CLOUDFLARE ...")
            if proxy_enabled():
                return download_via_relay(target, log=log)
            raise RuntimeError(
                "Semua strategi download gagal (kemungkinan IP hosting diblokir YouTube).\n- "
                + "\n- ".join(errs[-5:])
                + "\n💡 Atur YT_FETCH_PROXY (Cloudflare Worker relay) untuk unduh via IP Cloudflare.")

    files = [os.path.join(WORK, f) for f in os.listdir(WORK)
             if f.endswith((".mp4", ".mp3", ".webm", ".m4a", ".mkv"))]
    if not files:
        raise RuntimeError("File hasil download tidak ditemukan.")
    return max(files, key=os.path.getsize)


# ---------------- UNDUH via RELAY CLOUDFLARE (fallback 403) ----------------
def proxy_enabled():
    return bool(FETCH_PROXY)


def _proxy_get(url, log=None, headers=None, timeout=120):
    """Ambil byte via worker relay: GET {PROXY}/?url=<encoded>."""
    if not FETCH_PROXY:
        raise RuntimeError("YT_FETCH_PROXY belum diatur.")
    r = requests.get(FETCH_PROXY + "/", params={"url": url},
                     headers=headers or {}, timeout=timeout, stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"Relay {r.status_code} untuk {url[:80]}")
    data = r.content
    if log:
        log(f"   ☁️ relay: {len(data)//1024} KB dari {url[:60]}...")
    return data


def _proxy_stream(url, path, log=None, headers=None, timeout=600):
    """Unduh (streaming) satu URL melalui relay ke file."""
    if not FETCH_PROXY:
        raise RuntimeError("YT_FETCH_PROXY belum diatur.")
    r = requests.get(FETCH_PROXY + "/", params={"url": url},
                     headers=headers or {}, timeout=timeout, stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"Relay {r.status_code} untuk {url[:80]}")
    total = 0
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            total += len(chunk)
    if log:
        log(f"   ☁️ relay: {total//1024} KB → {os.path.basename(path)}")
    return total


def _parse_m3u8(text, base_url):
    """Ambil EXT-X-MAP (init) + daftar URL segmen dari playlist HLS."""
    init = None
    segs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                init = urljoin(base_url, m.group(1))
        elif line.startswith("#"):
            continue
        else:
            segs.append(urljoin(base_url, line))
    return init, segs


def _pull_format(fmt, label, log=None):
    """Unduh satu format (HLS playlist / DASH fragments / URL tunggal) via relay ke file."""
    url = fmt.get("url") or fmt.get("manifest_url")
    if not url:
        raise RuntimeError("Format tidak punya URL.")
    frags = fmt.get("fragments") or []
    is_m3u8 = (fmt.get("protocol") in ("m3u8", "m3u8_native")
               or str(url).endswith(".m3u8"))
    out = os.path.join(WORK, f"relay_{label}.bin")
    with open(out, "wb") as f:
        if is_m3u8:
            if log:
                log(f"   ☁️ HLS: ambil manifest {url[:70]}...")
            manifest = _proxy_get(url, log=log).decode("utf-8", "replace")
            init, segs = _parse_m3u8(manifest, url)
            if not segs:
                raise RuntimeError("Playlist HLS tidak berisi segmen.")
            if log:
                log(f"   ☁️ HLS: {len(segs)} segmen" + (" (+init)" if init else ""))
            if init:
                f.write(_proxy_get(init, log=None))
            for i, s in enumerate(segs, 1):
                for attempt in range(3):
                    try:
                        data = _proxy_get(s, log=None)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                f.write(data)
                if log and (i % 20 == 0 or i == len(segs)):
                    log(f"   ☁️ HLS: {i}/{len(segs)} segmen...")
        else:
            # DASH fragments (Range) atau URL tunggal
            if frags:
                for i, fr in enumerate(frags, 1):
                    furl = fr.get("url") or url
                    headers = {}
                    if fr.get("range"):
                        headers["Range"] = f"bytes={fr['range']}"
                    f.write(_proxy_get(furl, log=None, headers=headers))
                    if log and i % 20 == 0:
                        log(f"   ☁️ DASH: {i}/{len(frags)} fragmen...")
            else:
                if log:
                    log(f"   ☁️ unduh URL tunggal {url[:70]}...")
                f.write(_proxy_get(url, log=log, timeout=600))
    return out, os.path.getsize(out)


def _relay_finish(vfile, afile, final_path, log=None):
    """Gabung video+audio hasil relay pakai ffmpeg (repair + merge ke mp4)."""
    cmd = [FFMPEG, "-y", "-i"]
    if vfile and afile:
        cmd += [vfile, "-i", afile]
    else:
        cmd += [vfile or afile]
    cmd += ["-c", "copy", "-movflags", "+faststart", final_path]
    if log:
        log("   🎞️ merge (ffmpeg) via relay...")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        # coba remux terpisah (mungkin konflik codec): fallback ke stream video saja
        if log:
            log("   ⚠️ merge gagal — pakai stream video saja.")
        p = subprocess.run([FFMPEG, "-y", "-i", vfile or afile,
                            "-c", "copy", "-movflags", "+faststart", final_path],
                           capture_output=True, text=True, timeout=1800)
        if p.returncode != 0:
            raise RuntimeError("ffmpeg gagal memproses hasil relay: " + (p.stderr or "")[-300:])
    return final_path


def pick_relay_formats(formats, max_height=1080):
    """Pilih format video & audio terbaik untuk jalur relay (HLS/DASH https)."""
    videos = [f for f in formats if f.get("vcodec") and f["vcodec"] != "none"
              and (f.get("protocol") in ("m3u8", "m3u8_native", "https", "http"))]
    audios = [f for f in formats if f.get("acodec") and f["acodec"] != "none"
              and (f.get("protocol") in ("m3u8", "m3u8_native", "https", "http"))]

    def score(f):
        h = f.get("height") or 0
        res_rank = -abs(min(h or 720, max_height) - 720)
        m3u8 = 50 if f.get("protocol") in ("m3u8", "m3u8_native") else 0
        avc = 30 if "avc1" in (f.get("vcodec") or "") else 0
        return (avc, m3u8, h, res_rank)

    v = sorted(videos, key=score, reverse=True)[0] if videos else None
    a = sorted(audios, key=score, reverse=True)[0] if audios else None
    return v, a


def download_via_relay(target, log=None):
    """Strategi terakhir: unduh video LEWAT Cloudflare Worker (IP non-blokir)."""
    if not FETCH_PROXY:
        raise RuntimeError("YT_FETCH_PROXY belum diatur (env/Secrets yt_fetch_proxy).")
    if log:
        log(f"☁️ Relay Cloudflare aktif — ekstrak format untuk {target} ...")
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "noplaylist": True}
    if FFMPEG:
        ydl_opts["ffmpeg_location"] = FFMPEG
    with YoutubeDL(ydl_opts) as y:
        info = y.extract_info(target, download=False)
    fmts = info.get("formats") or []
    v, a = pick_relay_formats(fmts)
    if not v:
        raise RuntimeError("Tidak ada format video yang bisa direlay.")
    if log:
        log(f"☁️ Format dipilih: video {v.get('format_id')} "
            f"({v.get('height')}p, {v.get('protocol')})"
            + (f" + audio {a.get('format_id')}" if a else ""))
    vfile, vsize = _pull_format(v, "v", log=log)
    afile = None
    if a:
        afile, asize = _pull_format(a, "a", log=log)
    final = os.path.join(WORK, f"relay_{fmt_slug(info.get('title','video'))}.mp4")
    if log:
        log(f"🎞️ Merapikan & menggabungkan via ffmpeg ...")
    _relay_finish(vfile, afile, final, log=log)
    for p in (vfile, afile):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    if log:
        log(f"✅ Relay selesai → {os.path.basename(final)} "
            f"({fmt_size(os.path.getsize(final))})")
    return final


def fmt_slug(text):
    s = re.sub(r"[^\w\s-]", "", text or "video")
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "video"


# ---------------- UPLOAD GOFILE ----------------
def gofile_upload(path, log=None):
    name = os.path.basename(path)
    size = os.path.getsize(path)
    if log:
        log(f"☁️  Upload ke gofile.io: {name} ({fmt_size(size)}) ...")
    server = requests.get("https://api.gofile.io/servers", timeout=30)\
                   .json()["data"]["servers"][0]["name"]
    try:
        from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
        m = MultipartEncoder(fields={"file": (name, open(path, "rb"),
                                              "application/octet-stream")})
        last = [-10]

        def cb(mon):
            pct = int(mon.bytes_read * 100 / mon.len) if mon.len else 100
            if log and pct >= last[0] + 10:
                log(f"☁️  ... {pct}% dari {fmt_size(mon.len)}")
                last[0] = pct

        data = MultipartEncoderMonitor(m, cb)
        r = requests.post(f"https://{server}.gofile.io/contents/uploadfile",
                          data=data, headers={"Content-Type": data.content_type},
                          timeout=None)
    except ImportError:
        with open(path, "rb") as f:
            r = requests.post(f"https://{server}.gofile.io/contents/uploadfile",
                              files={"file": (name, f)}, timeout=None)
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Upload gofile gagal: {data}")
    return data["data"]["downloadPage"], data["data"].get("code")


# ---------------- SINKRONISASI CLOUD (BLOGGER DRAFT atau CLOUDFLARE KV) ----------------
# Prioritas backend: Blogger (draft post, gratis, tanpa batas tulis harian)
# → Cloudflare Worker KV → file lokal saja. Pilih otomatis dari konfigurasi.
def _blogger():
    try:
        import blogger_sync
        return blogger_sync
    except Exception:
        return None


def sync_backend():
    """'blogger' | 'worker' | 'off' — backend penyimpanan cloud yang aktif."""
    b = _blogger()
    if b and b.blogger_configured():
        return "blogger"
    if FEBSPOT_SYNC_URL and FEBSPOT_SYNC_TOKEN:
        return "worker"
    return "off"


def sync_enabled():
    """True bila ada backend cloud (Blogger atau Worker) yang dikonfigurasi."""
    return sync_backend() != "off"


def sync_fetch(key="accounts"):
    """Ambil JSON dari backend cloud. None bila tidak ada / gagal / kosong."""
    backend = sync_backend()
    if backend == "blogger":
        try:
            return _blogger().read(key)
        except Exception:
            return None
    if backend == "worker":
        try:
            r = requests.get(FEBSPOT_SYNC_URL.rstrip("/") + "/kv/" + key,
                             headers={"X-Token": FEBSPOT_SYNC_TOKEN}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                data = d.get("data") if isinstance(d, dict) else None
                return data if data is not None else None
            return None
        except Exception:
            return None
    return None


def sync_push(key, value):
    """Simpan JSON ke backend cloud (best effort — tidak melempar error)."""
    backend = sync_backend()
    if backend == "blogger":
        try:
            return _blogger().write(key, value)
        except Exception:
            return False
    if backend == "worker":
        try:
            r = requests.put(FEBSPOT_SYNC_URL.rstrip("/") + "/kv/" + key,
                             json=value,
                             headers={"X-Token": FEBSPOT_SYNC_TOKEN,
                                      "Content-Type": "application/json"}, timeout=10)
            return r.status_code in (200, 201)
        except Exception:
            return False
    return False


# --- hemat kuota (relevan untuk Cloudflare KV = 1.000 tulis/hari; Blogger tidak
# butuh ini tapi tidak mengganggu) ---
# Push "pintar": diulang paling cepat tiap ttl_ok (6 jam) bila sukses,
# atau ttl_fail (10 menit) bila gagal. Perubahan nyata (tambah/hapus/ganti
# akun) tetap push langsung via save_accounts(). Marker disimpan di /tmp.
def _push_marker(key):
    return os.path.join(tempfile.gettempdir(), f"yt_sync_{key}.json")


def sync_push_smart(key, value, ttl_ok=6 * 3600, ttl_fail=600):
    """Push ke cloud hanya bila : (a) belum pernah sukses < 6 jam,
    atau (b) percobaan terakhir gagal & sudah > 10 menit."""
    if not sync_enabled():
        return False
    try:
        m = json.load(open(_push_marker(key)))
        age = time.time() - m.get("t", 0)
        if m.get("ok") and age < ttl_ok:
            return True                    # sudah tersinkron baru-baru ini
        if not m.get("ok") and age < ttl_fail:
            return False                   # gagal baru saja — jangan spam
    except (json.JSONDecodeError, OSError):
        pass
    ok = sync_push(key, value)
    try:
        json.dump({"t": time.time(), "ok": ok}, open(_push_marker(key), "w"))
    except OSError:
        pass
    return ok


def sync_status():
    """Ringkasan status sinkronisasi (untuk ditampilkan di UI)."""
    backend = sync_backend()
    if backend == "off":
        return "off"
    if backend == "blogger":
        try:
            return _blogger().status()
        except Exception:
            return "error"
    try:
        r = requests.get(FEBSPOT_SYNC_URL.rstrip("/") + "/ping",
                         headers={"X-Token": FEBSPOT_SYNC_TOKEN}, timeout=8)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, dict) and d.get("ok"):
                return "ok"
    except Exception:
        pass
    return "error"


# ---------------- SAFELINKU (short link subscription) ----------------
def _safelinku_token():
    tok = SAFELINKU_TOKEN or os.getenv("SAFELINKU_TOKEN", "")
    if not tok and os.path.exists(SAFELINKU_CONFIG):
        try:
            tok = json.load(open(SAFELINKU_CONFIG)).get("token", "")
        except (json.JSONDecodeError, OSError):
            tok = ""
    return tok


def _safelinku_cache():
    # prioritas: Worker KV (tahan restart container) → file lokal
    rem = sync_fetch("safelinku")
    if isinstance(rem, dict) and rem:
        return rem
    if os.path.exists(SAFELINKU_CACHE):
        try:
            cache = json.load(open(SAFELINKU_CACHE))
            if isinstance(cache, dict) and cache:
                sync_push_smart("safelinku", cache)   # bootstrap hemat kuota (maks 1×/6 jam)
            return cache
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _safelinku_save_cache(cache):
    try:
        d = os.path.dirname(SAFELINKU_CACHE) or "."
        os.makedirs(d, exist_ok=True)
        with open(SAFELINKU_CACHE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(SAFELINKU_CACHE + ".tmp", SAFELINKU_CACHE)
    except OSError:
        pass  # read-only (cloud) → tetap disimpan di Worker/pengganti
    sync_push("safelinku", cache)


def safelinku_shorten(url, alias=None, log=None):
    """Buat short link SafelinkU (dengan cache persisten). Balas URL pendek."""
    alias = alias or SUBSCRIBE_ALIAS
    cache = _safelinku_cache()
    key = f"{url}|{alias}"
    if key in cache:
        if log:
            log(f"🔗 Short link (cache): {cache[key]}")
        return cache[key]

    token = _safelinku_token()
    if not token:
        raise RuntimeError("Token SafelinkU belum diatur (env SAFELINKU_TOKEN atau "
                           f"file {SAFELINKU_CONFIG}).")
    body = {"url": url}
    if alias:
        body["alias"] = alias
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = requests.post(SAFELINKU_API, json=body, headers=headers, timeout=30)
    if r.status_code == 201:
        data = r.json()
        short = data.get("url") or data.get("short_url") or data.get("data", {}).get("url")
        if not short:
            raise RuntimeError("Respons SafelinkU tidak berisi URL: %s" % str(data)[:200])
        cache[key] = short
        cache[url] = short          # juga cache per URL (tanpa alias)
        _safelinku_save_cache(cache)
        if log:
            log(f"🔗 Short link dibuat: {short}")
        return short
    if r.status_code == 400 and alias:
        # alias tidak boleh dipakai / tidak valid → coba tanpa alias
        if log:
            log("⚠️ Alias ditolak — membuat short link tanpa alias...")
        return safelinku_shorten(url, alias=None, log=log)
    if r.status_code == 401:
        raise RuntimeError("SafelinkU: token tidak valid (401).")
    if r.status_code == 429:
        raise RuntimeError("SafelinkU: rate limit (60/menit). Tunggu lalu coba lagi.")
    raise RuntimeError(f"SafelinkU: HTTP {r.status_code} — {r.text[:200]}")


def subscribe_link(log=None):
    """Short link untuk tombol subscribe (fallback: URL asli bila SafelinkU gagal)."""
    try:
        return safelinku_shorten(SUBSCRIBE_URL, SUBSCRIBE_ALIAS, log)
    except Exception as e:
        if log:
            log(f"⚠️ SafelinkU gagal ({e}) — memakai URL subscribe asli.")
        return SUBSCRIBE_URL


# ---------------- UPLOAD FEBS POT (MULTI-AKUN) ----------------
def _default_accounts():
    return {"active": None, "accounts": {}}


def _write_maybe(acc):
    try:
        save_accounts(acc)
    except Exception:
        pass  # filesystem read-only → akun tetap dipakai di memori/sesi


def load_accounts():
    """Muat akun — prioritas: file lokal (terbaru) → Worker KV (tahan restart) → Secrets/env.

    Lokal dulu karena KV butuh hingga ~60 dtk agar tulisannya terlihat (eventual
    consistency); di restarted container file lokal hilang → baru ambil dari Worker.
    """
    local = None
    if os.path.exists(ACCOUNTS_FILE):
        try:
            local = json.load(open(ACCOUNTS_FILE))
        except (json.JSONDecodeError, OSError):
            local = None

    # 1) file lokal ada & berisi akun → pakai (paling baru, ditulis sebelum push)
    if isinstance(local, dict) and local.get("accounts"):
        local.setdefault("active", None)
        local.setdefault("accounts", {})
        if sync_enabled():
            sync_push_smart("accounts", local)    # sinkron cloud hemat kuota (maks 1×/6 jam)
        return local

    # 2) Worker KV = cadangan saat container di-restart / file lokal hilang
    remote = sync_fetch("accounts")
    if isinstance(remote, dict) and remote.get("accounts"):
        remote.setdefault("active", None)
        remote.setdefault("accounts", {})
        try:
            save_accounts(remote, push=False)     # tulis balik ke file lokal (tanpa push balik)
        except Exception:
            pass
        return remote

    acc = _default_accounts()

    # 1) seed multi-akun dari Secrets (env FEBSPOT_ACCOUNTS_SEED) — format file akun lengkap
    seed = os.environ.get("FEBSPOT_ACCOUNTS_SEED")
    if seed and seed.lstrip().startswith(("{", "[")):
        try:
            d = json.loads(seed)
            if isinstance(d, dict) and d.get("accounts"):
                d.setdefault("active", None)
                _write_maybe(d)
                return d
        except Exception:
            pass

    # 2) migrasi akun tunggal: file febspot_cookies.json ATAU env FEBSPOT_COOKIES (JSON literal)
    raw = None
    if os.path.exists(FEBSPOT_COOKIES):
        try:
            raw = open(FEBSPOT_COOKIES, encoding="utf-8").read()
        except Exception:
            raw = None
    env_raw = os.environ.get("FEBSPOT_COOKIES")
    if env_raw and env_raw.lstrip().startswith(("[", "{")):
        raw = env_raw
    if raw:
        try:
            cks = normalize_cookies(raw)
            aid = "default"
            acc["accounts"][aid] = {"id": aid, "label": "Akun Utama",
                                    "channel": FEBSPOT_CHANNEL,
                                    "cookies": cks, "snapshot": None,
                                    "created": time.time()}
            acc["active"] = aid
            _write_maybe(acc)
        except Exception:
            pass
    return acc


def save_accounts(acc, push=True):
    """Simpan akun: file lokal + (bila dikonfigurasi) push ke Worker KV cloud.
    push=False dipakai saat memulihkan dari remote (hindari push balik yang boros kuota)."""
    tmp = ACCOUNTS_FILE + ".tmp"
    try:
        d = os.path.dirname(ACCOUNTS_FILE) or "."
        os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(acc, f, indent=2)
        os.replace(tmp, ACCOUNTS_FILE)
    except OSError as e:
        # file lokal gagal (read-only) → jangan matikan alur; Worker tetap jadi cadangan
        if not sync_enabled():
            raise RuntimeError(
                f"Tidak dapat menulis file akun '{ACCOUNTS_FILE}' ({e}). "
                f"Di Streamlit Cloud pastikan FEBSPOT_ACCOUNTS_FILE menunjuk folder yang bisa "
                f"ditulis (mis. /tmp) — atau atur FEBSPOT_SYNC_URL/TOKEN (Cloudflare Worker), "
                f"atau simpan cookies permanen di Secrets.")
    if push and sync_enabled():
        sync_push("accounts", acc)


def _clean_domain(d):
    d = str(d or "")
    d = re.sub(r"https?://", "", d)
    d = d.split(")(")[0].split("(")[0].strip().strip("[]").strip(".").strip()
    return "." + d if d and not d.startswith(".") else d


def normalize_cookies(text):
    """Terima JSON array (EditThisCookie), {name:value}, atau string 'k=v; k2=v2'.
    Balas list {domain,name,value}."""
    if isinstance(text, (list, tuple)):
        out = []
        for c in text:
            if isinstance(c, dict) and c.get("name"):
                out.append({"domain": _clean_domain(c.get("domain", ".febspot.com")),
                            "name": c["name"], "value": str(c.get("value", ""))})
        return out
    s = str(text).strip()
    if not s:
        raise ValueError("Cookies kosong")
    if s.startswith(("[", "{")):
        data = json.loads(s)
        if isinstance(data, dict):
            if "accounts" in data and isinstance(data["accounts"], dict):
                raise ValueError("Itu file akun (multi-akun). Tempel cookies salah satu akun saja.")
            if "domain" in data or "name" in data and "value" in data:
                return normalize_cookies([data])
            # {name: value, ...}
            return [{"domain": ".febspot.com", "name": k, "value": str(v)}
                    for k, v in data.items()]
        return normalize_cookies(data)
    out = []
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out.append({"domain": ".febspot.com", "name": k.strip(), "value": v.strip()})
    return out


def add_account(label, cookies_text, channel=None):
    """Tambah akun baru; otomatis jadi aktif. Balas dict akun."""
    cks = normalize_cookies(cookies_text)
    if not cks:
        raise ValueError("Tidak ada cookie valid yang ditemukan.")
    acc = load_accounts()
    aid = "acc-" + secrets.token_hex(4)
    acc["accounts"][aid] = {"id": aid, "label": (label or "Akun " + aid)[:40],
                            "channel": str(channel or FEBSPOT_CHANNEL),
                            "cookies": cks, "snapshot": None, "created": time.time()}
    acc["active"] = aid
    save_accounts(acc)
    return acc["accounts"][aid]


def remove_account(aid):
    acc = load_accounts()
    if aid in acc["accounts"]:
        del acc["accounts"][aid]
        if acc["active"] == aid:
            acc["active"] = next(iter(acc["accounts"]), None)
    save_accounts(acc)
    return acc


def set_active_account(aid):
    acc = load_accounts()
    if aid in acc["accounts"]:
        acc["active"] = aid
        save_accounts(acc)
    return acc


def get_account(aid=None):
    acc = load_accounts()
    aid = aid or acc.get("active")
    if not aid or aid not in acc["accounts"]:
        raise RuntimeError("Tidak ada akun febspot. Tambahkan akun dulu (tempel cookies).")
    return acc["accounts"][aid]


def febspot_session(cookies):
    s = requests.Session()
    cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    s.headers["Cookie"] = cookie_hdr
    s.headers["User-Agent"] = UA
    s.headers["Accept"] = "application/json, text/html;q=0.9,*/*;q=0.8"
    return s


def _clean_html(html):
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\|+", "|", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _find_num(text, label, kind="num", window=420):
    """Angka SETELAH label (mis. 'Total Subscribers | 8')."""
    i = text.find(label)
    if i < 0:
        return None
    seg = text[i:i + window]
    if kind == "money":
        m = re.search(r"\$\s*([\d.,]+)", seg)
        return m.group(1) if m else None
    m = re.search(r"\|\s*([\d][\d.,]*)", seg)
    return m.group(1) if m else None


def _find_num_before(text, label, window=420):
    """Angka SEBELUM label (pola dashboard: 'Videos | 11 | Total uploaded')."""
    i = text.find(label)
    if i < 0:
        return None
    seg = text[max(0, i - window):i]
    nums = re.findall(r"([\d][\d.,]*)\s*\|", seg)
    return nums[-1] if nums else None


def account_info(aid=None, log=None):
    """Ambil data akun febspot (nama, email, video, views, earning, dll).
    Balas snapshot dict — dan menyimpannya ke file akun (persisten)."""
    acc = get_account(aid)
    s = febspot_session(acc["cookies"])
    snap = {"name": None, "email": None, "account_id": None, "channel": acc.get("channel"),
            "videos": 0, "total_views": 0, "subscribers": None, "referrals": None,
            "balance": None, "payout_min": None, "today_views": None, "views_7d": None,
            "monetized_today": None, "status": None, "monetization": None,
            "video_list": [], "fetched_at": None}

    try:
        h = s.get(f"{FEBSPOT_BASE}/edit/profile/", timeout=30).text
        t = _clean_html(h)
        m = re.search(r"Account ID: #?(\d+)", t)
        snap["account_id"] = m.group(1) if m else None
        m = re.search(r"([\w.+-]+@[\w.-]+\.\w{2,})", t)
        snap["email"] = m.group(1) if m else None
        m = re.search(r"Account status:\s*([A-Za-z ]+)", t)
        snap["status"] = m.group(1).strip() if m else None
        m = re.search(r"Monetization:\s*([A-Za-z ]+)", t)
        snap["monetization"] = m.group(1).strip() if m else None
        pre = t[:t.find("Log out")] if "Log out" in t else t[:800]
        tok = [x for x in pre.split("|") if x.strip()]
        snap["name"] = tok[-1].strip() if tok else None
    except Exception as e:
        if log:
            log(f"⚠️ Profile: {e}")

    try:
        h = s.get(f"{FEBSPOT_BASE}/my/", timeout=30).text
        t = _clean_html(h)
        snap["balance"] = _find_num(t, "Available balance", "money")
        snap["payout_min"] = _find_num(t, "$20.00 minimum", "money")
        snap["videos"] = int(_find_num_before(t, "Total uploaded") or 0)
        snap["subscribers"] = _find_num_before(t, "Channel subscribers")
        mc = re.search(r"Creator Monetization\s*\|\s*Active", t)
        if not snap["monetization"] and mc:
            snap["monetization"] = "Active"
    except Exception as e:
        if log:
            log(f"⚠️ Dashboard: {e}")

    try:
        h = s.get(f"{FEBSPOT_BASE}/my/statistics/", timeout=30).text
        t = _clean_html(h)
        snap["referrals"] = _find_num(t, "Total Referrals", "num")
        snap["today_views"] = _find_num_before(t, "Verified monetized views counted today", 600)
        snap["views_7d"] = _find_num_before(t, "Total views last 7 days", 600)
        snap["monetized_today"] = _find_num_before(t, "Today’s monetized views", 600)
    except Exception as e:
        if log:
            log(f"⚠️ Statistik: {e}")

    # daftar video + views per video
    videos = []
    try:
        h = s.get(f"{FEBSPOT_BASE}/my/videos/", timeout=30).text
        m = re.search(r"upload-channel/(\d+)/", h)
        if m:
            snap["channel"] = m.group(1)
        for blk in re.findall(r'<article class="oct-card item">(.*?)</article>', h, re.S):
            mi = re.search(r'edit-video/(\d+)/', blk)
            mt = re.search(r'oct-title[^>]*>([^<]+)<', blk)
            mv = re.search(r'([\d.,]+)\s*views?', blk)
            mu = re.search(r"copy_video_link\('([^']*)'", blk)
            mu2 = re.search(r'href="(https://www\.febspot\.com/video/\d+)"', blk)
            vid = mi.group(1) if mi else None
            if not vid:
                continue
            url = (mu.group(1) if mu else None) or (mu2.group(1) if mu2 else None)
            v = None
            if mv:
                try:
                    v = int(mv.group(1).replace(",", ""))
                except ValueError:
                    v = None
            videos.append({"id": vid, "title": re.sub(r"&#\d+;|&[a-z]+;", "", mt.group(1)).strip() if mt else "",
                           "views": v, "url": url})
            if v:
                snap["total_views"] += v
    except Exception as e:
        if log:
            log(f"⚠️ Video: {e}")
    snap["video_list"] = videos
    if videos and not snap["videos"]:
        snap["videos"] = len(videos)
    snap["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # simpan snapshot → persisten
    accs = load_accounts()
    key = acc["id"]
    if key in accs["accounts"]:
        accs["accounts"][key]["snapshot"] = snap
        accs["accounts"][key]["label"] = snap["name"] or accs["accounts"][key]["label"]
        accs["accounts"][key]["channel"] = snap["channel"] or accs["accounts"][key]["channel"]
        save_accounts(accs)
    if log:
        log(f"✅ Data akun: {snap['name']} | video {snap['videos']} | views {fmt_num(snap['total_views'])} | "
            f"saldo ${snap['balance'] or 0}")
    return snap


def febspot_upload(path, title=None, description="", log=None, account_id=None):
    """Upload video ke kanal febspot akun terpilih (default: akun aktif). Balas dict hasil."""
    acc = get_account(account_id)
    channel = acc.get("channel") or FEBSPOT_CHANNEL
    url = f"{FEBSPOT_BASE}/upload-channel/{channel}/"
    s = febspot_session(acc["cookies"])
    s.headers["Referer"] = url
    s.headers["X-Requested-With"] = "XMLHttpRequest"
    name = os.path.basename(path)
    token = secrets.token_hex(16)

    if log:
        log(f"☁️  Upload ke febspot.com (kanal {FEBSPOT_CHANNEL}) ...")
    with open(path, "rb") as f:
        r = s.post(url, files={
            "content": (name, f, "video/mp4"),
            "upload_option": (None, "file"),
            "action": (None, "upload_file"),
            "realname": (None, name),
            "filename": (None, token),
            "format": (None, "json"),
            "mode": (None, "async"),
        }, timeout=None)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError("Respons upload febspot bukan JSON: %s" % r.text[:200])
    if j.get("status") != "success":
        errs = "; ".join(e.get("message", "") for e in j.get("errors", [])) or str(j)[:200]
        raise RuntimeError(f"Upload febspot gagal: {errs}")
    d = j.get("data") or {}
    file_name = d.get("file") or token + ".mp4"
    file_hash = d.get("file_hash") or d.get("filename") or token
    if log:
        log(f"☁️  Berhasil diunggah ({d.get('size_string', '?')}, durasi {d.get('duration_string', '?')}). Publikasi...")

    payload = {
        "dvd_id": channel,
        "allow_commnent": "1",
        "is_accept": "1",
        "function": "get_block",
        "block_id": "video_edit_video_edit",
        "action": "add_new_complete",
        "file": file_name,
        "file_hash": file_hash,
        "title": (title or os.path.splitext(name)[0])[:95],
        "description": description or "",
        "format": "json",
        "mode": "async",
    }
    r2 = s.post(url, data=payload, timeout=180)
    try:
        j2 = r2.json()
    except ValueError:
        raise RuntimeError("Respons publish febspot bukan JSON: %s" % r2.text[:200])
    if j2.get("status") != "success":
        raise RuntimeError(f"Publish febspot gagal: {str(j2)[:200]}")

    # cari video id & URL publik (tunggu prosesing/moderasi)
    vid = None
    pub_url = None
    for attempt in range(30):
        try:
            r3 = s.get(f"{FEBSPOT_BASE}/my/videos/", timeout=30)
            html = r3.text
            m = re.search(r'edit-video/(\d+)/', html)
            if m:
                vid = m.group(1)
                i = html.find(f"edit-video/{vid}/")
                seg = html[max(0, i - 1400):i + 800]
                u = re.search(r"copy_video_link\(\s*'([^']*)'", seg)
                if u and u.group(1):
                    pub_url = u.group(1)
                    break
        except requests.RequestException:
            pass
        if log and attempt in (0, 5, 15):
            log(f"⏳ Menunggu febspot memproses video... ({attempt + 1}/30)")
        time.sleep(6)

    result = {
        "video_id": vid,
        "url": pub_url,
        "manage_url": f"{FEBSPOT_BASE}/edit-video/{vid}/" if vid else None,
        "channel": channel,
        "account": acc.get("label"),
        "account_id": acc.get("id"),
    }
    if pub_url:
        if log:
            log(f"✅ Video publik: {pub_url}")
    elif vid:
        if log:
            log(f"⏳ Video dibuat (id {vid}) — menunggu review, cek via manage link.")
    else:
        if log:
            log("⚠️ Video terupload, tapi link belum muncul — cek dashboard febspot.")
    return result


def build_feb_description(d, log=None):
    """Deskripsi untuk febspot: info sumber + link subscribe (SafelinkU) +
    deskripsi YT + hashtags."""
    tags = [re.sub(r"\s+", "", t) for t in (d.get("tags") or []) if re.sub(r"\s+", "", t)]
    hashtags = " ".join("#" + t[:30] for t in tags[:12])
    src = (f"Sumber: YouTube — {d.get('channel')}\n"
           f"Views: {fmt_num(d.get('view_count'))} | Likes: {fmt_num(d.get('like_count'))} | "
           f"Durasi: {fmt_dur(d.get('duration'))}")
    sub = subscribe_link(log=log)
    sub_line = f"📢 Subscribe: {sub}"
    desc = (d.get("description") or "").strip()
    if len(desc) > 1500:
        desc = desc[:1500] + " ..."
    return "\n".join(p for p in (src, sub_line, "", desc, "", hashtags) if p)


# ---------------- GRAB (download + upload) ----------------
VALID_TARGETS = ("febspot", "gofile", "both")


def grab_video(video, fmt="mp4", log=None, target="febspot", account_id=None):
    """Info → download → upload ke target → hapus file lokal."""
    if target not in VALID_TARGETS:
        target = "febspot"
    d = video_info(video, log)
    if log:
        log(f"👁️  Views {fmt_num(d.get('view_count'))} | 👍 {fmt_num(d.get('like_count'))} | "
            f"⏱️ {fmt_dur(d.get('duration'))} | 🏷️ {len(d.get('tags') or [])} tags")
    files = {}
    links = []
    feb = None
    try:
        path = download_video(d["id"], fmt, log)
        files["video"] = path

        if target in ("febspot", "both"):
            try:
                feb = febspot_upload(path, title=d.get("title"),
                                     description=build_feb_description(d, log), log=log,
                                     account_id=account_id)
                if feb.get("url"):
                    links.append(("📺 FEBS POT", feb["url"]))
                if feb.get("manage_url"):
                    links.append(("🛠️ KELOLA", feb["manage_url"]))
                if not feb.get("url") and not feb.get("manage_url"):
                    links.append(("📺 FEBS POT", "terunggah — cek dashboard febspot"))
                # short link subscription (SafelinkU) — dipakai di deskripsi & ditampilkan di hasil
                try:
                    links.append(("📢 SUBSCRIBE", subscribe_link()))
                except Exception as e2:
                    if log:
                        log(f"⚠️ short link subscribe gagal: {e2}")
            except Exception as e:
                if target == "both":
                    if log:
                        log(f"⚠️ febspot gagal: {e} — lanjut ke gofile.")
                else:
                    raise

        if target in ("gofile", "both"):
            link, _ = gofile_upload(path, log)
            links.append(("🎬 VIDEO", link))
            meta = build_meta(d)
            mpath = os.path.join(WORK, "metadata.txt")
            with open(mpath, "w", encoding="utf-8") as f:
                f.write(meta)
            files["meta"] = mpath
            link2, _ = gofile_upload(mpath, log)
            links.append(("📋 DATA", link2))

        if not links:
            raise RuntimeError("Tidak ada tujuan upload yang berhasil.")

        return {
            "links": links,
            "info": d,
            "meta": build_meta(d),
            "file": os.path.basename(path),
            "size": os.path.getsize(path),
            "febspot": feb,
        }
    finally:
        cleanup_work()                     # hapus SEMUA file kerja (sukses maupun gagal)
        if log:
            log("🧹 File lokal dihapus. Workspace tetap bersih.")


def grab_keyword(keyword, fmt="mp4", log=None, target="febspot", auto_shortest=True,
                 account_id=None):
    res = search_videos(keyword, 5, log)
    if not res:
        raise RuntimeError("Tidak ada hasil pencarian.")
    if auto_shortest:
        best = min(res, key=lambda r: int(r["dur"]) if str(r["dur"]).isdigit() else 10**12)
        if log:
            log(f"🤖 Mesin memilih video terpendek: {best['id']} — {best['title']}")
    else:
        best = res[0]
        if log:
            log(f"🎯 Mengambil hasil pertama: {best['id']} — {best['title']}")
    return grab_video(best["id"], fmt, log, target, account_id)


# ---------------- CLI ----------------
if __name__ == "__main__":
    from datetime import datetime

    def plog(line):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1].lower()
    try:
        if cmd == "search" and len(sys.argv) >= 3:
            search_videos(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 10, plog)
        elif cmd == "info" and len(sys.argv) >= 3:
            print(build_meta(video_info(sys.argv[2], plog)))
        elif cmd == "grab" and len(sys.argv) >= 3:
            fmt = sys.argv[3] if len(sys.argv) > 3 else "mp4"
            tgt = sys.argv[4] if len(sys.argv) > 4 else "febspot"
            if find_id(sys.argv[2]):
                res = grab_video(sys.argv[2], fmt, plog, tgt)
            else:
                res = grab_keyword(sys.argv[2], fmt, plog, tgt)
            print("\n" + "=" * 60)
            for kind, url in res["links"]:
                print(f"{kind} : {url}")
            print("=" * 60)
        else:
            print(__doc__)
    except Exception as e:
        print("❌", e)
        sys.exit(1)

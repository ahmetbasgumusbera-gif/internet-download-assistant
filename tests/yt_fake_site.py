"""
Sahte "YouTube" sitesi: yerel HTTP sunucusu + gerçek ffmpeg ile üretilmiş küçük medya dosyaları +
yt-dlp'ye tanıtılan bir çıkarıcı (extractor) eklentisi. İnternet gerekmez.

  http://127.0.0.1:PORT/watch/<id>   -> yt-dlp bu adresi "video sayfası" sayar ("gone" = kullanılamıyor)
  http://127.0.0.1:PORT/media/<dosya> -> Range destekli medya dosyaları (isteğe bağlı yavaşlatma)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from utils import paths


def find_ffmpeg():
    """ffmpeg çalıştırılabilirinin yolu (IDA_TEST_FFMPEG > bin/ > PATH > imageio-ffmpeg) ya da None."""
    env = os.environ.get("IDA_TEST_FFMPEG")
    if env and Path(env).exists():
        return env
    folder = paths.get_ffmpeg_dir()
    if folder:
        exe = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def yt_dlp_available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


PLUGIN_CODE = '''
import json
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError


class FakeIDAIE(InfoExtractor):
    IE_NAME = "fakeida"
    _VALID_URL = r"(?P<base>http://127\\.0\\.0\\.1:\\d+)/watch/(?P<id>[\\w-]+)"

    def _real_extract(self, url):
        m = self._match_valid_url(url)
        if m.group("id") == "gone":
            raise ExtractorError("Video unavailable", expected=True)
        info = self._download_json(m.group("base") + "/media/info.json", m.group("id"))
        info["webpage_url"] = url
        return info
'''


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            self._serve()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _serve(self):
        site = self.server.site
        name = self.path.split("?")[0].rsplit("/", 1)[-1]
        file = site.media_dir / name
        if not file.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = file.read_bytes()
        total = len(data)
        start, end, status = 0, total - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
                status = 206
        body = data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        for i in range(0, len(body), 8192):
            piece = body[i:i + 8192]
            self.wfile.write(piece)
            if name != "info.json":
                with site.lock:
                    site.bytes_sent += len(piece)
                if site.delay:
                    time.sleep(site.delay)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class FakeYoutube:
    """with FakeYoutube() as site:  site.url('abc')  site.extra_args  site.ffmpeg"""

    def __init__(self, seconds=4, big=False):
        self.ffmpeg = find_ffmpeg()
        self.seconds = seconds
        self.big = big
        self.delay = 0.0
        self.bytes_sent = 0
        self.lock = threading.Lock()

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="fakeyt_"))
        self.media_dir = self.root / "media"
        self.media_dir.mkdir()
        self._make_media()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.site = self
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._write_info()
        plugin = self.root / "plugins" / "fakepkg" / "yt_dlp_plugins" / "extractor"
        plugin.mkdir(parents=True)
        (plugin / "fakeida.py").write_text(PLUGIN_CODE, encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- yardımcılar
    @property
    def extra_args(self):
        return ("--plugin-dirs", str(self.root / "plugins"))

    def url(self, video_id="abc123"):
        return f"{self.base}/watch/{video_id}"

    def _ff(self, *args):
        subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", *args], check=True)

    def _make_media(self):
        m = self.media_dir
        bitrate = "6M" if self.big else "400k"
        size = "1280x720" if self.big else "320x180"
        for name, rate, fps in (("v1080.mp4", bitrate, 30), ("v360.mp4", "150k", 30)):
            self._ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}", "-t", str(self.seconds),
                     "-c:v", "libx264", "-b:v", rate, "-pix_fmt", "yuv420p", "-g", "15", str(m / name))
        self._ff("-f", "lavfi", "-i", "sine=frequency=440", "-t", str(self.seconds),
                 "-c:a", "aac", "-b:a", "128k", str(m / "a140.m4a"))
        self._ff("-f", "lavfi", "-i", "testsrc2=size=320x180", "-frames:v", "1", str(m / "thumb.jpg"))

    def _write_info(self):
        sizes = {n: (self.media_dir / n).stat().st_size for n in ("v1080.mp4", "v360.mp4", "a140.m4a")}
        formats = [
            {"format_id": "140", "url": f"{self.base}/media/a140.m4a", "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a.40.2", "abr": 128, "tbr": 128, "filesize": sizes["a140.m4a"], "protocol": "https"},
            {"format_id": "134", "url": f"{self.base}/media/v360.mp4", "ext": "mp4", "vcodec": "avc1.4d401e",
             "acodec": "none", "height": 360, "width": 640, "fps": 30, "tbr": 150,
             "filesize": sizes["v360.mp4"], "protocol": "https"},
            {"format_id": "137", "url": f"{self.base}/media/v1080.mp4", "ext": "mp4", "vcodec": "avc1.640028",
             "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 400,
             "filesize": sizes["v1080.mp4"], "protocol": "https"},
        ]
        info = {
            "id": "abc123", "title": "Sahte Video ŞÖĞ", "channel": "Sahte Kanal", "duration": self.seconds,
            "view_count": 42, "upload_date": "20261014", "thumbnail": f"{self.base}/media/thumb.jpg",
            "formats": formats, "chapters": [{"title": "Bölüm", "start_time": 0, "end_time": self.seconds}],
        }
        (self.media_dir / "info.json").write_text(json.dumps(info), encoding="utf-8")


def probe_streams(ffmpeg, path):
    """ffmpeg -i çıktısından akış özetini döndürür (ffprobe gerektirmez)."""
    done = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8")
    text = done.stderr
    return {
        "video": len(re.findall(r"Stream #\d+:\d+.*?: Video:", text)),
        "audio": len(re.findall(r"Stream #\d+:\d+.*?: Audio:", text)),
        "attached_pic": "attached pic" in text,
        "text": text,
    }


def duration_seconds(ffmpeg, path):
    text = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None

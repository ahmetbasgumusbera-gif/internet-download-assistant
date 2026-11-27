"""Sitelerdeki videolar: aday sıralama, akış analizi çözümleme, seçim -> yt-dlp argümanları, çerez dosyası."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core import webvideo
from core.server import clean_webvideo
from core.webvideo import StreamChoice, WebCandidate

HLS = {
    "title": "Film", "thumbnail": "https://x/t.jpg", "duration": 5400, "webpage_url": "https://x/master.m3u8",
    "formats": [
        {"format_id": "hls-audio-tr", "vcodec": "none", "acodec": "mp4a.40.2", "language": "tr", "format_note": "Türkçe", "abr": 128, "ext": "mp4"},
        {"format_id": "hls-audio-en", "vcodec": "none", "acodec": "mp4a.40.2", "language": "en", "format_note": "English", "abr": 128, "ext": "mp4"},
        {"format_id": "hls-720", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "width": 1280, "tbr": 2500, "ext": "mp4"},
        {"format_id": "hls-1080", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "width": 1920, "tbr": 5200, "ext": "mp4"},
        {"format_id": "hls-mux", "vcodec": "avc1.4d401f", "acodec": None, "height": 480, "tbr": 900, "ext": "mp4"},
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
    ],
    "subtitles": {"en": [{"name": "English"}], "tr": [{"name": "Türkçe"}]},
}


class PageTests(unittest.TestCase):
    def test_groups_and_sorting(self):
        page = webvideo.parse_page({"pageUrl": "https://s/", "items": [
            {"url": "https://s/a.mp4", "score": 30}, {"url": "https://ads.s/b.mp4", "score": -10, "ad": True},
            {"url": "https://s/c.mp4", "score": 90}, {"url": "", "score": 5}]})
        rec, others, ads = page.groups()
        self.assertEqual(rec.url, "https://s/c.mp4")
        self.assertEqual([c.url for c in others], ["https://s/a.mp4"])
        self.assertEqual([c.url for c in ads], ["https://ads.s/b.mp4"])

    def test_no_recommendation_when_scores_are_low(self):
        rec, others, _ = webvideo.parse_page({"pageUrl": "https://s/", "items": [{"url": "https://s/a.mp4", "score": 10}]}).groups()
        self.assertIsNone(rec)
        self.assertEqual(len(others), 1)

    def test_file_name(self):
        c = WebCandidate(url="https://s/v/abc.webm?x=1", title="Güzel: Film?")
        self.assertEqual(webvideo.file_name_for(c), "Güzel_ Film_.webm")
        self.assertEqual(webvideo.file_name_for(WebCandidate(url="https://s/play", mime="video/mp4", title="A"), "B"), "A.mp4")
        self.assertEqual(webvideo.file_name_for(WebCandidate(url="https://s/play"), "Sayfa"), "Sayfa.mp4")

    def test_server_cleaning(self):
        data = clean_webvideo({"pageUrl": "https://s/p", "items": [
            {"url": "https://s/a.mp4", "kind": "bilinmeyen", "score": "7", "ad": 1}, {"url": "javascript:x"}], "jar": [{"name": "a", "value": "b", "domain": "s"}, "bozuk"]})
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual((data["items"][0]["kind"], data["items"][0]["score"], data["items"][0]["ad"]), ("file", 7, True))
        self.assertEqual(len(data["jar"]), 1)
        with self.assertRaises(ValueError):
            clean_webvideo({"pageUrl": "chrome://newtab"})


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.info = webvideo.parse_stream(HLS)

    def test_parsing(self):
        self.assertEqual([f.format_id for f in self.info.video_formats], ["hls-1080", "hls-720", "hls-mux"])
        self.assertEqual([f.format_id for f in self.info.audio_formats], ["hls-audio-en", "hls-audio-tr"])
        self.assertEqual([c for c, _ in self.info.subtitles], ["tr", "en"])
        self.assertNotIn("sb0", [f.format_id for f in self.info.formats])

    def test_selectors(self):
        sel = lambda **kw: webvideo.build_selector(self.info, StreamChoice(**kw))
        self.assertEqual(sel(), "bv*+ba/b")
        self.assertEqual(sel(audio_id="hls-audio-tr"), "bv*+hls-audio-tr/b")
        self.assertEqual(sel(video_id="hls-1080"), "hls-1080+ba/hls-1080/b")
        self.assertEqual(sel(video_id="hls-1080", audio_id="hls-audio-tr"), "hls-1080+hls-audio-tr/hls-1080/b")
        self.assertEqual(sel(video_id="hls-mux"), "hls-mux")                     # sesi içinde
        self.assertEqual(sel(video_id="yok"), "bv*+ba/b")

    def test_args(self):
        choice = StreamChoice(video_id="hls-720", audio_id="hls-audio-tr", subtitles=["tr"], container="mkv", title="Film %1")
        args = webvideo.build_args(self.info, choice, referrer="https://s/p", user_agent="UA", cookie_path=Path("c.txt"))
        self.assertEqual(args[args.index("-f") + 1], "hls-720+hls-audio-tr/hls-720/b")
        self.assertEqual(args[args.index("--merge-output-format") + 1], "mkv")
        self.assertEqual(args[args.index("-o") + 1], "Film %%1.%(ext)s")
        self.assertIn("Referer:https://s/p", args)
        self.assertEqual(args[args.index("--sub-langs") + 1], "tr")
        self.assertIn("--embed-subs", args)
        self.assertEqual(args[args.index("--cookies") + 1], "c.txt")

    def test_labels_and_summary(self):
        self.assertEqual(webvideo.video_label(self.info.video_formats[0]), "1080p  ·  5,2 Mbps  ·  sessiz (ses ayrı)")
        self.assertIn("Türkçe", webvideo.audio_label(self.info.audio_formats[1]))
        self.assertEqual(webvideo.choice_summary(self.info, StreamChoice(video_id="hls-720", audio_id="hls-audio-tr")),
                         "720p  ·  tr  ·  MP4")

    def test_empty_formats_is_an_error(self):
        with self.assertRaises(Exception):
            webvideo.parse_stream({"title": "x", "formats": []})

    def test_playlist_takes_first_entry(self):
        info = webvideo.parse_stream({"_type": "playlist", "entries": [None, HLS]})
        self.assertTrue(info.from_playlist)


class ErrorTests(unittest.TestCase):
    def test_site_errors_are_not_blamed_on_youtube_or_internet(self):
        cases = {
            "ERROR: [generic] Unable to download webpage: HTTP Error 403: Forbidden (caused by <HTTPError 403>)": "403",
            "ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found": "404",
            "ERROR: Unsupported URL: https://film.example/izle": "tanınmıyor",
            "ERROR: [generic] Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>": "bağlanılamadı",
            "ERROR: HTTP Error 429: Too Many Requests": "429",
        }
        for raw, key in cases.items():
            with self.subTest(raw=raw):
                message = webvideo.friendly_stream_error(raw)
                self.assertIn(key, message)
                self.assertNotIn("YouTube", message)
                self.assertIn("yt-dlp:", message)

    def test_unknown_error_shows_the_real_line(self):
        self.assertEqual(webvideo.friendly_stream_error("satır\nERROR: garip bir şey"), "yt-dlp hatası: garip bir şey")

    def test_origin_header_follows_referer(self):
        args = webvideo.header_args("https://film.example/izle/1?x=2", "UA")
        self.assertIn("Referer:https://film.example/izle/1?x=2", args)
        self.assertIn("Origin:https://film.example", args)
        self.assertEqual(webvideo.header_args(""), [])


class CookieTests(unittest.TestCase):
    def test_cookie_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            jar = [{"domain": "www.s.com", "hostOnly": True, "path": "/", "secure": True, "name": "a", "value": "1", "expires": 0},
                   {"domain": ".s.com", "hostOnly": False, "path": "/x", "secure": False, "name": "b", "value": "2", "expires": 99},
                   {"domain": "s.com", "name": "bad\tname", "value": "x"}]
            path = webvideo.cookie_file(jar, tmp)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# Netscape HTTP Cookie File")
            self.assertEqual(lines[1].split("\t"), ["www.s.com", "FALSE", "/", "TRUE", "0", "a", "1"])
            self.assertEqual(lines[2].split("\t"), [".s.com", "TRUE", "/x", "FALSE", "99", "b", "2"])
            self.assertEqual(len(lines), 3, "bozuk çerez atlanmalı")
            self.assertEqual(webvideo.cookie_file(jar, tmp), path, "aynı içerik aynı dosya")
            self.assertIsNone(webvideo.cookie_file([], tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

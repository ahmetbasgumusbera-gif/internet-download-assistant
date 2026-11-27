import json
import subprocess
import unittest
from unittest import mock

from core import youtube
from core.youtube import (
    AnalysisError, Selection, analyze, available_heights, build_args, default_selection,
    effective_container, estimate_size, format_selector, fps_options, friendly_error,
    is_playlist_only, is_youtube_url, parse_info, parse_time, pick_audio, pick_video,
    selection_summary, validate_range,
)
from tests.yt_samples import sample_info_dict, sample_video_info

MB = 1024 * 1024


class UrlTests(unittest.TestCase):
    def test_youtube_urls(self):
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "http://youtube.com/watch?v=abc",
            "https://youtu.be/dQw4w9WgXcQ", "https://m.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc", "https://www.youtube.com/shorts/abc123",
            "https://www.youtube.com/live/abc123", "https://www.youtube-nocookie.com/embed/abc",
            "  https://youtu.be/abc  ",
        ):
            self.assertTrue(is_youtube_url(url), url)

    def test_not_youtube(self):
        for url in ("https://example.com/youtube.com", "https://notyoutube.com/watch?v=1",
                    "https://youtube.com.evil.com/watch?v=1", "ftp://youtube.com/x", "", "bozuk"):
            self.assertFalse(is_youtube_url(url), url)

    def test_playlist_detection(self):
        self.assertTrue(is_playlist_only("https://www.youtube.com/playlist?list=PL123"))
        self.assertTrue(is_playlist_only("https://www.youtube.com/watch?list=PL123"))
        self.assertFalse(is_playlist_only("https://www.youtube.com/watch?v=abc&list=PL123"))
        self.assertFalse(is_playlist_only("https://youtu.be/abc?list=PL123"))
        self.assertFalse(is_playlist_only("https://www.youtube.com/watch?v=abc"))


class ParseTests(unittest.TestCase):
    def test_basic_fields(self):
        info = sample_video_info()
        self.assertEqual(info.title, "Örnek video başlığı")
        self.assertEqual(info.channel, "Örnek Kanal")
        self.assertEqual(info.upload_date, "14 Eki 2026")
        self.assertEqual(info.duration, 754)
        self.assertTrue(info.has_chapters)
        self.assertFalse(info.is_live)

    def test_storyboards_are_dropped(self):
        info = sample_video_info()
        self.assertNotIn("sb0", [f.format_id for f in info.formats])

    def test_subtitle_languages_turkish_first(self):
        info = sample_video_info()
        self.assertEqual([c for c, _ in info.subtitle_langs], ["tr", "en"])
        self.assertIn("de", [c for c, _ in info.auto_caption_langs])

    def test_playlist_wrapper_uses_first_entry(self):
        info = parse_info({"_type": "playlist", "entries": [None, sample_info_dict(title="İlk")]})
        self.assertEqual(info.title, "İlk")
        self.assertTrue(info.from_playlist)

    def test_empty_playlist(self):
        with self.assertRaises(AnalysisError):
            parse_info({"_type": "playlist", "entries": []})

    def test_size_estimated_from_bitrate_when_missing(self):
        data = sample_info_dict()
        data["formats"] = [{"format_id": "x", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "none", "tbr": 1000}]
        info = parse_info(data)
        self.assertEqual(info.formats[0].filesize, int(1000 * 125 * 754))

    def test_live_detection(self):
        self.assertTrue(sample_video_info(is_live=True).is_live)
        self.assertTrue(sample_video_info(live_status="is_upcoming").is_live)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.info = sample_video_info()

    def test_available_heights_desc(self):
        self.assertEqual(available_heights(self.info), [2160, 1440, 1080, 720, 480, 360, 240, 144])

    def test_fps_options(self):
        self.assertEqual(fps_options(self.info, 1080), {30, 60})
        self.assertEqual(fps_options(self.info, 480), {30})
        self.assertEqual(fps_options(self.info, 2160), {30, 60})

    def test_default_selection_is_sensible(self):
        sel = default_selection(self.info)
        self.assertEqual((sel.mode, sel.height, sel.fps, sel.container, sel.codec),
                         ("video", 1080, 30, "mp4", "compat"))
        self.assertTrue(sel.embed_thumbnail)
        self.assertEqual(sel.subtitles, [])

    def test_default_when_1080_missing_uses_nearest_lower(self):
        data = sample_info_dict()
        data["formats"] = [f for f in data["formats"] if f.get("height") not in (1080, 1440, 2160)]
        sel = default_selection(parse_info(data))
        self.assertEqual(sel.height, 720)

    def test_default_when_only_low_quality(self):
        data = sample_info_dict()
        data["formats"] = [f for f in data["formats"] if (f.get("height") or 0) <= 240]
        self.assertEqual(default_selection(parse_info(data)).height, 240)

    def test_pick_video_compat_prefers_h264(self):
        self.assertEqual(pick_video(self.info, Selection(height=1080, fps=30)).format_id, "137")
        self.assertEqual(pick_video(self.info, Selection(height=1080, fps=60)).format_id, "299")

    def test_pick_video_efficient_prefers_av1_then_vp9(self):
        self.assertEqual(pick_video(self.info, Selection(height=1080, fps=30, codec="efficient")).format_id, "399")
        self.assertEqual(pick_video(self.info, Selection(height=1080, fps=60, codec="efficient")).format_id, "303")

    def test_pick_video_webm_forces_vp9(self):
        self.assertEqual(pick_video(self.info, Selection(height=1080, fps=30, container="webm")).format_id, "248")

    def test_pick_video_falls_back_when_no_h264(self):
        self.assertEqual(pick_video(self.info, Selection(height=2160, fps=30)).format_id, "313")

    def test_pick_video_avoids_hdr_when_sdr_exists(self):
        self.assertEqual(pick_video(self.info, Selection(height=2160, fps=60)).format_id, "315")

    def test_pick_video_missing_fps_falls_back(self):
        self.assertEqual(pick_video(self.info, Selection(height=480, fps=60)).format_id, "135")

    def test_pick_audio(self):
        self.assertEqual(pick_audio(self.info, Selection(container="mp4")).format_id, "140")
        self.assertEqual(pick_audio(self.info, Selection(container="webm")).format_id, "251")
        self.assertEqual(pick_audio(self.info, Selection(mode="audio", audio="m4a")).format_id, "140")
        self.assertEqual(pick_audio(self.info, Selection(mode="audio", audio="opus")).format_id, "251")
        self.assertEqual(pick_audio(self.info, Selection(mode="audio", audio="mp3")).format_id, "251")

    def test_effective_container_webm_falls_back_to_mkv(self):
        data = sample_info_dict()
        data["formats"] = [f for f in data["formats"] if f["format_id"] not in ("248", "303", "271", "308", "313", "315", "337", "399", "400", "401")]
        info = parse_info(data)
        self.assertEqual(effective_container(info, Selection(height=1080, container="webm")), "mkv")
        self.assertEqual(effective_container(self.info, Selection(height=1080, container="webm")), "webm")
        self.assertEqual(effective_container(self.info, Selection(height=1080, container="mkv")), "mkv")

    def test_estimate_size(self):
        sel = Selection(height=1080, fps=30)
        self.assertEqual(estimate_size(self.info, sel), int(120 * MB) + int(11.6 * MB))
        self.assertEqual(estimate_size(self.info, Selection(mode="video_only", height=1080, fps=30)), 120 * MB)
        self.assertEqual(estimate_size(self.info, Selection(mode="audio", audio="mp3", audio_kbps=320)), 754 * 320 * 125)
        self.assertEqual(estimate_size(self.info, Selection(mode="audio", audio="m4a")), int(11.6 * MB))

    def test_summary(self):
        text = selection_summary(self.info, Selection(height=1080, fps=30))
        self.assertTrue(text.startswith("1080p  ·  30 FPS  ·  MP4  ·  ~"), text)
        self.assertIn("Sessiz", selection_summary(self.info, Selection(mode="video_only", height=720, fps=30)))
        self.assertIn("MP3 192 kbps", selection_summary(self.info, Selection(mode="audio", audio="mp3")))


class ArgsTests(unittest.TestCase):
    def setUp(self):
        self.info = sample_video_info()

    def test_format_selector_uses_concrete_ids_with_fallbacks(self):
        sel = Selection(height=1080, fps=30)
        self.assertEqual(format_selector(self.info, sel),
                         "137+140/137+ba/bv*[height<=1080]+ba/b[height<=1080]/b")
        self.assertEqual(format_selector(self.info, Selection(mode="video_only", height=720, fps=30)),
                         "136/bv*[height<=720]/b")
        self.assertEqual(format_selector(self.info, Selection(mode="audio", audio="m4a")), "140/ba/b")

    def test_default_download_args(self):
        args = build_args(self.info, default_selection(self.info))
        self.assertEqual(args[:2], ["-f", "137+140/137+ba/bv*[height<=1080]+ba/b[height<=1080]/b"])
        for expected in ("--merge-output-format", "mp4", "--embed-metadata", "--embed-thumbnail",
                         "--convert-thumbnails", "--embed-chapters"):
            self.assertIn(expected, args)
        self.assertNotIn("--write-subs", args)
        self.assertNotIn("--download-sections", args)

    def test_thumbnail_off(self):
        sel = default_selection(self.info)
        sel.embed_thumbnail = False
        self.assertNotIn("--embed-thumbnail", build_args(self.info, sel))

    def test_webm_has_no_thumbnail_embedding(self):
        sel = Selection(height=1080, fps=30, container="webm")
        self.assertNotIn("--embed-thumbnail", build_args(self.info, sel))

    def test_subtitles(self):
        sel = default_selection(self.info)
        sel.subtitles = ["tr"]
        args = build_args(self.info, sel)
        self.assertIn("--embed-subs", args)
        self.assertEqual(args[args.index("--sub-langs") + 1], "tr")
        self.assertNotIn("--write-auto-subs", args)           # tr elle yapılmış altyazı olarak var
        sel.subtitles = ["de"]
        self.assertIn("--write-auto-subs", build_args(self.info, sel))   # de yalnızca otomatik

    def test_audio_mp3(self):
        args = build_args(self.info, Selection(mode="audio", audio="mp3", audio_kbps=320))
        self.assertEqual(args[:2], ["-f", "251/ba/b"])
        self.assertIn("-x", args)
        self.assertEqual(args[args.index("--audio-format") + 1], "mp3")
        self.assertEqual(args[args.index("--audio-quality") + 1], "320K")
        self.assertIn("--embed-thumbnail", args)
        self.assertNotIn("--merge-output-format", args)

    def test_audio_original_m4a_has_no_quality_flag(self):
        args = build_args(self.info, Selection(mode="audio", audio="m4a"))
        self.assertEqual(args[args.index("--audio-format") + 1], "m4a")
        self.assertNotIn("--audio-quality", args)

    def test_audio_opus_has_no_thumbnail(self):
        self.assertNotIn("--embed-thumbnail", build_args(self.info, Selection(mode="audio", audio="opus")))

    def test_video_only_remuxes(self):
        args = build_args(self.info, Selection(mode="video_only", height=720, fps=30))
        self.assertIn("--remux-video", args)
        self.assertNotIn("--merge-output-format", args)

    def test_time_range(self):
        sel = default_selection(self.info)
        sel.start, sel.end = "1:30", "3:45"
        args = build_args(self.info, sel)
        self.assertEqual(args[args.index("--download-sections") + 1], "*1:30-3:45")
        sel.end = ""
        self.assertEqual(build_args(self.info, sel)[build_args(self.info, sel).index("--download-sections") + 1], "*1:30-inf")

    def test_invalid_time_range_rejected(self):
        sel = default_selection(self.info)
        sel.start, sel.end = "5:00", "1:00"
        with self.assertRaises(ValueError):
            build_args(self.info, sel)
        sel.start, sel.end = "abc", ""
        with self.assertRaises(ValueError):
            build_args(self.info, sel)
        sel.start, sel.end = "99:00", ""
        with self.assertRaises(ValueError):
            build_args(self.info, sel)

    def test_no_chapters_flag_when_video_has_none(self):
        info = sample_video_info(chapters=[])
        self.assertNotIn("--embed-chapters", build_args(info, default_selection(info)))


class TimeTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("90"), 90)
        self.assertEqual(parse_time("1:30"), 90)
        self.assertEqual(parse_time("01:02:03"), 3723)
        self.assertIsNone(parse_time(""))
        with self.assertRaises(ValueError):
            parse_time("1:2:3:4")

    def test_validate_range(self):
        validate_range("", "")
        validate_range("0:10", "0:20", 60)
        with self.assertRaises(ValueError):
            validate_range("0:20", "0:10")


class ErrorTests(unittest.TestCase):
    def test_friendly_messages(self):
        cases = {
            "ERROR: [youtube] x: Sign in to confirm you're not a bot": "bot",
            "ERROR: [youtube] x: Private video. Sign in if you've been granted access": "özel",
            "ERROR: [youtube] x: Video unavailable": "kullanılamıyor",
            "ERROR: Requested format is not available": "biçim",
            "ERROR: Postprocessing: ffprobe and ffmpeg not found": "ffmpeg",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden": "403",
            "ERROR: Unsupported URL: https://x": "desteklenmiyor",
        }
        for raw, key in cases.items():
            self.assertIn(key, friendly_error(raw), raw)

    def test_unknown_error_is_shown_trimmed(self):
        self.assertIn("garip", friendly_error("satır1\nERROR: garip bir şey"))


class AnalyzeTests(unittest.TestCase):
    def fake(self, returncode=0, stdout="", stderr=""):
        return mock.patch("core.youtube.procutil.run_capture",
                          return_value=subprocess.CompletedProcess([], returncode, stdout, stderr))

    def test_success(self):
        with self.fake(stdout=json.dumps(sample_info_dict())):
            info = analyze("https://youtu.be/x")
        self.assertEqual(info.id, "dQw4w9WgXcQ")

    def test_failure_is_friendly(self):
        with self.fake(returncode=1, stderr="ERROR: Video unavailable"):
            with self.assertRaises(AnalysisError) as cm:
                analyze("https://youtu.be/x")
        self.assertIn("kullanılamıyor", str(cm.exception))

    def test_invalid_json(self):
        with self.fake(stdout="bu json degil"):
            with self.assertRaises(AnalysisError):
                analyze("https://youtu.be/x")

    def test_live_is_rejected(self):
        with self.fake(stdout=json.dumps(sample_info_dict(is_live=True))):
            with self.assertRaises(AnalysisError):
                analyze("https://youtu.be/x")

    def test_timeout_is_friendly(self):
        with mock.patch("core.youtube.procutil.run_capture", side_effect=subprocess.TimeoutExpired("x", 1)):
            with self.assertRaises(AnalysisError) as cm:
                analyze("https://youtu.be/x")
        self.assertIn("zaman aşımı", str(cm.exception))

    def test_command_uses_no_playlist_and_js_runtime(self):
        with self.fake(stdout=json.dumps(sample_info_dict())) as run, \
                mock.patch("core.youtube.js_runtime_status", return_value="node"):
            analyze("https://youtu.be/x")
        cmd = run.call_args[0][0]
        self.assertIn("--no-playlist", cmd)
        self.assertEqual(cmd[cmd.index("--js-runtimes") + 1], "node")

    def test_deno_needs_no_flag(self):
        with mock.patch("core.youtube.js_runtime_status", return_value="deno"), \
                mock.patch("core.youtube.tools.tool_path", return_value=None):
            self.assertEqual(youtube.js_runtime_args(), [])


if __name__ == "__main__":
    unittest.main()

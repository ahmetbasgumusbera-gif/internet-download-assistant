"""
Testlerde kullanılan, gerçek bir YouTube analiz çıktısına (yt-dlp -J) benzeyen örnek veri.
Biçimler yt-dlp'nin sıralamasıyla verilir: önce ses, sonra video; her grupta kalite artan sırada.
"""
from core.youtube import parse_info

_MB = 1024 * 1024


def _v(fid, height, width, fps, vcodec, ext, size_mb=None, tbr=0, hdr="SDR"):
    d = {"format_id": fid, "ext": ext, "height": height, "width": width, "fps": fps,
         "vcodec": vcodec, "acodec": "none", "tbr": tbr, "dynamic_range": hdr}
    if size_mb is not None:
        d["filesize"] = int(size_mb * _MB)
    return d


def _a(fid, ext, acodec, abr, size_mb, lang="en"):
    return {"format_id": fid, "ext": ext, "height": None, "width": None, "fps": None,
            "vcodec": "none", "acodec": acodec, "abr": abr, "tbr": abr,
            "filesize": int(size_mb * _MB), "language": lang, "format_note": "medium"}


def sample_info_dict(**overrides):
    formats = [
        {"format_id": "sb0", "ext": "mhtml", "height": 45, "width": 80, "vcodec": "none", "acodec": "none"},
        _a("139", "m4a", "mp4a.40.5", 49, 4.4), _a("249", "webm", "opus", 50, 4.5),
        _a("250", "webm", "opus", 70, 6.2), _a("140", "m4a", "mp4a.40.2", 129, 11.6),
        _a("251", "webm", "opus", 134, 11.9),
        # H.264 (mp4)
        _v("160", 144, 256, 30, "avc1.4d400c", "mp4", 4), _v("133", 240, 426, 30, "avc1.4d4015", "mp4", 8),
        _v("134", 360, 640, 30, "avc1.4d401e", "mp4", 15), _v("135", 480, 854, 30, "avc1.4d401f", "mp4", 28),
        _v("136", 720, 1280, 30, "avc1.4d401f", "mp4", 60), _v("298", 720, 1280, 60, "avc1.4d4020", "mp4", 85),
        _v("137", 1080, 1920, 30, "avc1.640028", "mp4", 120), _v("299", 1080, 1920, 60, "avc1.64002a", "mp4", 180),
        # VP9 (webm)
        _v("248", 1080, 1920, 30, "vp09.00.40.08", "webm", 90), _v("303", 1080, 1920, 60, "vp09.00.40.08", "webm", 140),
        _v("271", 1440, 2560, 30, "vp09.00.50.08", "webm", 230), _v("308", 1440, 2560, 60, "vp09.00.50.08", "webm", 360),
        _v("313", 2160, 3840, 30, "vp09.00.50.08", "webm", 650), _v("315", 2160, 3840, 60, "vp09.00.51.08", "webm", 1100),
        _v("337", 2160, 3840, 60, "vp09.02.51.10", "webm", 1250, hdr="HDR10"),
        # AV1 (mp4)
        _v("399", 1080, 1920, 30, "av01.0.08M.08", "mp4", 75), _v("400", 1440, 2560, 30, "av01.0.12M.08", "mp4", 190),
        _v("401", 2160, 3840, 30, "av01.0.12M.08", "mp4", 520),
        # birleşik (ses+video)
        {"format_id": "18", "ext": "mp4", "height": 360, "width": 640, "fps": 30, "vcodec": "avc1.42001E",
         "acodec": "mp4a.40.2", "tbr": 300, "filesize": 25 * _MB},
    ]
    data = {
        "id": "dQw4w9WgXcQ", "title": "Örnek video başlığı", "channel": "Örnek Kanal",
        "duration": 754, "view_count": 1234567, "upload_date": "20261014",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "formats": formats,
        "subtitles": {"en": [{"name": "English"}], "tr": [{"name": "Türkçe"}]},
        "automatic_captions": {"de": [{"name": "Almanca"}], "tr": [{"name": "Türkçe (otomatik)"}]},
        "chapters": [{"title": "Giriş", "start_time": 0, "end_time": 60}],
        "is_live": False,
    }
    data.update(overrides)
    return data


def sample_video_info(**overrides):
    return parse_info(sample_info_dict(**overrides))

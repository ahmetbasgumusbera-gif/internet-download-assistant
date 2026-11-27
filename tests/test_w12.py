"""W.12: tanı bulguları ve rapor klasörü (ağ yok)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core import diagnostics
from utils.config_manager import ConfigManager


def sample(**over):
    data = {
        "system": {"disk_free": 50 * 1024 ** 3, "js_runtime": "deno", "proxy": [], "config": {"connections_per_download": 8}},
        "tools": {"yt-dlp": {"path": "x", "version": "1", "seconds": 0.5},
                  "ffmpeg": {"path": "x", "version": "1", "seconds": 0.2},
                  "deno": {"path": "x", "version": "1", "seconds": 0.2}},
        "net": [{"host": "github.com", "dns_ms": 20, "tcp_ms": 30, "tls_ms": 60}],
        "http": [], "youtube": {},
    }
    data.update(over)
    return data


class FindingsTests(unittest.TestCase):
    def test_clean_system_has_no_problem(self):
        self.assertEqual(diagnostics.findings(sample()), ["Belirgin bir sorun bulunamadı."])

    def test_missing_tool_slow_tool_and_network_error(self):
        data = sample(tools={"yt-dlp": {"path": "x", "version": "1", "seconds": 6.0, "warm_seconds": 5.0},
                             "ffmpeg": {"path": None, "version": "", "seconds": 0.0}},
                      net=[{"host": "a.com", "error": "zaman aşımı"}])
        text = "\n".join(diagnostics.findings(data))
        self.assertIn("ffmpeg bulunamadı", text)
        self.assertIn("yt-dlp ilk çalıştırmada", text)
        self.assertIn("a.com bağlantısı kurulamadı", text)

    def test_connection_comparison_and_slow_start(self):
        http = [{"group": "small", "label": "zip", "ok": True, "connections": 8, "ready_s": 4.2, "first_byte_s": 4.5},
                {"group": "speed", "label": "t", "ok": True, "connections": 1, "speed": 10e6},
                {"group": "speed", "label": "t", "ok": True, "connections": 8, "speed": 10.5e6}]
        text = "\n".join(diagnostics.findings(sample(http=http)))
        self.assertIn("sondalama", text)
        self.assertIn("Tek bağlantı", text)

    def test_failed_case_is_reported(self):
        http = [{"group": "custom", "label": "Kendi bağlantın 1", "ok": False, "error": "HTTP 403"}]
        self.assertIn("HTTP 403", "\n".join(diagnostics.findings(sample(http=http))))

    def test_render_contains_sections(self):
        text = diagnostics.render(dict(sample(), started="x", elapsed=1.0), ["not"], "günlük")
        for part in ("BULGULAR", "SİSTEM", "ARAÇLAR", "AĞ (ms)", "YOUTUBE", "günlük"):
            self.assertIn(part, text)


class ReportDirTests(unittest.TestCase):
    def test_configured_folder_is_used_and_created(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = ConfigManager(tmp / "c.json")
            cfg.set("diag_dir", str(tmp / "raporlar"))
            self.assertEqual(diagnostics.report_dir(cfg), tmp / "raporlar")
            path = diagnostics.save_report("merhaba", cfg)
            self.assertTrue(path.is_file() and path.parent == tmp / "raporlar")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""W.11: sürüm karşılaştırma ve GitHub sürüm yanıtının işlenmesi (ağ yok)."""
import unittest
from unittest import mock

from core import appupdate


def fake_response(tag="v1.5.1", assets=None, status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = {
        "tag_name": tag, "body": "notlar", "html_url": "https://github.com/x/y/releases/tag/" + tag,
        "assets": assets if assets is not None else [
            {"name": "IDA-Setup-1.5.1.exe", "size": 10, "browser_download_url": "https://x/IDA-Setup-1.5.1.exe"},
            {"name": "IDA-Setup-1.5.1.exe.sha256", "size": 90, "browser_download_url": "https://x/s.sha256"}]}
    return resp


class VersionTests(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(appupdate.parse_version("v1.10.0"), appupdate.parse_version("1.9.9"))
        self.assertEqual(appupdate.parse_version("v1.5"), appupdate.parse_version("1.5.0"))
        self.assertEqual(appupdate.parse_version(""), (0, 0, 0, 0))


class CheckTests(unittest.TestCase):
    def test_newer_version_is_reported(self):
        with mock.patch("core.appupdate.requests.get", return_value=fake_response()):
            info = appupdate.check("1.5.0")
        self.assertEqual(info["version"], "1.5.1")
        self.assertEqual(info["name"], "IDA-Setup-1.5.1.exe")
        self.assertTrue(info["sha_url"])

    def test_same_or_older_is_none(self):
        with mock.patch("core.appupdate.requests.get", return_value=fake_response()):
            self.assertIsNone(appupdate.check("1.5.1"))
            self.assertIsNone(appupdate.check("2.0.0"))

    def test_missing_setup_asset_and_http_errors(self):
        with mock.patch("core.appupdate.requests.get", return_value=fake_response(assets=[])):
            with self.assertRaises(appupdate.UpdateError):
                appupdate.check("1.0.0")
        for status in (404, 403):
            with mock.patch("core.appupdate.requests.get", return_value=fake_response(status=status)):
                with self.assertRaises(appupdate.UpdateError):
                    appupdate.check("1.0.0")


if __name__ == "__main__":
    unittest.main()

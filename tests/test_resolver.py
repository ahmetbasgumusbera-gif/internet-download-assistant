"""Çözümleyici testleri: saf adres dönüşümü, ağ yok."""
import unittest

from core.resolver import (
    DRIVE, DROPBOX, FOLDER_MESSAGE, GITHUB, Resolved, UnsupportedLink, drive_direct_url, resolve,
)

ID = "1iSjoR6LefVFpbxInh2guK3JDPAArbq3v"
DIRECT = drive_direct_url(ID)


class GoogleDriveTests(unittest.TestCase):
    def test_direct_url_format(self):
        self.assertEqual(DIRECT, f"https://drive.usercontent.google.com/download?id={ID}&export=download&authuser=0&confirm=t")

    def test_file_links_become_direct_download(self):
        links = (
            f"https://drive.google.com/file/d/{ID}/view?usp=drive_link",
            f"https://drive.google.com/file/d/{ID}/view?usp=sharing&resourcekey=0-abc_DEF",
            f"https://drive.google.com/file/d/{ID}/view",
            f"https://drive.google.com/file/d/{ID}/preview",
            f"https://drive.google.com/file/d/{ID}/edit",
            f"https://drive.google.com/file/d/{ID}",
            f"https://drive.google.com/file/u/0/d/{ID}/view",
            f"https://drive.google.com/u/1/file/d/{ID}/view",
            f"http://drive.google.com/file/d/{ID}/view",
            f"HTTPS://Drive.Google.com/file/d/{ID}/view",
            f"  https://drive.google.com/file/d/{ID}/view  ",
            f"https://drive.google.com/open?id={ID}",
            f"https://drive.google.com/uc?id={ID}",
            f"https://drive.google.com/uc?export=download&id={ID}",
            f"https://drive.google.com/u/0/uc?id={ID}&export=download",
            f"https://docs.google.com/uc?id={ID}&export=download",
        )
        for link in links:
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(DIRECT, DRIVE))

    def test_folders_are_rejected_with_clear_message(self):
        for link in (
            "https://drive.google.com/drive/folders/1AbCdEf",
            "https://drive.google.com/drive/u/0/folders/1AbCdEf?usp=sharing",
            "https://drive.google.com/drive/folders/1AbCdEf?usp=drive_link",
            "https://drive.google.com/folders/1AbCdEf",
        ):
            with self.subTest(link=link):
                with self.assertRaises(UnsupportedLink) as cm:
                    resolve(link)
                self.assertEqual(str(cm.exception), FOLDER_MESSAGE)
                self.assertIn("klasör", str(cm.exception))
        self.assertTrue(issubclass(UnsupportedLink, ValueError))

    def test_already_direct_usercontent_link_is_kept_but_recognised(self):
        link = f"https://drive.usercontent.google.com/download?id={ID}&export=download&authuser=0&confirm=t&uuid=x&at=y"
        self.assertEqual(resolve(link), Resolved(link, DRIVE))

    def test_other_drive_pages_are_left_alone(self):
        for link in ("https://drive.google.com/drive/my-drive", "https://drive.google.com/drive/u/0/recent",
                     "https://drive.google.com/", "https://drive.google.com/open", "https://drive.google.com/uc?id="):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))


class GoogleDocsTests(unittest.TestCase):
    def test_native_documents_use_export_urls(self):
        cases = (
            (f"https://docs.google.com/document/d/{ID}/edit?usp=sharing", "document", "docx"),
            (f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0", "spreadsheets", "xlsx"),
            (f"https://docs.google.com/presentation/d/{ID}/edit?usp=sharing", "presentation", "pptx"),
            (f"https://docs.google.com/document/u/0/d/{ID}/edit", "document", "docx"),
            (f"https://docs.google.com/document/d/{ID}", "document", "docx"),
        )
        for link, kind, ext in cases:
            with self.subTest(link=link):
                self.assertEqual(resolve(link),
                                 Resolved(f"https://docs.google.com/{kind}/d/{ID}/export?format={ext}", DRIVE))

    def test_existing_export_link_is_kept(self):
        link = f"https://docs.google.com/document/d/{ID}/export?format=pdf"
        self.assertEqual(resolve(link), Resolved(link, DRIVE))

    def test_published_and_form_links_are_left_alone(self):
        for link in ("https://docs.google.com/document/d/e/2PACX-1vAbC/pub",
                     "https://docs.google.com/forms/d/e/1FAIpQLSd/viewform",
                     "https://docs.google.com/"):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))

    def test_id_starting_with_e_is_not_mistaken_for_published(self):
        self.assertEqual(resolve("https://docs.google.com/document/d/e1234/edit").url,
                         "https://docs.google.com/document/d/e1234/export?format=docx")


class DropboxTests(unittest.TestCase):
    def test_dl_is_forced_to_1_and_other_params_kept(self):
        cases = (
            ("https://www.dropbox.com/s/abc123/dosya.zip?dl=0", "https://www.dropbox.com/s/abc123/dosya.zip?dl=1"),
            ("https://www.dropbox.com/s/abc123/dosya.zip", "https://www.dropbox.com/s/abc123/dosya.zip?dl=1"),
            ("https://dropbox.com/s/abc123/dosya.zip?dl=0", "https://dropbox.com/s/abc123/dosya.zip?dl=1"),
            ("http://www.dropbox.com/s/abc123/dosya.zip?dl=0", "https://www.dropbox.com/s/abc123/dosya.zip?dl=1"),
            ("https://www.dropbox.com/s/abc123/dosya.zip?raw=1", "https://www.dropbox.com/s/abc123/dosya.zip?dl=1"),
            ("https://www.dropbox.com/scl/fi/ID/rapor.pdf?rlkey=KEY&st=ST&dl=0",
             "https://www.dropbox.com/scl/fi/ID/rapor.pdf?rlkey=KEY&st=ST&dl=1"),
            ("https://www.dropbox.com/scl/fi/ID/rapor.pdf?dl=1&rlkey=KEY",
             "https://www.dropbox.com/scl/fi/ID/rapor.pdf?rlkey=KEY&dl=1"),
            ("https://www.dropbox.com/scl/fo/ID/klasor?rlkey=KEY&dl=0",
             "https://www.dropbox.com/scl/fo/ID/klasor?rlkey=KEY&dl=1"),
        )
        for link, expected in cases:
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(expected, DROPBOX))

    def test_non_share_pages_and_direct_hosts_are_left_alone(self):
        for link in ("https://www.dropbox.com/home", "https://www.dropbox.com/login",
                     "https://dl.dropboxusercontent.com/s/abc/dosya.zip"):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))


class GitHubTests(unittest.TestCase):
    def test_blob_becomes_raw(self):
        self.assertEqual(resolve("https://github.com/user/repo/blob/main/src/a.py"),
                         Resolved("https://github.com/user/repo/raw/main/src/a.py", GITHUB))
        self.assertEqual(resolve("https://github.com/user/repo/blob/v1.0/docs/Kılavuz.pdf?plain=1#L5"),
                         Resolved("https://github.com/user/repo/raw/v1.0/docs/Kılavuz.pdf", GITHUB))

    def test_other_github_links_are_left_alone(self):
        for link in ("https://github.com/user/repo", "https://github.com/user/repo/releases/download/v1/a.zip",
                     "https://github.com/user/repo/archive/refs/heads/main.zip",
                     "https://github.com/user/repo/blob/", "https://github.com/user/repo/raw/main/a.py",
                     "https://raw.githubusercontent.com/user/repo/main/a.py"):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))


class OneDriveTests(unittest.TestCase):
    def test_short_link_becomes_shares_api(self):
        r = resolve("https://1drv.ms/u/s!AbCdEf")
        self.assertEqual(r.source, "OneDrive")
        self.assertTrue(r.url.startswith("https://api.onedrive.com/v1.0/shares/u!"))
        self.assertTrue(r.url.endswith("/root/content"))
        self.assertNotIn("=", r.url)

    def test_folders_are_rejected(self):
        for link in ("https://1drv.ms/f/s!AbCd",
                     "https://contoso.sharepoint.com/:f:/g/personal/x/Eabc",
                     "https://onedrive.live.com/redir?resid=ABC&authkey=K&ithint=folder"):
            with self.subTest(link=link):
                with self.assertRaises(UnsupportedLink):
                    resolve(link)

    def test_sharepoint_gets_download_flag(self):
        link = "https://contoso.sharepoint.com/:u:/g/personal/x/Eabc?e=zz"
        self.assertEqual(resolve(link), Resolved(link + "&download=1", "OneDrive"))

    def test_lookalike_and_other_pages_are_untouched(self):
        for link in ("https://1drv.ms.evil.com/u/s!A", "https://1drv.ms/", "https://contoso.sharepoint.com/sites/x",
                     "https://onedrive.live.com/download?resid=ABC"):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))


class SafetyTests(unittest.TestCase):
    def test_lookalike_hosts_and_ordinary_links_are_untouched(self):
        for link in (
            f"https://drive.google.com.evil.com/file/d/{ID}/view",
            f"https://notdrive.google.com/file/d/{ID}/view",
            f"https://evil.com/drive.google.com/file/d/{ID}/view",
            f"https://example.com/file/d/{ID}/view",
            "https://www.dropbox.com.evil.com/s/abc/a.zip?dl=0",
            "https://github.com.evil.com/user/repo/blob/main/a.py",
            "https://example.com/dosya.zip", "http://127.0.0.1:8000/a.bin",
        ):
            with self.subTest(link=link):
                self.assertEqual(resolve(link), Resolved(link))

    def test_non_http_and_garbage_are_returned_unchanged(self):
        for link in (f"ftp://drive.google.com/file/d/{ID}/view", "bu bir link değil", "", "   ", "http://[bozuk"):
            with self.subTest(link=link):
                self.assertEqual(resolve(link).url, link.strip())
                self.assertEqual(resolve(link).source, "")

    def test_none_is_safe(self):
        self.assertEqual(resolve(None), Resolved(""))


if __name__ == "__main__":
    unittest.main()

"""
Paylaşım linklerini doğrudan indirme adresine çeviren çözümleyici (Qt yok, AĞ YOK).

Saf bir adres dönüşümüdür: verilen linki tanırsa indirilebilir adresi, tanımazsa linki AYNEN döndürür.
Desteklenen: Google Drive (dosya linkleri), Google Docs/Sheets/Slides (dışa aktarma), Dropbox, GitHub blob.
Hiçbir ağ isteği yapılmaz; bu yüzden test edilebilir ve hızlıdır. Anlamadığı linklere dokunmaz.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

DRIVE = "Google Drive"
DROPBOX = "Dropbox"
GITHUB = "GitHub"

FOLDER_MESSAGE = ("Google Drive klasörleri henüz desteklenmiyor. "
                  "Klasörün içindeki dosyaların bağlantısını tek tek verin.")

_ID = r"[\w-]+"
_DOCS_FORMATS = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pptx"}


class UnsupportedLink(ValueError):
    """Tanınan ama desteklenmeyen paylaşım türü (ör. Drive klasörü). Mesaj kullanıcıya gösterilir."""


@dataclass(frozen=True)
class Resolved:
    url: str            # indirilecek adres (tanınmadıysa girilen adres)
    source: str = ""    # tanınan site adı; boş = tanınmadı / dokunulmadı


def drive_direct_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0&confirm=t"


def _query_id(parsed) -> str:
    value = parse_qs(parsed.query).get("id", [""])[0]
    return value if re.fullmatch(_ID, value) else ""


def _drive(parsed, url: str) -> Resolved:
    path = parsed.path
    if re.search(rf"/folders/{_ID}", path):
        raise UnsupportedLink(FOLDER_MESSAGE)
    match = re.match(rf"^(?:/u/\d+)?/file/(?:u/\d+/)?d/({_ID})", path)
    file_id = match.group(1) if match else ""
    if not file_id and re.match(r"^(?:/u/\d+)?/(?:uc|open)$", path):
        file_id = _query_id(parsed)
    if not file_id:
        return Resolved(url)
    return Resolved(drive_direct_url(file_id), DRIVE)


def _docs(parsed, url: str) -> Resolved:
    path = parsed.path
    match = re.match(rf"^/(document|spreadsheets|presentation)/(?:u/\d+/)?d/(?!e/)({_ID})(.*)$", path)
    if match:
        kind, file_id, rest = match.groups()
        if rest.startswith("/export"):
            return Resolved(url, DRIVE)          # zaten dışa aktarma adresi
        return Resolved(f"https://docs.google.com/{kind}/d/{file_id}/export?format={_DOCS_FORMATS[kind]}", DRIVE)
    if re.match(r"^(?:/u/\d+)?/(?:uc|open)$", path):
        file_id = _query_id(parsed)
        if file_id:
            return Resolved(drive_direct_url(file_id), DRIVE)
    return Resolved(url)


def _dropbox(parsed, url: str) -> Resolved:
    if not re.match(r"^/(?:s|sh|scl)/", parsed.path):
        return Resolved(url)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in ("dl", "raw")]
    pairs.append(("dl", "1"))
    fixed = urlunparse(("https", parsed.netloc, parsed.path, parsed.params, urlencode(pairs), parsed.fragment))
    return Resolved(fixed, DROPBOX)


def _github(parsed, url: str) -> Resolved:
    match = re.match(r"^/([^/]+)/([^/]+)/blob/(.+)$", parsed.path)
    if not match:
        return Resolved(url)
    owner, repo, rest = match.groups()
    return Resolved(f"https://github.com/{owner}/{repo}/raw/{rest}", GITHUB)


ONEDRIVE = "OneDrive"
ONEDRIVE_FOLDER_MESSAGE = ("OneDrive/SharePoint klasörleri henüz desteklenmiyor. "
                           "Klasörün içindeki dosyaların bağlantısını tek tek verin.")


def _share_api_url(url: str) -> str:
    """Paylaşım adresini OneDrive 'shares' API adresine çevirir (u! + base64url, dolgusuz)."""
    token = base64.urlsafe_b64encode(url.split("#")[0].encode("utf-8")).decode("ascii").rstrip("=")
    return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"


def _onedrive(parsed, url: str) -> Resolved:
    host = (parsed.hostname or "").lower()
    if host == "1drv.ms":
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return Resolved(url)
        if parts[0] == "f":
            raise UnsupportedLink(ONEDRIVE_FOLDER_MESSAGE)
        return Resolved(_share_api_url(url), ONEDRIVE)
    query = parse_qs(parsed.query)
    if "resid" not in query or parsed.path.startswith("/download"):
        return Resolved(url)
    if "folder" in (query.get("ithint") or [""])[0].lower():
        raise UnsupportedLink(ONEDRIVE_FOLDER_MESSAGE)
    return Resolved(_share_api_url(url), ONEDRIVE)


def _sharepoint(parsed, url: str) -> Resolved:
    kind = re.match(r"^/:([a-z]):/", parsed.path)
    if not kind:
        return Resolved(url)
    if kind.group(1) == "f":
        raise UnsupportedLink(ONEDRIVE_FOLDER_MESSAGE)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "download"]
    pairs.append(("download", "1"))
    fixed = urlunparse(("https", parsed.netloc, parsed.path, parsed.params, urlencode(pairs), ""))
    return Resolved(fixed, ONEDRIVE)


_HANDLERS = {
    "1drv.ms": _onedrive,
    "onedrive.live.com": _onedrive,
    "drive.google.com": _drive,
    "docs.google.com": _docs,
    "drive.usercontent.google.com": lambda parsed, url: Resolved(url, DRIVE),   # zaten doğrudan adres
    "dropbox.com": _dropbox,
    "www.dropbox.com": _dropbox,
    "github.com": _github,
}


def resolve(url: str) -> Resolved:
    """
    Paylaşım linkini doğrudan indirme adresine çevirir. Tanımazsa linki aynen verir.
    Tanınan ama desteklenmeyen bir tür ise UnsupportedLink (ValueError) fırlatır.
    """
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return Resolved(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return Resolved(url)
    handler = _HANDLERS.get(host) or (_sharepoint if host.endswith(".sharepoint.com") else None)
    return handler(parsed, url) if handler else Resolved(url)

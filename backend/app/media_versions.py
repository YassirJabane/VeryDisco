"""Version identity is separate from display titles and search simplification."""
import re
import unicodedata


def album_identity_key(value: str) -> str:
    """Same edition key for scan, incremental updates and availability checks."""
    clean = re.sub(r'(?i)\s*[\(\[]?\b(?:disc|cd|disk)\s*\d+[\)\]]?\s*$', '', value or '').strip()
    return re.sub(r'[^\w]', '', clean or value or '').lower()

_REMASTER = re.compile(r"(?i)(?:[\s\-–—:(\[]*)\b(?:(?P<before>(?:19|20)\d{2})\s+)?remaster(?:ed)?(?:\s+(?P<after>(?:19|20)\d{2}))?\s*[)\]]?\s*$")


def remaster_identity(title: str) -> tuple[str, str | None]:
    """Return base and version; None is original/unspecified, '' undated remaster."""
    title = (title or "").strip()
    match = _REMASTER.search(title)
    if not match:
        return title, None
    base = title[:match.start()].rstrip(" -–—([:")
    if not base:
        return title, None
    return base, match.group("before") or match.group("after") or ""


def same_master(left: str, right: str) -> bool:
    return remaster_identity(left)[1] == remaster_identity(right)[1]


def master_matches_path(title: str, filename: str, album: str = "") -> bool:
    from pathlib import PurePosixPath
    path = PurePosixPath(filename.replace('\\', '/'))
    expected = remaster_identity(title)[1]
    if expected is None:
        expected = remaster_identity(album)[1]
    actual = remaster_identity(path.stem)[1]
    if actual is None:
        parent = re.sub(r'(?i)\s*[\[(](?:flac|mp3|320(?:kbps)?|lossless|web|cd)[^\])]*[\])]\s*$', '', path.parent.name)
        actual = remaster_identity(parent)[1]
    return expected == actual


def title_key(title: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


def same_recording_title(left: str, right: str) -> bool:
    a, av = remaster_identity(left)
    b, bv = remaster_identity(right)
    return bool(title_key(a)) and title_key(a) == title_key(b) and av == bv

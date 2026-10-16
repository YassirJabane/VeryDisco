"""Small cached artwork derivatives; originals are never changed."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path


def folder_signature(folder: Path) -> tuple:
    # Tag edits don't necessarily change directory mtime.
    return tuple(sorted(
        (p.name, p.stat().st_mtime_ns, p.stat().st_size)
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".mp3", ".flac", ".m4a"}
    ))


@lru_cache(maxsize=256)
def local_thumbnail(folder: str, signature: tuple, size: int) -> bytes | None:
    from PIL import Image, ImageOps, UnidentifiedImageError
    from backend.app.main import _extract_cover_sync

    data, _, file_path = _extract_cover_sync(folder, signature)
    if not data and not file_path:
        return None
    try:
        with Image.open(file_path if file_path else BytesIO(data)) as source:
            source.draft("RGB", (size, size))
            image = ImageOps.exif_transpose(source)
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=80, optimize=True)
            return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None

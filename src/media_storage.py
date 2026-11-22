"""Cloudflare R2 storage for publicly served media files."""
import asyncio
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

from src.config import settings
from src.r2_client import create_r2_client


def _upload_video_to_r2(video_path: str) -> str:
    """Upload an MP4 to R2 and return its public URL."""
    required = {
        "CLOUDFLARE_ACCOUNT_ID": settings.r2_account_id,
        "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
        "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
        "R2_BUCKET_NAME": settings.r2_bucket_name,
        "R2_PUBLIC_BASE_URL": settings.r2_public_base_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"R2 is not fully configured; missing: {', '.join(missing)}")

    public_url = urlparse(settings.r2_public_base_url)
    if public_url.scheme != "https" or not public_url.netloc:
        raise ValueError("R2_PUBLIC_BASE_URL must be an HTTPS URL")

    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file does not exist: {path}")
    if path.suffix.lower() != ".mp4":
        raise ValueError(f"Only MP4 videos can be uploaded: {path}")

    prefix = PurePosixPath(settings.r2_object_prefix.strip("/"))
    object_key = str(prefix / path.name) if str(prefix) != "." else path.name
    client = create_r2_client()
    client.upload_file(
        str(path),
        settings.r2_bucket_name,
        object_key,
        ExtraArgs={
            "ContentType": "video/mp4",
            "CacheControl": "public, max-age=31536000, immutable",
        },
    )

    encoded_key = quote(object_key, safe="/")
    return f"{settings.r2_public_base_url.rstrip('/')}/{encoded_key}"


async def upload_video_to_r2(video_path: str) -> str:
    """Upload an MP4 to R2 without blocking the async pipeline."""
    return await asyncio.to_thread(_upload_video_to_r2, video_path)

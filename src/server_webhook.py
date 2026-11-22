"""
FastAPI server for media hosting and Instagram webhook handling.
Serves generated videos via HTTPS (via Cloudflare Tunnel) and handles webhooks.
"""
import asyncio
from contextlib import asynccontextmanager
import hmac
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Request, Response, HTTPException, Header, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import settings as config_settings
from src.database import get_product_by_id
from src.instagram_uploader import InstagramWebhookHandler
import structlog

logger = structlog.get_logger(__name__)

# Expose settings for test mocking
settings = config_settings


# =============================================================================
# Models
# =============================================================================

class WebhookChallenge(BaseModel):
    """Webhook verification challenge."""
    hub_mode: str
    hub_challenge: str
    hub_verify_token: str


class MediaFileInfo(BaseModel):
    """Media file information."""
    filename: str
    size_bytes: int
    size_mb: float
    modified: datetime
    url: str


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize media serving when the application starts."""
    media_dir = get_media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
    yield


app = FastAPI(
    title="Meesho Fashion Channel API",
    description="Media hosting and webhook handler for Telugu fashion channel",
    version="1.0.0",
    lifespan=lifespan,
)

# Lazy initialization - will be set on first use
webhook_handler = None


def get_webhook_handler() -> InstagramWebhookHandler:
    """Get or create webhook handler with current settings."""
    global webhook_handler
    if webhook_handler is None:
        webhook_handler = InstagramWebhookHandler()
    return webhook_handler


def get_settings():
    """Get settings (allows test mocking)."""
    from src.config import settings
    return settings


# Mount media directory for direct file serving (lazy)
def get_media_dir() -> Path:
    """Get media directory path."""
    return Path(get_settings().media_dir)


# =============================================================================
# Helper Functions
# =============================================================================

def verify_signature(payload: bytes, signature: str, secret: Optional[str] = None) -> bool:
    """Verify HMAC-SHA256 signature from Meta."""
    if not signature:
        return False

    # Handle both "sha256=<hash>" and raw hash formats
    if signature.startswith("sha256="):
        expected_sig = signature[7:]
    else:
        expected_sig = signature

    app_secret_value = settings.meta_app_secret if secret is None else secret
    if not app_secret_value:
        return False
    app_secret = app_secret_value.encode()
    computed_sig = hmac.new(app_secret, payload, hashlib.sha256).hexdigest()

    # Constant-time comparison
    return hmac.compare_digest(computed_sig, expected_sig)


# Alias for backward compatibility / test expectations
verify_webhook_signature = verify_signature


def require_admin_token(token: Optional[str]):
    """Reject admin requests unless the configured token matches."""
    expected = settings.admin_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


# =============================================================================
# Media File Handler
# =============================================================================

class MediaFileHandler:
    """Handler for media file operations."""

    def __init__(self, media_dir: Path, media_base_url: str):
        self.media_dir = Path(media_dir)
        self.media_base_url = media_base_url
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def get_file_path(self, filename: str) -> Optional[Path]:
        """Get full path for a media file with security checks."""
        if ".." in filename or filename.startswith("/"):
            return None
        file_path = self.media_dir / filename
        if file_path.exists() and file_path.is_file():
            return file_path
        return None

    def get_media_url(self, filename: str) -> str:
        """Get public URL for a media file."""
        return f"{self.media_base_url.rstrip('/')}/{filename}"

    def get_media_info(self, filename: str) -> Optional[MediaFileInfo]:
        """Get media file information."""
        file_path = self.get_file_path(filename)
        if not file_path:
            return None

        stat = file_path.stat()
        ext = file_path.suffix.lower()

        # Determine media type
        media_type = "video/mp4"
        if ext in [".jpg", ".jpeg"]:
            media_type = "image/jpeg"
        elif ext == ".png":
            media_type = "image/png"
        elif ext == ".webp":
            media_type = "image/webp"
        elif ext in [".mp3", ".m4a", ".aac"]:
            media_type = "audio/mpeg"

        return MediaFileInfo(
            filename=filename,
            size_bytes=stat.st_size,
            size_mb=round(stat.st_size / (1024 * 1024), 2),
            modified=datetime.fromtimestamp(stat.st_mtime),
            url=self.get_media_url(filename)
        )

    def list_media_files(self) -> List[MediaFileInfo]:
        """List all media files."""
        files = []
        for file_path in self.media_dir.iterdir():
            if file_path.is_file():
                info = self.get_media_info(file_path.name)
                if info:
                    files.append(info)
        return files

    def delete_media_file(self, filename: str) -> bool:
        """Delete a media file."""
        file_path = self.get_file_path(filename)
        if file_path:
            file_path.unlink()
            return True
        return False


def get_media_url(filename: str) -> str:
    """Get public media URL."""
    return f"{get_settings().media_base_url}/{filename}"


# =============================================================================
# Routes
# =============================================================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "Meesho Fashion Channel API",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health():
    """Detailed health check."""
    media_dir = get_media_dir()
    return {
        "status": "healthy",
        "media_dir": str(media_dir),
        "media_dir_exists": media_dir.exists(),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/media/{filename}")
async def serve_media(filename: str):
    """Serve media file with proper headers."""
    # Security: prevent directory traversal
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = get_media_dir()
    file_path = media_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type
    ext = file_path.suffix.lower()
    media_type = "video/mp4"
    if ext in [".jpg", ".jpeg"]:
        media_type = "image/jpeg"
    elif ext == ".png":
        media_type = "image/png"
    elif ext == ".webp":
        media_type = "image/webp"
    elif ext in [".mp3", ".m4a", ".aac"]:
        media_type = "audio/mpeg"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Accept-Ranges": "bytes"
        }
    )


@app.get("/api/media/{filename}/info", response_model=MediaFileInfo)
async def get_media_info(filename: str):
    """Get media file information."""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = get_media_dir()
    file_path = media_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    stat = file_path.stat()
    return MediaFileInfo(
        filename=filename,
        size_bytes=stat.st_size,
        size_mb=round(stat.st_size / (1024 * 1024), 2),
        modified=datetime.fromtimestamp(stat.st_mtime),
        url=get_media_url(filename)
    )


@app.get("/api/products/{product_id}")
async def get_product(product_id: str):
    """Get product by Meesho product ID."""
    product = get_product_by_id(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "meesho_product_id": product.meesho_product_id,
        "title": product.title,
        "category": product.category,
        "price": product.price,
        "original_price": product.original_price,
        "discount_percent": product.discount_percent,
        "rating": product.rating,
        "fabric": product.fabric,
        "color": product.color,
        "product_code": product.product_code,
        "affiliate_url": product.affiliate_url,
        "image_urls": product.image_urls,
        "published_youtube": product.published_youtube,
        "published_instagram": product.published_instagram,
        "scraped_at": product.scraped_at.isoformat() if product.scraped_at else None
    }


# =============================================================================
# Instagram Webhook Endpoints
# =============================================================================

@app.get("/webhook/instagram")
async def verify_instagram_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token")
):
    """Verify Instagram webhook subscription."""
    handler = get_webhook_handler()
    challenge = handler.verify_webhook(hub_mode, hub_verify_token, hub_challenge)

    if challenge:
        return Response(content=challenge, media_type="text/plain")
    else:
        raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook/instagram")
async def handle_instagram_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None)
):
    """Handle Instagram webhook events."""
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    if not x_hub_signature_256:
        logger.warning("Missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=401, detail="Missing signature")
    if not verify_signature(body, x_hub_signature_256):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Received webhook: {payload.get('object', 'unknown')}")

    # Process entries
    for entry in payload.get("entry", []):
        # Handle comments
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})

            if field == "comments":
                await _handle_comment_webhook(value)
            elif field == "mentions":
                await _handle_mention_webhook(value)
            elif field == "messages":
                await _handle_message_webhook(value)

    if settings.r2_state_bucket_name:
        from src.state_storage import upload_database_to_r2
        await asyncio.to_thread(upload_database_to_r2)

    return {"status": "ok"}


async def _handle_comment_webhook(value: dict):
    """Process comment webhook."""
    result = await get_webhook_handler().handle_comment(value)
    logger.info(f"Instagram comment handling result: {result.get('action')}")


async def _handle_mention_webhook(value: dict):
    """Process mention webhook."""
    logger.info(f"Mention received: {value}")


async def _handle_message_webhook(value: dict):
    """Process DM webhook."""
    logger.info(f"Message received: {value}")


# =============================================================================
# Admin Endpoints
# =============================================================================

@app.get("/admin/stats")
async def get_stats(x_admin_token: Optional[str] = Header(None)):
    """Get pipeline statistics."""
    require_admin_token(x_admin_token)
    from src.database import get_pipeline_stats
    return get_pipeline_stats()


@app.get("/admin/quota")
async def get_quota(x_admin_token: Optional[str] = Header(None)):
    """Get quota status for all platforms."""
    require_admin_token(x_admin_token)
    from src.database import get_daily_quota
    from datetime import date

    today = date.today().isoformat()
    quota = get_daily_quota(today, "youtube_uploads")
    return {
        "date": today,
        "youtube_uploads": quota.youtube_uploads if quota else 0,
        "ig_uploads": quota.instagram_uploads if quota else 0,
        "youtube_quota_used": quota.youtube_quota_used if quota else 0,
        "limits": {
            "youtube": get_settings().max_yt_uploads_per_day,
            "instagram": get_settings().max_ig_uploads_per_day
        }
    }


@app.post("/admin/quota/reset")
async def reset_quota(quota_type: str, x_admin_token: Optional[str] = Header(None)):
    """Reset daily quota (for testing)."""
    require_admin_token(x_admin_token)
    from src.database import reset_daily_quota, DailyQuota
    from datetime import date

    today = date.today().isoformat()
    reset_daily_quota(today, quota_type)
    return {"status": "reset", "date": today, "type": quota_type}


@app.post("/admin/state/reload")
async def reload_state(x_admin_token: Optional[str] = Header(None)):
    """Refresh this service's SQLite database from the private R2 snapshot."""
    require_admin_token(x_admin_token)
    if not settings.r2_state_bucket_name:
        raise HTTPException(status_code=503, detail="R2 state storage is not configured")

    from src.state_storage import restore_database_from_r2
    restored = await asyncio.to_thread(restore_database_from_r2)
    return {"status": "reloaded" if restored else "initialized"}


# =============================================================================
# Server Runner
# =============================================================================

def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI server with uvicorn."""
    import uvicorn
    if settings.r2_state_bucket_name:
        from src.state_storage import restore_database_from_r2
        restore_database_from_r2()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
"""
YouTube uploader using YouTube Data API v3 with OAuth 2.0.
Handles resumable uploads, quota management, and metadata.
"""
import asyncio
import os
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config import settings
from src.database import Product, get_daily_quota, increment_daily_quota, DailyQuota
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class UploadResult:
    """Result of YouTube upload."""
    video_id: str
    video_url: str
    upload_time: datetime
    quota_used: int


@dataclass
class QuotaStatus:
    """YouTube quota status."""
    used_today: int
    limit: int
    remaining: int
    can_upload: bool
    reset_time: datetime


# =============================================================================
# YouTube Uploader
# =============================================================================

class YouTubeUploader:
    """YouTube Data API v3 uploader with OAuth 2.0."""

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    TOKEN_FILE = Path("data/youtube_token.pickle")
    CREDENTIALS_FILE = Path("data/google_credentials.json")

    def __init__(self):
        self.youtube = None
        self.credentials = None

    async def authenticate(self) -> bool:
        """Authenticate with Google OAuth 2.0."""
        self.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        creds = None

        # Load existing token
        if self.TOKEN_FILE.exists():
            try:
                with open(self.TOKEN_FILE, "rb") as token:
                    creds = pickle.load(token)
            except Exception as e:
                logger.warning(f"Failed to load token: {e}")

        if creds is None and all((
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_refresh_token,
        )):
            creds = Credentials(
                token=None,
                refresh_token=settings.google_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                scopes=self.SCOPES,
            )

        if creds is None:
            if not self.CREDENTIALS_FILE.exists():
                logger.error(
                    "Google credentials are unavailable. Configure GOOGLE_CLIENT_ID, "
                    "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN, or provide "
                    f"{self.CREDENTIALS_FILE} for local OAuth."
                )
                return False

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.CREDENTIALS_FILE), self.SCOPES
            )
            creds = flow.run_local_server(port=0)
            logger.info("Obtained new Google OAuth credentials")

        if not creds.valid:
            if creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("Refreshed Google OAuth token")
                except Exception as e:
                    logger.error(f"Google OAuth token refresh failed: {e}")
                    raise
            else:
                logger.error("Google OAuth credentials are invalid and cannot be refreshed")
                return False

        # Retain refreshed credentials for local runs; Actions should also supply
        # the refresh token as a secret because runner files are ephemeral.
        with open(self.TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)

        self.credentials = creds
        self.youtube = build("youtube", "v3", credentials=creds)
        logger.info("YouTube API authenticated successfully")
        return True

    async def check_quota(self) -> QuotaStatus:
        """Check daily quota usage."""
        today = datetime.now().date().isoformat()
        quota = get_daily_quota(today, "youtube_uploads")

        used = quota.youtube_uploads if quota else 0
        quota_units_used = quota.youtube_quota_used if quota else 0
        limit = settings.max_yt_uploads_per_day
        remaining = max(0, limit - used)
        can_upload = (
            remaining > 0
            and quota_units_used + settings.yt_quota_per_upload <= settings.yt_quota_limit_daily
        )

        # Quota resets at midnight UTC
        now = datetime.now()
        reset_time = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())

        return QuotaStatus(
            used_today=used,
            limit=limit,
            remaining=remaining,
            can_upload=can_upload,
            reset_time=reset_time
        )

    async def upload_video(
        self,
        video_path: str,
        product: Product,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        privacy_status: Optional[str] = None,
        category_id: Optional[int] = None
    ) -> UploadResult:
        """Upload video to YouTube with resumable upload."""
        if not self.youtube and not await self.authenticate():
            raise RuntimeError("YouTube authentication failed")

        # Check quota
        quota_status = await self.check_quota()
        if not quota_status.can_upload:
            raise RuntimeError(f"Daily YouTube upload quota exhausted ({quota_status.used_today}/{quota_status.limit})")

        # Prepare metadata
        if not title:
            title = f"{product.title} - ₹{product.price} | Meesho Fashion"

        if not description:
            description = settings.yt_description_template.format(
                category=product.category.replace("-", " ").title(),
                title=product.title,
                price=product.price,
                original_price=product.original_price,
                discount=product.discount_percent,
                fabric=product.fabric,
                color=product.color,
                product_code=product.product_code,
                affiliate_url=product.affiliate_url
            )

        if not tags:
            tags = settings.yt_tags

        if not privacy_status:
            privacy_status = settings.yt_privacy_status

        if not category_id:
            category_id = settings.yt_category_id

        body = {
            "snippet": {
                "title": title[:100],  # YouTube title limit
                "description": description[:5000],  # YouTube description limit
                "tags": tags[:500],  # YouTube tags limit
                "categoryId": str(category_id),
                "defaultLanguage": "te",
                "defaultAudioLanguage": "te"
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        # Resumable upload
        media = MediaFileUpload(
            video_path,
            chunksize=1024 * 1024,  # 1MB chunks
            resumable=True,
            mimetype="video/mp4"
        )

        logger.info(f"Starting YouTube upload: {video_path}")

        try:
            request = self.youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            error = None
            retry = 0

            while response is None:
                try:
                    status, response = request.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        logger.info(f"Upload progress: {progress}%")
                except HttpError as e:
                    error = e
                    if e.resp.status in [500, 502, 503, 504]:
                        # Retryable error
                        retry += 1
                        if retry > 3:
                            raise
                        wait_time = 2 ** retry
                        logger.warning(f"Retryable error, waiting {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        raise
                except Exception as e:
                    error = e
                    retry += 1
                    if retry > 3:
                        raise
                    wait_time = 2 ** retry
                    logger.warning(f"Error, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)

            video_id = response.get("id")
            video_url = f"https://www.youtube.com/watch?v={video_id}"

            # Increment quota
            quota_date = datetime.now().date().isoformat()
            increment_daily_quota(quota_date, "youtube_uploads")
            increment_daily_quota(
                quota_date, "youtube_quota", settings.yt_quota_per_upload
            )

            logger.info(f"YouTube upload successful: {video_url}")

            return UploadResult(
                video_id=video_id,
                video_url=video_url,
                upload_time=datetime.now(),
                quota_used=settings.yt_quota_per_upload
            )

        except HttpError as e:
            logger.error(f"YouTube API error: {e}")
            raise RuntimeError(f"YouTube upload failed: {e}")
        except Exception as e:
            logger.error(f"Upload error: {e}")
            raise

    async def delete_video(self, video_id: str) -> bool:
        """Delete a video by ID."""
        if not self.youtube:
            await self.authenticate()

        try:
            self.youtube.videos().delete(id=video_id).execute()
            logger.info(f"Deleted YouTube video: {video_id}")
            return True
        except HttpError as e:
            logger.error(f"Failed to delete video {video_id}: {e}")
            return False

    async def get_video_status(self, video_id: str) -> Optional[dict]:
        """Get video status and details."""
        if not self.youtube:
            await self.authenticate()

        try:
            response = self.youtube.videos().list(
                part="snippet,status,statistics",
                id=video_id
            ).execute()

            items = response.get("items", [])
            return items[0] if items else None
        except HttpError as e:
            logger.error(f"Failed to get video status: {e}")
            return None


# =============================================================================
# Convenience Functions
# =============================================================================

async def upload_to_youtube(
    video_path: str,
    product: Product,
    **kwargs
) -> UploadResult:
    """Convenience function to upload video to YouTube."""
    uploader = YouTubeUploader()
    return await uploader.upload_video(video_path, product, **kwargs)


async def check_youtube_quota() -> QuotaStatus:
    """Check YouTube quota status."""
    uploader = YouTubeUploader()
    return await uploader.check_quota()
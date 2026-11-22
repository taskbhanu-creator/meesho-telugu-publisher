"""
Instagram Reels uploader using the configured Meta Graph API version.
Implements 3-step process: create container -> poll FINISHED -> publish.
"""
import asyncio
import json
import re
import httpx
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.config import settings
from src.database import (
    Product,
    get_ig_dm_log,
    get_product_by_id,
    get_product_for_media,
    log_dm_sent,
    update_ig_dm_log,
)
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class InstagramUploadResult:
    """Result of Instagram Reels upload."""
    media_id: str
    media_url: str
    permalink: str
    upload_time: datetime


@dataclass
class InstagramContainerStatus:
    """Container status from Meta Graph API."""
    status_code: str  # EXPIRED, ERROR, FINISHED, IN_PROGRESS, PUBLISHED
    status: str
    media_id: Optional[str] = None
    error_message: Optional[str] = None


# =============================================================================
# Instagram Uploader
# =============================================================================

class InstagramUploader:
    """Meta Graph API uploader for Instagram Reels."""

    BASE_URL = f"https://graph.facebook.com/{settings.meta_graph_api_version}"

    def __init__(self):
        self.access_token = settings.meta_page_access_token
        self.ig_user_id = settings.meta_ig_user_id
        self.client = httpx.AsyncClient(timeout=120.0)

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    def _get_headers(self) -> dict:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    async def check_quota(self) -> dict:
        """Check daily Instagram upload quota."""
        from src.database import get_daily_quota
        today = datetime.now().date().isoformat()
        quota = get_daily_quota(today, "ig_uploads")

        used = quota.instagram_uploads if quota else 0
        limit = settings.max_ig_uploads_per_day
        remaining = max(0, limit - used)

        return {
            "used_today": used,
            "limit": limit,
            "remaining": remaining,
            "can_upload": remaining > 0
        }

    async def create_container(
        self,
        video_url: str,
        caption: str,
        media_type: str = "REELS",
        share_to_feed: bool = False
    ) -> str:
        """
        Step 1: Create media container.
        Returns container ID.
        """
        url = f"{self.BASE_URL}/{self.ig_user_id}/media"

        data = {
            "media_type": media_type,
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": self.access_token
        }

        logger.info("Creating Instagram media container...")
        response = await self.client.post(url, data=data)
        response.raise_for_status()

        result = response.json()
        container_id = result.get("id")

        if not container_id:
            raise RuntimeError(f"Failed to create container: {result}")

        logger.info(f"Container created: {container_id}")
        return container_id

    async def poll_container_status(self, container_id: str) -> InstagramContainerStatus:
        """
        Step 2: Poll container status until FINISHED or ERROR.
        """
        url = f"{self.BASE_URL}/{container_id}"

        params = {
            "fields": "status_code,status",
            "access_token": self.access_token
        }

        response = await self.client.get(url, params=params)
        response.raise_for_status()

        result = response.json()
        return InstagramContainerStatus(
            status_code=result.get("status_code", "UNKNOWN"),
            status=result.get("status", "UNKNOWN"),
            media_id=result.get("id"),
            error_message=result.get("error_message")
        )

    async def wait_for_container(
        self,
        container_id: str,
        max_wait_seconds: int = 300,
        poll_interval: int = 5
    ) -> InstagramContainerStatus:
        """
        Wait for container to reach FINISHED status.
        """
        elapsed = 0
        while elapsed < max_wait_seconds:
            status = await self.poll_container_status(container_id)
            logger.info(f"Container {container_id} status: {status.status_code}")

            if status.status_code == "FINISHED":
                return status
            elif status.status_code in ["ERROR", "EXPIRED"]:
                raise RuntimeError(
                    f"Container {container_id} failed: {status.status} - {status.error_message}"
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"Container {container_id} did not finish within {max_wait_seconds}s")

    async def publish_container(self, container_id: str) -> dict:
        """
        Step 3: Publish the finished container.
        """
        url = f"{self.BASE_URL}/{self.ig_user_id}/media_publish"

        data = {
            "creation_id": container_id,
            "access_token": self.access_token
        }

        logger.info(f"Publishing container: {container_id}")
        response = await self.client.post(url, data=data)
        response.raise_for_status()

        result = response.json()
        logger.info(f"Published: {result}")
        return result

    async def upload_reel(
        self,
        video_url: str,
        product: Product,
        caption: Optional[str] = None,
        media_type: str = "REELS",
        share_to_feed: bool = False
    ) -> InstagramUploadResult:
        """
        Complete 3-step upload process:
        1. Create container
        2. Poll for FINISHED
        3. Publish
        """
        # Check quota
        quota = await self.check_quota()
        if not quota["can_upload"]:
            raise RuntimeError(f"Daily Instagram upload quota exhausted ({quota['used_today']}/{quota['limit']})")

        # Prepare caption
        if not caption:
            caption = settings.ig_caption_template.format(
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

        # Step 1: Create container
        container_id = await self.create_container(
            video_url=video_url,
            caption=caption,
            media_type=media_type,
            share_to_feed=share_to_feed
        )

        # Step 2: Wait for FINISHED
        status = await self.wait_for_container(container_id)

        # Step 3: Publish
        publish_result = await self.publish_container(container_id)

        media_id = publish_result.get("id")
        if not media_id:
            raise RuntimeError(f"Publish failed: {publish_result}")

        # Get permalink
        media_info = await self.get_media_info(media_id)
        permalink = media_info.get("permalink", f"https://www.instagram.com/p/{media_id}/")

        # Increment quota
        from src.database import increment_daily_quota
        increment_daily_quota(datetime.now().date().isoformat(), "ig_uploads")

        logger.info(f"Instagram Reel published: {permalink}")

        return InstagramUploadResult(
            media_id=media_id,
            media_url=video_url,
            permalink=permalink,
            upload_time=datetime.now()
        )

    async def get_media_info(self, media_id: str) -> dict:
        """Get media info including permalink."""
        url = f"{self.BASE_URL}/{media_id}"

        params = {
            "fields": "id,permalink,media_type,media_url,timestamp",
            "access_token": self.access_token
        }

        response = await self.client.get(url, params=params)
        response.raise_for_status()

        return response.json()

    async def delete_media(self, media_id: str) -> bool:
        """Delete media by ID."""
        url = f"{self.BASE_URL}/{media_id}"

        params = {"access_token": self.access_token}

        try:
            response = await self.client.delete(url, params=params)
            response.raise_for_status()
            logger.info(f"Deleted Instagram media: {media_id}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to delete media {media_id}: {e}")
            return False

    async def reply_to_comment(self, comment_id: str, message: str) -> bool:
        """Reply to a comment (for DM automation)."""
        url = f"{self.BASE_URL}/{comment_id}/replies"

        data = {
            "message": message,
            "access_token": self.access_token
        }

        try:
            response = await self.client.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Replied to comment {comment_id}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to reply to comment: {e}")
            return False

    async def send_dm(self, recipient_id: str, message: str) -> bool:
        """Send DM to user (requires Instagram Messaging API)."""
        url = f"{self.BASE_URL}/{self.ig_user_id}/messages"

        data = {
            "recipient": {"id": recipient_id},
            "message": {"text": message},
            "access_token": self.access_token
        }

        try:
            response = await self.client.post(url, json=data)
            response.raise_for_status()
            logger.info(f"Sent DM to {recipient_id}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to send DM: {e}")
            return False

    async def send_private_reply(self, comment_id: str, message: str) -> bool:
        """Send the one-time private reply allowed for a comment."""
        if not settings.meta_page_id:
            raise RuntimeError("META_PAGE_ID is required to send Instagram private replies")

        url = f"{self.BASE_URL}/{settings.meta_page_id}/messages"
        data = {
            "recipient": json.dumps({"comment_id": comment_id}),
            "message": json.dumps({"text": message}),
            "access_token": self.access_token,
        }

        try:
            response = await self.client.post(url, data=data)
            response.raise_for_status()
            logger.info(f"Sent private reply for Instagram comment {comment_id}")
            return True
        except httpx.HTTPError as error:
            logger.error(f"Failed to send private reply for comment {comment_id}: {error}")
            return False

# =============================================================================
# Webhook Handler for Instagram
# =============================================================================

class InstagramWebhookHandler:
    """Handles Instagram webhook events (comments, DMs)."""

    def __init__(self):
        pass

    @property
    def verify_token(self) -> str:
        """Get verify token from settings (lazy read)."""
        from src.config import settings
        return settings.meta_webhook_verify_token

    def verify_webhook(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """Verify webhook subscription."""
        if mode == "subscribe" and token == self.verify_token:
            logger.info("Webhook verified successfully")
            return challenge
        logger.warning("Webhook verification failed")
        return None

    async def handle_comment(self, comment_data: dict) -> dict:
        """Handle new comment on Instagram media."""
        comment_id = comment_data.get("id")
        text = comment_data.get("text", "").strip()
        media_id = comment_data.get("media", {}).get("id")
        from_user = comment_data.get("from", {})
        username = from_user.get("username") or "unknown"

        logger.info(f"New Instagram comment from {username}: {text}")

        if not re.search(r"(?<!\w)LINK(?!\w)", text, re.IGNORECASE):
            return {"action": "ignored", "keyword": text}
        if not comment_id or not media_id:
            logger.warning("Cannot process LINK comment without comment and media IDs")
            return {"action": "invalid"}
        if get_ig_dm_log(comment_id):
            logger.info(f"Comment {comment_id} was already processed")
            return {"action": "duplicate"}

        product_id = get_product_for_media(media_id)
        product = get_product_by_id(product_id) if product_id else None
        if not product:
            logger.warning(f"No product is associated with Instagram media {media_id}")
            return {"action": "unmapped"}

        claimed = log_dm_sent(
            comment_id=comment_id,
            media_id=media_id,
            username=username,
            comment_text=text,
            product_id=product.meesho_product_id,
            success=False,
            error="Private reply pending",
        )
        if not claimed:
            logger.info(f"Comment {comment_id} was concurrently claimed")
            return {"action": "duplicate"}

        message = f"ఇదిగో మీ ఉత్పత్తి లింక్: {product.affiliate_url}"
        try:
            sent = await send_instagram_private_reply(comment_id, message)
        except httpx.HTTPError as error:
            if not update_ig_dm_log(comment_id, False, str(error)):
                raise RuntimeError(f"Could not record DM failure for comment {comment_id}") from error
            logger.error(f"Instagram private reply failed for {comment_id}: {error}")
            return {"action": "failed"}

        updated = update_ig_dm_log(
            comment_id,
            sent,
            None if sent else "Meta API did not accept the private reply",
        )
        if not updated:
            raise RuntimeError(f"Could not record DM result for comment {comment_id}")
        return {"action": "dm_sent" if sent else "failed", "product_id": product.meesho_product_id}


    async def handle_dm(self, dm_data: dict) -> dict:
        """Handle incoming DM."""
        # For future expansion
        logger.info(f"Received DM: {dm_data}")
        return {"action": "logged"}


# =============================================================================
# Convenience Functions
# =============================================================================

async def upload_to_instagram(
    video_url: str,
    product: Product,
    **kwargs
) -> InstagramUploadResult:
    """Convenience function to upload Reel to Instagram."""
    uploader = InstagramUploader()
    try:
        return await uploader.upload_reel(video_url, product, **kwargs)
    finally:
        await uploader.close()


async def send_instagram_private_reply(comment_id: str, message: str) -> bool:
    """Send a one-time private reply to an Instagram comment."""
    uploader = InstagramUploader()
    try:
        return await uploader.send_private_reply(comment_id, message)
    finally:
        await uploader.close()


async def check_instagram_quota() -> dict:
    """Check Instagram quota status."""
    uploader = InstagramUploader()
    try:
        return await uploader.check_quota()
    finally:
        await uploader.close()
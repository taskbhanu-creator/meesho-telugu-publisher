"""Shared Cloudflare R2 S3-compatible client configuration."""
import boto3

from src.config import settings


def create_r2_client(for_state: bool = False):
    """Create an authenticated S3 client for the configured R2 account."""
    access_key = settings.r2_state_access_key_id if for_state else settings.r2_access_key_id
    secret_key = settings.r2_state_secret_access_key if for_state else settings.r2_secret_access_key
    access_key_name = "R2_STATE_ACCESS_KEY_ID" if for_state else "R2_ACCESS_KEY_ID"
    secret_key_name = "R2_STATE_SECRET_ACCESS_KEY" if for_state else "R2_SECRET_ACCESS_KEY"
    required = {
        "R2_ACCOUNT_ID": settings.r2_account_id,
        access_key_name: access_key,
        secret_key_name: secret_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"R2 is not configured; missing: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

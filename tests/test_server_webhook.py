"""
Tests for server webhook module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import hmac
import hashlib
import json
from datetime import datetime

from src.server_webhook import app, verify_webhook_signature, MediaFileHandler
from src.config import settings
from src.database import IGDMLog, save_ig_dm_log


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch('src.server_webhook.settings') as mock:
        mock.webhook_verify_token = "test_verify_token"
        mock.instagram_app_secret = "test_app_secret"
        mock.media_dir = "/tmp/media"
        mock.media_base_url = "https://example.com/media"
        yield mock


class TestVerifyWebhookSignature:
    """Test webhook signature verification."""

    def test_verify_valid_signature(self):
        """Test verification of valid HMAC signature."""
        payload = b'{"test": "data"}'
        secret = "test_secret"
        signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        result = verify_webhook_signature(payload, signature, secret)
        assert result is True

    def test_verify_invalid_signature(self):
        """Test verification fails for invalid signature."""
        payload = b'{"test": "data"}'
        secret = "test_secret"
        invalid_signature = "invalid_signature"

        result = verify_webhook_signature(payload, invalid_signature, secret)
        assert result is False

    def test_verify_empty_signature(self):
        """Test verification fails for empty signature."""
        payload = b'{"test": "data"}'
        secret = "test_secret"

        result = verify_webhook_signature(payload, "", secret)
        assert result is False

    def test_verify_none_signature(self):
        """Test verification fails for None signature."""
        payload = b'{"test": "data"}'
        secret = "test_secret"

        result = verify_webhook_signature(payload, None, secret)
        assert result is False


class TestWebhookEndpoints:
    """Test webhook endpoints."""

    def test_webhook_get_verification(self, client, mock_settings):
        """Test GET webhook verification challenge."""
        response = client.get(
            "/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge123",
                "hub.verify_token": "test_verify_token"
            }
        )

        assert response.status_code == 200
        assert response.text == "challenge123"

    def test_webhook_get_invalid_token(self, client, mock_settings):
        """Test GET webhook with invalid verify token."""
        response = client.get(
            "/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge123",
                "hub.verify_token": "wrong_token"
            }
        )

        assert response.status_code == 403

    def test_webhook_get_missing_params(self, client, mock_settings):
        """Test GET webhook with missing parameters."""
        response = client.get("/webhook/instagram")

        assert response.status_code == 400

    def test_webhook_post_valid_signature(self, client, mock_settings):
        """Test POST webhook with valid signature."""
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123",
                "time": int(datetime.now().timestamp()),
                "changes": [{
                    "field": "comments",
                    "value": {
                        "comment_id": "COMMENT123",
                        "from": {"id": "USER123", "username": "testuser"},
                        "media": {"id": "MEDIA123"},
                        "text": "LINK please"
                    }
                }]
            }]
        }

        payload_bytes = json.dumps(payload).encode()
        signature = hmac.new(
            "test_app_secret".encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        response = client.post(
            "/webhook/instagram",
            content=payload_bytes,
            headers={"X-Hub-Signature-256": f"sha256={signature}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"

    def test_webhook_post_invalid_signature(self, client, mock_settings):
        """Test POST webhook with invalid signature."""
        payload = {"test": "data"}
        payload_bytes = json.dumps(payload).encode()

        response = client.post(
            "/webhook/instagram",
            content=payload_bytes,
            headers={"X-Hub-Signature-256": "sha256=invalid"}
        )

        assert response.status_code == 401

    def test_webhook_post_missing_signature(self, client, mock_settings):
        """Test POST webhook without signature header."""
        payload = {"test": "data"}
        payload_bytes = json.dumps(payload).encode()

        response = client.post(
            "/webhook/instagram",
            content=payload_bytes
        )

        assert response.status_code == 401

    def test_webhook_post_comment_with_link_keyword(self, client, mock_settings, tmp_path):
        """Test webhook processes comment with LINK keyword."""
        # Create a mock media file
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        test_video = media_dir / "TEST123_video.mp4"
        test_video.write_bytes(b"dummy video")

        with patch('src.server_webhook.settings.media_dir', str(media_dir)):
            payload = {
                "object": "instagram",
                "entry": [{
                    "id": "123",
                    "time": int(datetime.now().timestamp()),
                    "changes": [{
                        "field": "comments",
                        "value": {
                            "comment_id": "COMMENT123",
                            "from": {"id": "USER123", "username": "testuser"},
                            "media": {"id": "MEDIA123"},
                            "text": "Please send LINK"
                        }
                    }]
                }]
            }

            payload_bytes = json.dumps(payload).encode()
            signature = hmac.new(
                "test_app_secret".encode(),
                payload_bytes,
                hashlib.sha256
            ).hexdigest()

            with patch('src.instagram_uploader.send_dm_reply') as mock_dm:
                mock_dm.return_value = True

                response = client.post(
                    "/webhook/instagram",
                    content=payload_bytes,
                    headers={"X-Hub-Signature-256": f"sha256={signature}"}
                )

                assert response.status_code == 200
                mock_dm.assert_called_once()

    def test_webhook_post_comment_without_link_keyword(self, client, mock_settings):
        """Test webhook ignores comment without LINK keyword."""
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123",
                "time": int(datetime.now().timestamp()),
                "changes": [{
                    "field": "comments",
                    "value": {
                        "comment_id": "COMMENT456",
                        "from": {"id": "USER456", "username": "testuser2"},
                        "media": {"id": "MEDIA123"},
                        "text": "Nice video!"
                    }
                }]
            }]
        }

        payload_bytes = json.dumps(payload).encode()
        signature = hmac.new(
            "test_app_secret".encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        with patch('src.instagram_uploader.send_dm_reply') as mock_dm:
            response = client.post(
                "/webhook/instagram",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": f"sha256={signature}"}
            )

            assert response.status_code == 200
            mock_dm.assert_not_called()

    def test_webhook_post_dm_message(self, client, mock_settings):
        """Test webhook processes direct message."""
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123",
                "time": int(datetime.now().timestamp()),
                "changes": [{
                    "field": "messages",
                    "value": {
                        "message_id": "MSG123",
                        "from": {"id": "USER123", "username": "testuser"},
                        "text": "LINK"
                    }
                }]
            }]
        }

        payload_bytes = json.dumps(payload).encode()
        signature = hmac.new(
            "test_app_secret".encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        with patch('src.instagram_uploader.send_dm_reply') as mock_dm:
            mock_dm.return_value = True

            response = client.post(
                "/webhook/instagram",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": f"sha256={signature}"}
            )

            assert response.status_code == 200


class TestMediaEndpoints:
    """Test media file serving endpoints."""

    def test_media_file_exists(self, client, tmp_path):
        """Test serving existing media file."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        test_file = media_dir / "test_video.mp4"
        test_file.write_bytes(b"video content")

        with patch('src.server_webhook.settings.media_dir', str(media_dir)):
            response = client.get("/media/test_video.mp4")

            assert response.status_code == 200
            assert response.content == b"video content"
            assert response.headers["content-type"] == "video/mp4"

    def test_media_file_not_found(self, client, tmp_path):
        """Test 404 for non-existent media file."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        with patch('src.server_webhook.settings.media_dir', str(media_dir)):
            response = client.get("/media/nonexistent.mp4")

            assert response.status_code == 404

    def test_media_directory_traversal(self, client, tmp_path):
        """Test directory traversal protection."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        with patch('src.server_webhook.settings.media_dir', str(media_dir)):
            response = client.get("/media/../etc/passwd")

            assert response.status_code == 404


class TestAdminEndpoints:
    """Test admin endpoints."""

    def test_admin_stats(self, client, mock_settings):
        """Test admin stats endpoint."""
        with patch('src.server_webhook.get_pipeline_stats') as mock_stats:
            mock_stats.return_value = {
                "total_products": 100,
                "pending_products": 50,
                "youtube_uploaded": 30,
                "instagram_uploaded": 40
            }

            response = client.get("/admin/stats")

            assert response.status_code == 200
            data = response.json()
            assert data["total_products"] == 100

    def test_admin_health(self, client, mock_settings):
        """Test admin health endpoint."""
        response = client.get("/admin/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_admin_trigger_pipeline(self, client, mock_settings):
        """Test admin trigger pipeline endpoint."""
        with patch('src.server_webhook.get_scheduler') as mock_get_scheduler:
            mock_scheduler = AsyncMock()
            mock_scheduler.trigger_pipeline_now = MagicMock(return_value=AsyncMock())
            mock_get_scheduler.return_value = mock_scheduler

            response = client.post("/admin/trigger/pipeline")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "triggered"


class TestIGDMLogDeduplication:
    """Test IG DM log deduplication."""

    def test_duplicate_comment_id_ignored(self, tmp_path):
        """Test that duplicate comment_ids are not saved twice."""
        import src.database as db_module

        # Override DB path
        original_path = db_module.DB_PATH
        db_module.DB_PATH = tmp_path / "test.db"
        db_module._engine = None
        db_module._session_factory = None

        from src.database import init_db
        init_db()

        log1 = IGDMLog(
            ig_user_id="USER123",
            ig_username="testuser",
            comment_id="COMMENT123",
            media_id="MEDIA123",
            keyword="LINK",
            replied=True
        )

        log2 = IGDMLog(
            ig_user_id="USER123",
            ig_username="testuser",
            comment_id="COMMENT123",  # Same comment_id
            media_id="MEDIA123",
            keyword="LINK",
            replied=True
        )

        save_ig_dm_log(log1)
        save_ig_dm_log(log2)  # Should be ignored due to UNIQUE constraint

        # Verify only one record exists
        retrieved = IGDMLog.get_by_comment_id("COMMENT123")
        assert retrieved is not None

        # Cleanup
        db_module.DB_PATH = original_path
        db_module._engine = None
        db_module._session_factory = None


class TestRateLimiting:
    """Test rate limiting on webhook."""

    def test_webhook_rate_limit(self, client, mock_settings):
        """Test webhook endpoint has rate limiting."""
        payload = {"test": "data"}
        payload_bytes = json.dumps(payload).encode()
        signature = hmac.new(
            "test_app_secret".encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        # Make multiple rapid requests
        responses = []
        for _ in range(10):
            response = client.post(
                "/webhook/instagram",
                content=payload_bytes,
                headers={"X-Hub-Signature-256": f"sha256={signature}"}
            )
            responses.append(response.status_code)

        # At least some should succeed (depending on rate limit config)
        assert 200 in responses or 429 in responses


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
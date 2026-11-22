import asyncio
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from src import instagram_uploader, server_webhook
from src.database import (
    Product,
    get_ig_dm_log,
    init_db,
    mark_instagram_published,
    save_product,
)


@pytest.fixture(autouse=True)
def restore_database_instance():
    import src.database as db_module

    original_instance = db_module._db_instance
    yield
    db_module._db_instance = original_instance


def create_published_product(tmp_path):
    init_db(tmp_path / "pipeline.db")
    product = Product(
        meesho_product_id="WEBHOOK1",
        title="Cotton Kurti",
        category="kurtis",
        price=299,
        original_price=599,
        discount_percent=50,
        rating=4.5,
        review_count=20,
        image_urls=[],
        affiliate_url="https://meesho.com/product/WEBHOOK1",
        product_code="KUR-WEBHOOK1",
        fabric="Cotton",
        color="Blue",
        size_options=["S", "M", "L"],
        in_stock=True,
    )
    save_product(product)
    mark_instagram_published("WEBHOOK1", "MEDIA1", "https://instagram.com/p/MEDIA1")
    return product


def test_link_comment_sends_private_reply_once(tmp_path, monkeypatch):
    product = create_published_product(tmp_path)
    sent = []

    async def send_reply(comment_id, message):
        sent.append((comment_id, message))
        return True

    monkeypatch.setattr(instagram_uploader, "send_instagram_private_reply", send_reply)
    event = {
        "id": "COMMENT1",
        "text": "Please send link",
        "media": {"id": "MEDIA1"},
        "from": {"username": "shopper"},
    }
    handler = instagram_uploader.InstagramWebhookHandler()

    first = asyncio.run(handler.handle_comment(event))
    duplicate = asyncio.run(handler.handle_comment(event))

    assert first["action"] == "dm_sent"
    assert duplicate["action"] == "duplicate"
    assert len(sent) == 1
    assert product.affiliate_url in sent[0][1]
    log = get_ig_dm_log("COMMENT1")
    assert log.dm_sent is True
    assert log.product_id == product.meesho_product_id


def test_instagram_webhook_rejects_missing_signature(monkeypatch):
    monkeypatch.setattr(server_webhook.settings, "meta_app_secret", "app-secret")

    response = TestClient(server_webhook.app).post(
        "/webhook/instagram",
        content=b'{"object":"instagram","entry":[]}',
    )

    assert response.status_code == 401


def test_instagram_webhook_accepts_valid_signature(monkeypatch):
    secret = "app-secret"
    payload = b'{"object":"instagram","entry":[]}'
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    monkeypatch.setattr(server_webhook.settings, "meta_app_secret", secret)
    monkeypatch.setattr(server_webhook.settings, "r2_state_bucket_name", "")

    response = TestClient(server_webhook.app).post(
        "/webhook/instagram",
        content=payload,
        headers={"X-Hub-Signature-256": f"sha256={signature}"},
    )

    assert response.status_code == 200


def test_instagram_webhook_persists_state_when_configured(monkeypatch):
    secret = "app-secret"
    payload = b'{"object":"instagram","entry":[]}'
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    persisted = []
    monkeypatch.setattr(server_webhook.settings, "meta_app_secret", secret)
    monkeypatch.setattr(server_webhook.settings, "r2_state_bucket_name", "private-state")
    monkeypatch.setattr(
        "src.state_storage.upload_database_to_r2",
        lambda: persisted.append(True),
    )

    response = TestClient(server_webhook.app).post(
        "/webhook/instagram",
        content=payload,
        headers={"X-Hub-Signature-256": f"sha256={signature}"},
    )

    assert response.status_code == 200
    assert persisted == [True]


def test_instagram_webhook_verification_uses_meta_query_parameter_names(monkeypatch):
    monkeypatch.setattr(server_webhook.settings, "meta_webhook_verify_token", "verify-me")

    response = TestClient(server_webhook.app).get(
        "/webhook/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "challenge",
            "hub.verify_token": "verify-me",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge"


def test_admin_state_reload_requires_admin_token(monkeypatch):
    monkeypatch.setattr(server_webhook.settings, "admin_api_token", "admin-secret")

    response = TestClient(server_webhook.app).post("/admin/state/reload")

    assert response.status_code == 401


def test_admin_state_reload_loads_shared_database(monkeypatch):
    monkeypatch.setattr(server_webhook.settings, "admin_api_token", "admin-secret")
    monkeypatch.setattr(server_webhook.settings, "r2_state_bucket_name", "private-state")

    def restore():
        return True

    monkeypatch.setattr("src.state_storage.restore_database_from_r2", restore)
    response = TestClient(server_webhook.app).post(
        "/admin/state/reload",
        headers={"X-Admin-Token": "admin-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reloaded"

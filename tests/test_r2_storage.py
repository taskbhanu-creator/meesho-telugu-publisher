import pytest

from src import media_storage


def test_upload_video_to_r2_returns_public_url(tmp_path, monkeypatch):
    video_path = tmp_path / "sample clip.mp4"
    video_path.write_bytes(b"video")
    uploaded = {}

    class Client:
        def upload_file(self, filename, bucket, key, ExtraArgs):
            uploaded.update(filename=filename, bucket=bucket, key=key, args=ExtraArgs)

    monkeypatch.setattr(media_storage, "create_r2_client", lambda: Client())
    monkeypatch.setattr(media_storage.settings, "r2_account_id", "account")
    monkeypatch.setattr(media_storage.settings, "r2_access_key_id", "access")
    monkeypatch.setattr(media_storage.settings, "r2_secret_access_key", "secret")
    monkeypatch.setattr(media_storage.settings, "r2_bucket_name", "media")
    monkeypatch.setattr(media_storage.settings, "r2_public_base_url", "https://cdn.example.com/")
    monkeypatch.setattr(media_storage.settings, "r2_object_prefix", "videos")

    url = media_storage._upload_video_to_r2(str(video_path))

    assert url == "https://cdn.example.com/videos/sample%20clip.mp4"
    assert uploaded["bucket"] == "media"
    assert uploaded["key"] == "videos/sample clip.mp4"
    assert uploaded["args"]["ContentType"] == "video/mp4"


def test_upload_video_to_r2_rejects_missing_settings(tmp_path, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(media_storage.settings, "r2_account_id", "")

    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID"):
        media_storage._upload_video_to_r2(str(video_path))

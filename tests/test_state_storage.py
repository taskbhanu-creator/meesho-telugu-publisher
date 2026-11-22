import shutil
import sqlite3

import pytest
from botocore.exceptions import ClientError

from src import state_storage
from src.database import init_db


@pytest.fixture(autouse=True)
def restore_database_instance():
    import src.database as db_module

    original_instance = db_module._db_instance
    yield
    db_module._db_instance = original_instance


def configure_state(monkeypatch, database_path):
    monkeypatch.setattr(state_storage.settings, "database_path", database_path)
    monkeypatch.setattr(state_storage.settings, "r2_bucket_name", "public-media")
    monkeypatch.setattr(state_storage.settings, "r2_state_bucket_name", "private-state")
    monkeypatch.setattr(state_storage.settings, "r2_state_object_key", "pipeline.db")


def test_restore_initializes_database_when_remote_snapshot_is_missing(tmp_path, monkeypatch):
    configure_state(monkeypatch, tmp_path / "data" / "pipeline.db")

    class Client:
        def head_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )

    monkeypatch.setattr(state_storage, "create_r2_client", lambda **kwargs: Client())

    assert state_storage.restore_database_from_r2() is False
    assert state_storage.settings.database_path.is_file()


def test_restore_replaces_database_from_valid_snapshot(tmp_path, monkeypatch):
    remote_db = tmp_path / "remote.db"
    with sqlite3.connect(remote_db) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('restored')")

    configure_state(monkeypatch, tmp_path / "data" / "pipeline.db")

    class Client:
        def head_object(self, **kwargs):
            return {}

        def download_file(self, bucket, key, destination):
            shutil.copyfile(remote_db, destination)

    monkeypatch.setattr(state_storage, "create_r2_client", lambda **kwargs: Client())

    assert state_storage.restore_database_from_r2() is True
    with sqlite3.connect(state_storage.settings.database_path) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "restored"


def test_upload_uses_consistent_sqlite_snapshot(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "pipeline.db"
    init_db(database_path)
    configure_state(monkeypatch, database_path)
    uploaded = {}

    class Client:
        def upload_file(self, filename, bucket, key, ExtraArgs):
            connection = sqlite3.connect(filename)
            try:
                uploaded["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            uploaded.update(bucket=bucket, key=key, args=ExtraArgs)

    monkeypatch.setattr(state_storage, "create_r2_client", lambda **kwargs: Client())

    state_storage.upload_database_to_r2()

    assert uploaded["integrity"] == "ok"
    assert uploaded["bucket"] == "private-state"
    assert uploaded["key"] == "pipeline.db"


def test_state_bucket_must_not_be_public_media_bucket(tmp_path, monkeypatch):
    configure_state(monkeypatch, tmp_path / "pipeline.db")
    monkeypatch.setattr(state_storage.settings, "r2_state_bucket_name", "public-media")

    with pytest.raises(ValueError, match="separate private bucket"):
        state_storage._state_location()

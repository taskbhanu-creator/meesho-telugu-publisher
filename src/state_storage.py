"""Private R2 persistence for the SQLite pipeline database."""
import os
import sqlite3
import tempfile
from pathlib import Path

from botocore.exceptions import ClientError

from src.config import settings
from src.database import init_db
from src.r2_client import create_r2_client


def _state_location() -> tuple[str, str]:
    bucket = settings.r2_state_bucket_name
    object_key = settings.r2_state_object_key.strip("/")
    if not bucket:
        raise RuntimeError("R2_STATE_BUCKET_NAME is required for persistent pipeline state")
    if not object_key or any(part in {".", ".."} for part in object_key.split("/")):
        raise ValueError("R2_STATE_OBJECT_KEY must be a non-empty object key without dot segments")
    if bucket == settings.r2_bucket_name:
        raise ValueError("Pipeline state must use a separate private bucket from public media")
    return bucket, object_key


def _is_missing_object(error: ClientError) -> bool:
    response = error.response
    code = response.get("Error", {}).get("Code")
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


def restore_database_from_r2() -> bool:
    """Restore the database snapshot; initialize a fresh database on first run."""
    bucket, object_key = _state_location()
    client = create_r2_client(for_state=True)
    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        client.head_object(Bucket=bucket, Key=object_key)
    except ClientError as error:
        if not _is_missing_object(error):
            raise
        init_db(database_path)
        return False

    fd, temp_name = tempfile.mkstemp(prefix="pipeline-restore-", suffix=".db", dir=database_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        client.download_file(bucket, object_key, str(temp_path))
        connection = sqlite3.connect(temp_path)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Downloaded pipeline database failed integrity check: {result}")
        os.replace(temp_path, database_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    init_db(database_path)
    return True


def upload_database_to_r2() -> None:
    """Create a consistent SQLite snapshot and upload it to the private bucket."""
    bucket, object_key = _state_location()
    database_path = Path(settings.database_path)
    if not database_path.is_file():
        raise FileNotFoundError(f"Pipeline database does not exist: {database_path}")

    client = create_r2_client(for_state=True)
    with tempfile.TemporaryDirectory(prefix="pipeline-state-", dir=database_path.parent) as temp_dir:
        snapshot_path = Path(temp_dir) / "pipeline.db"
        source = sqlite3.connect(database_path)
        snapshot = sqlite3.connect(snapshot_path)
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
            source.close()

        client.upload_file(
            str(snapshot_path),
            bucket,
            object_key,
            ExtraArgs={
                "ContentType": "application/vnd.sqlite3",
                "CacheControl": "no-store",
            },
        )

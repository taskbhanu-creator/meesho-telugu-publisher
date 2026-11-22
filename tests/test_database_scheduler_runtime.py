import asyncio
from datetime import datetime

import pytest

from src.database import (
    PipelineRun,
    get_daily_quota,
    get_pipeline_stats,
    increment_daily_quota,
    init_db,
    save_pipeline_run,
)
from src.scheduler import PipelineResult, PipelineScheduler


@pytest.fixture
def temporary_database(tmp_path):
    import src.database as db_module

    original_instance = db_module._db_instance
    init_db(tmp_path / "pipeline.db")
    yield
    db_module._db_instance = original_instance


def test_daily_quota_fields_increment_independently(temporary_database):
    today = datetime.now().date().isoformat()

    increment_daily_quota(today, "youtube_uploads", 2)
    increment_daily_quota(today, "ig_uploads", 3)
    increment_daily_quota(today, "youtube_quota", 1600)
    quota = get_daily_quota(today, "youtube_uploads")

    assert quota.youtube_uploads == 2
    assert quota.instagram_uploads == 3
    assert quota.youtube_quota_used == 1600


def test_pipeline_run_uses_database_model_and_updates_stats(temporary_database):
    run = PipelineRun(
        status="success",
        products_extracted=2,
        videos_generated=2,
        youtube_uploaded=1,
        instagram_uploaded=1,
    )

    assert save_pipeline_run(run) > 0
    stats = get_pipeline_stats()

    assert stats["total_runs"] == 1
    assert stats["successful_runs"] == 1
    assert stats["total_products_extracted"] == 2
    assert stats["total_videos_generated"] == 2


def test_run_step_awaits_coroutine_returned_by_callable():
    scheduler = PipelineScheduler(dry_run=True)

    async def work():
        return "finished"

    step = asyncio.run(scheduler._run_step("test", lambda: work()))

    assert step.success
    assert step.data == "finished"


def test_finalize_pipeline_run_persists_partial_status(temporary_database):
    scheduler = PipelineScheduler(dry_run=True)
    result = PipelineResult(
        run_id="run-test",
        started_at=datetime.now(),
        completed_at=None,
        success=True,
        products_processed=1,
        youtube_uploaded=0,
        instagram_uploaded=0,
        errors=["Instagram upload failed"],
        steps=[],
        dry_run=True,
        videos_generated=1,
    )

    scheduler._finalize_run(result)
    stats = get_pipeline_stats()

    assert stats["total_runs"] == 1
    assert stats["successful_runs"] == 0

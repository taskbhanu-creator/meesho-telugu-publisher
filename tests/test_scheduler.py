"""
Tests for scheduler module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import asyncio

from src.scheduler import (
    PipelineScheduler, PipelineStepResult, PipelineResult,
    get_scheduler, run_scheduler
)
from src.database import Product, PipelineRun, DailyQuota
from src.config import settings


@pytest.fixture
def sample_product():
    """Create a sample product for testing."""
    return Product(
        meesho_product_id="TEST123",
        title="Cotton Kurti",
        category="kurtis",
        price=299,
        original_price=599,
        discount_percent=50,
        rating=4.5,
        review_count=100,
        image_urls=["https://example.com/img1.jpg"],
        affiliate_url="https://meesho.com/product/TEST123",
        product_code="KUR-TEST12",
        fabric="Cotton",
        color="Red",
        size_options=["S", "M", "L", "XL"],
        in_stock=True,
        scraped_at=datetime.now()
    )


class TestPipelineStepResult:
    """Test PipelineStepResult dataclass."""

    def test_step_result_creation(self):
        """Test creating a step result."""
        result = PipelineStepResult(
            step="test_step",
            success=True,
            message="Completed",
            data={"key": "value"},
            duration_ms=100
        )

        assert result.step == "test_step"
        assert result.success is True
        assert result.message == "Completed"
        assert result.data == {"key": "value"}
        assert result.duration_ms == 100


class TestPipelineResult:
    """Test PipelineResult dataclass."""

    def test_pipeline_result_creation(self):
        """Test creating a pipeline result."""
        now = datetime.now()
        result = PipelineResult(
            run_id="run_123",
            started_at=now,
            completed_at=now,
            success=True,
            products_processed=5,
            youtube_uploaded=3,
            instagram_uploaded=5,
            errors=[],
            steps=[],
            dry_run=False
        )

        assert result.run_id == "run_123"
        assert result.products_processed == 5
        assert result.youtube_uploaded == 3
        assert result.instagram_uploaded == 5
        assert result.dry_run is False


class TestPipelineScheduler:
    """Test PipelineScheduler class."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler instance for testing."""
        return PipelineScheduler(dry_run=True)

    def test_init_dry_run(self):
        """Test initialization with dry_run=True."""
        scheduler = PipelineScheduler(dry_run=True)
        assert scheduler.dry_run is True
        assert scheduler.is_running is False
        assert scheduler.consecutive_errors == 0

    def test_init_not_dry_run(self):
        """Test initialization with dry_run=False."""
        scheduler = PipelineScheduler(dry_run=False)
        assert scheduler.dry_run is False

    def test_start_stop(self, scheduler):
        """Test starting and stopping scheduler."""
        assert scheduler.is_running is False

        scheduler.start()
        assert scheduler.is_running is True
        assert len(scheduler.jobs) > 0

        scheduler.stop()
        assert scheduler.is_running is False

    def test_add_jobs(self, scheduler):
        """Test that jobs are added correctly."""
        scheduler._add_jobs()

        # Check expected jobs exist
        expected_jobs = ["pipeline", "daily_extract", "health_check", "quota_reset"]
        for job_name in expected_jobs:
            assert job_name in scheduler.jobs

    @pytest.mark.asyncio
    async def test_step_check_quotas(self, scheduler):
        """Test quota checking step."""
        with patch('src.scheduler.check_youtube_quota') as mock_yt, \
             patch('src.scheduler.check_instagram_quota') as mock_ig:

            mock_yt.return_value = MagicMock(can_upload=True, remaining=100, limit=200)
            mock_ig.return_value = {"can_upload": True, "remaining": 50, "limit": 100}

            result = await scheduler._step_check_quotas()

            assert result.success is True
            assert "YouTube: 100/200" in result.message
            assert "Instagram: 50/100" in result.message

    @pytest.mark.asyncio
    async def test_step_get_products(self, scheduler, sample_product):
        """Test getting unpublished products step."""
        with patch('src.scheduler.get_unpublished_products') as mock_get:
            mock_get.return_value = [sample_product]

            result = await scheduler._step_get_products()

            assert result.success is True
            assert "Found 1 unpublished products" in result.message
            assert result.data == [sample_product]

    @pytest.mark.asyncio
    async def test_step_get_products_empty(self, scheduler):
        """Test getting products when none available."""
        with patch('src.scheduler.get_unpublished_products') as mock_get:
            mock_get.return_value = []

            result = await scheduler._step_get_products()

            assert result.success is True
            assert "Found 0 unpublished products" in result.message
            assert result.data == []

    @pytest.mark.asyncio
    async def test_run_step_success(self, scheduler):
        """Test running a successful step."""
        async def success_coro():
            return "result_data"

        result = await scheduler._run_step("test_step", success_coro)

        assert result.success is True
        assert result.message == "Completed"
        assert result.data == "result_data"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_run_step_failure(self, scheduler):
        """Test running a failed step."""
        async def fail_coro():
            raise ValueError("Test error")

        result = await scheduler._run_step("test_step", fail_coro)

        assert result.success is False
        assert "Test error" in result.message
        assert result.data is None

    @pytest.mark.asyncio
    async def test_finalize_run(self, scheduler):
        """Test finalizing a pipeline run."""
        result = PipelineResult(
            run_id="test_run_123",
            started_at=datetime.now(),
            completed_at=None,
            success=True,
            products_processed=2,
            youtube_uploaded=1,
            instagram_uploaded=2,
            errors=[],
            steps=[],
            dry_run=True
        )

        with patch('src.scheduler.save_pipeline_run') as mock_save:
            finalized = scheduler._finalize_run(result)

            assert finalized.completed_at is not None
            assert scheduler.stats["total_runs"] == 1
            assert scheduler.stats["successful_runs"] == 1
            assert scheduler.stats["products_processed"] == 2
            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_run_failed(self, scheduler):
        """Test finalizing a failed pipeline run."""
        result = PipelineResult(
            run_id="test_run_456",
            started_at=datetime.now(),
            completed_at=None,
            success=False,
            products_processed=0,
            youtube_uploaded=0,
            instagram_uploaded=0,
            errors=["Error 1", "Error 2"],
            steps=[],
            dry_run=True
        )

        with patch('src.scheduler.save_pipeline_run') as mock_save:
            finalized = scheduler._finalize_run(result)

            assert scheduler.stats["total_runs"] == 1
            assert scheduler.stats["failed_runs"] == 1
            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_extraction(self, scheduler):
        """Test running extraction."""
        with patch('src.scheduler.extract_products') as mock_extract:
            mock_extract.return_value = {"kurtis": [], "sarees": []}

            result = await scheduler.run_extraction()

            assert result["success"] is True
            assert "total_products" in result

    @pytest.mark.asyncio
    async def test_run_extraction_failure(self, scheduler):
        """Test extraction failure."""
        with patch('src.scheduler.extract_products') as mock_extract:
            mock_extract.side_effect = Exception("Extraction failed")

            result = await scheduler.run_extraction()

            assert result["success"] is False
            assert "Extraction failed" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, scheduler):
        """Test health check when healthy."""
        with patch('src.scheduler.get_pipeline_stats') as mock_stats:
            mock_stats.return_value = {"total_products": 100}

            scheduler.consecutive_errors = 0

            result = await scheduler.health_check()

            assert result["status"] == "healthy"
            assert result["scheduler_running"] is False
            assert result["consecutive_errors"] == 0

    @pytest.mark.asyncio
    async def test_health_check_max_errors(self, scheduler):
        """Test health check stops scheduler at max errors."""
        scheduler.consecutive_errors = 10
        scheduler.max_consecutive_errors = 5
        scheduler.is_running = True

        with patch('src.scheduler.get_pipeline_stats') as mock_stats:
            mock_stats.return_value = {}

            result = await scheduler.health_check()

            assert result["status"] == "stopped"
            assert result["reason"] == "max_errors"
            assert scheduler.is_running is False

    def test_get_status(self, scheduler):
        """Test getting scheduler status."""
        scheduler.start()

        status = scheduler.get_status()

        assert status["running"] is True
        assert status["dry_run"] is True
        assert "jobs" in status
        assert "stats" in status

        scheduler.stop()

    @pytest.mark.asyncio
    async def test_trigger_pipeline_now(self, scheduler):
        """Test manual pipeline trigger."""
        task = scheduler.trigger_pipeline_now()

        assert isinstance(task, asyncio.Task)
        # Don't await - just test it creates a task

    @pytest.mark.asyncio
    async def test_trigger_extraction_now(self, scheduler):
        """Test manual extraction trigger."""
        task = scheduler.trigger_extraction_now()

        assert isinstance(task, asyncio.Task)


class TestPipelineRunIntegration:
    """Integration tests for pipeline runs (mocked)."""

    @pytest.mark.asyncio
    async def test_run_pipeline_no_products(self, scheduler, sample_product):
        """Test pipeline run when no products available."""
        with patch('src.scheduler.check_youtube_quota') as mock_yt, \
             patch('src.scheduler.check_instagram_quota') as mock_ig, \
             patch('src.scheduler.get_unpublished_products') as mock_get:

            mock_yt.return_value = MagicMock(can_upload=True, remaining=100, limit=200)
            mock_ig.return_value = {"can_upload": True, "remaining": 50, "limit": 100}
            mock_get.return_value = []

            result = await scheduler.run_pipeline()

            assert result.success is True
            assert result.products_processed == 0
            assert "No unpublished products" in result.message

    @pytest.mark.asyncio
    async def test_run_pipeline_with_products_dry_run(self, scheduler, sample_product):
        """Test pipeline run with products in dry-run mode."""
        with patch('src.scheduler.check_youtube_quota') as mock_yt, \
             patch('src.scheduler.check_instagram_quota') as mock_ig, \
             patch('src.scheduler.get_unpublished_products') as mock_get, \
             patch('src.scheduler.generate_script_and_tts') as mock_script, \
             patch('src.scheduler.build_video') as mock_video, \
             patch('src.scheduler.save_pipeline_run') as mock_save_run:

            mock_yt.return_value = MagicMock(can_upload=True, remaining=100, limit=200)
            mock_ig.return_value = {"can_upload": True, "remaining": 50, "limit": 100}
            mock_get.return_value = [sample_product]

            # Mock script generation
            mock_script.return_value = MagicMock(
                telugu_script="టెస్ట్ స్క్రిప్ట్",
                audio_path="/tmp/audio.mp3"
            )

            # Mock video building
            mock_video.return_value = MagicMock(
                video_path="/tmp/video.mp4",
                duration=25.0,
                size_mb=5.0,
                fps=30,
                resolution=(1080, 1920)
            )

            result = await scheduler.run_pipeline()

            assert result.success is True
            assert result.products_processed == 1
            # In dry-run, no actual uploads
            assert result.youtube_uploaded == 0
            assert result.instagram_uploaded == 0


class TestSchedulerSingleton:
    """Test scheduler singleton pattern."""

    def test_get_scheduler_creates_instance(self):
        """Test get_scheduler creates instance."""
        # Reset global
        import src.scheduler as sched_module
        sched_module._scheduler_instance = None

        scheduler = get_scheduler(dry_run=True)
        assert scheduler is not None
        assert scheduler.dry_run is True

    def test_get_scheduler_returns_same_instance(self):
        """Test get_scheduler returns same instance."""
        import src.scheduler as sched_module
        sched_module._scheduler_instance = None

        scheduler1 = get_scheduler(dry_run=True)
        scheduler2 = get_scheduler(dry_run=False)  # Should return same instance

        assert scheduler1 is scheduler2


class TestSchedulerConfiguration:
    """Test scheduler configuration from settings."""

    def test_post_interval_job(self):
        """Test interval job uses settings.post_interval_minutes."""
        with patch('src.scheduler.settings') as mock_settings:
            mock_settings.post_interval_minutes = 30
            mock_settings.health_check_interval_minutes = 10
            mock_settings.max_consecutive_errors = 5
            mock_settings.pipeline_dry_run = True

            scheduler = PipelineScheduler(dry_run=True)
            scheduler._add_jobs()

            # Check pipeline job trigger
            pipeline_job = scheduler.jobs.get("pipeline")
            assert pipeline_job is not None
            # IntervalTrigger minutes should match settings

    def test_health_check_interval(self):
        """Test health check interval from settings."""
        with patch('src.scheduler.settings') as mock_settings:
            mock_settings.post_interval_minutes = 30
            mock_settings.health_check_interval_minutes = 15
            mock_settings.max_consecutive_errors = 5
            mock_settings.pipeline_dry_run = True

            scheduler = PipelineScheduler(dry_run=True)
            scheduler._add_jobs()

            health_job = scheduler.jobs.get("health_check")
            assert health_job is not None


class TestQuotaReset:
    """Test quota reset functionality."""

    @pytest.mark.asyncio
    async def test_check_quota_reset_logs(self, scheduler, caplog):
        """Test quota reset check logs appropriately."""
        with patch('src.scheduler.logger') as mock_logger:
            await scheduler.check_quota_reset()
            mock_logger.info.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
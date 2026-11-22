"""
Pipeline scheduler using APScheduler.
Manages recurring extraction, generation, and upload jobs with dry-run support.
"""
import asyncio
import inspect
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
from src.database import (
    Product, get_unpublished_products, mark_youtube_published,
    mark_instagram_published, mark_product_used, PipelineRun, save_pipeline_run,
    get_pipeline_stats
)
from src.extractor import extract_products
from src.script_tts import generate_script_and_tts, ScriptResult
from src.video_builder import build_video, VideoResult
from src.media_storage import upload_video_to_r2
from src.youtube_uploader import upload_to_youtube, check_youtube_quota, UploadResult
from src.instagram_uploader import upload_to_instagram, check_instagram_quota, InstagramUploadResult
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class PipelineStepResult:
    """Result of a single pipeline step."""
    step: str
    success: bool
    message: str
    data: Any = None
    duration_ms: int = 0


@dataclass
class PipelineResult:
    """Result of full pipeline execution."""
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    success: bool
    products_processed: int
    youtube_uploaded: int
    instagram_uploaded: int
    errors: List[str]
    steps: List[PipelineStepResult]
    dry_run: bool
    videos_generated: int = 0


# =============================================================================
# Pipeline Scheduler
# =============================================================================

class PipelineScheduler:
    """Manages scheduled pipeline execution."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run or settings.pipeline_dry_run
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self.current_run: Optional[PipelineResult] = None
        self.consecutive_errors = 0
        self.max_consecutive_errors = settings.max_consecutive_errors

        # Job references for management
        self.jobs: Dict[str, Any] = {}

        # Stats
        self.stats = {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "products_processed": 0,
            "youtube_uploaded": 0,
            "instagram_uploaded": 0,
            "last_run": None,
            "last_success": None
        }

    def start(self):
        """Start the scheduler."""
        if self.is_running:
            logger.warning("Scheduler already running")
            return

        # Add jobs based on configuration
        self._add_jobs()

        self.scheduler.start()
        self.is_running = True
        logger.info(f"Scheduler started (dry_run={self.dry_run})")

    def stop(self):
        """Stop the scheduler."""
        if not self.is_running:
            return

        self.scheduler.shutdown(wait=True)
        self.is_running = False
        logger.info("Scheduler stopped")

    def _add_jobs(self):
        """Add scheduled jobs."""
        # Full pipeline job - runs at interval
        if settings.post_interval_minutes > 0:
            self.jobs["pipeline"] = self.scheduler.add_job(
                self.run_pipeline,
                IntervalTrigger(minutes=settings.post_interval_minutes),
                id="pipeline",
                name="Full Pipeline",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300
            )

        # Daily extraction job - runs once per day at 2 AM
        self.jobs["daily_extract"] = self.scheduler.add_job(
            self.run_extraction,
            CronTrigger(hour=2, minute=0),
            id="daily_extract",
            name="Daily Product Extraction",
            max_instances=1
        )

        # Health check job
        self.jobs["health_check"] = self.scheduler.add_job(
            self.health_check,
            IntervalTrigger(minutes=settings.health_check_interval_minutes),
            id="health_check",
            name="Health Check",
            max_instances=1
        )

        # Quota reset check - runs at midnight
        self.jobs["quota_reset"] = self.scheduler.add_job(
            self.check_quota_reset,
            CronTrigger(hour=0, minute=5),
            id="quota_reset",
            name="Daily Quota Reset Check",
            max_instances=1
        )

        logger.info(f"Scheduled jobs: {list(self.jobs.keys())}")

    async def run_pipeline(self) -> PipelineResult:
        """Run the complete pipeline: extract -> generate -> build -> upload."""
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        started_at = datetime.now()

        logger.info(f"Starting pipeline run: {run_id} (dry_run={self.dry_run})")

        result = PipelineResult(
            run_id=run_id,
            started_at=started_at,
            completed_at=None,
            success=False,
            products_processed=0,
            youtube_uploaded=0,
            instagram_uploaded=0,
            errors=[],
            steps=[],
            dry_run=self.dry_run
        )

        self.current_run = result

        try:
            # Step 1: Check quotas
            step_result = await self._step_check_quotas()
            result.steps.append(step_result)
            if not step_result.success and not self.dry_run:
                raise RuntimeError(step_result.message)

            # Step 2: Get unpublished products
            step_result = await self._step_get_products()
            result.steps.append(step_result)
            if not step_result.success:
                raise RuntimeError(step_result.message)

            products = step_result.data
            if not products:
                result.success = True
                result.message = "No unpublished products available"
                logger.info("No unpublished products to process")
                return self._finalize_run(result)

            # Process each product
            for product in products[:3]:  # Limit to 3 per run
                try:
                    product_result = await self._process_product(product)
                    result.products_processed += 1

                    if product_result.get("youtube"):
                        result.youtube_uploaded += 1
                    if product_result.get("instagram"):
                        result.instagram_uploaded += 1
                    if product_result.get("video"):
                        result.videos_generated += 1

                except Exception as e:
                    error_msg = f"Product {product.meesho_product_id} failed: {e}"
                    logger.error(error_msg)
                    result.errors.append(error_msg)

            result.success = True
            result.message = f"Processed {result.products_processed} products"
            self.consecutive_errors = 0

        except Exception as e:
            result.success = False
            result.message = str(e)
            result.errors.append(str(e))
            self.consecutive_errors += 1
            logger.error(f"Pipeline run failed: {e}")

        return self._finalize_run(result)

    async def _process_product(self, product: Product) -> dict:
        """Process a single product through the full pipeline."""
        results = {"youtube": False, "instagram": False}

        # Step 1: Generate script and TTS
        script_result = await self._run_step(
            "script_tts",
            lambda: generate_script_and_tts(product, str(settings.media_dir))
        )

        if not script_result.success:
            raise RuntimeError(f"Script/TTS failed: {script_result.message}")

        script_data: ScriptResult = script_result.data

        # Step 2: Build video
        video_result = await self._run_step(
            "video_build",
            lambda: build_video(product, script_data.telugu_script, script_data.audio_path, str(settings.media_dir))
        )

        if not video_result.success:
            raise RuntimeError(f"Video build failed: {video_result.message}")

        video_data: VideoResult = video_result.data
        if not self.dry_run:
            mark_product_used(product.meesho_product_id)
        results["video"] = True
        video_url = f"{settings.media_base_url}/{Path(video_data.video_path).name}"

        # Step 3: Upload to YouTube
        if not self.dry_run:
            yt_quota = await check_youtube_quota()
            if yt_quota.can_upload:
                yt_result = await self._run_step(
                    "youtube_upload",
                    lambda: upload_to_youtube(video_data.video_path, product)
                )

                if yt_result.success:
                    upload_data: UploadResult = yt_result.data
                    mark_youtube_published(product.meesho_product_id, upload_data.video_id, upload_data.video_url)
                    results["youtube"] = True
                    logger.info(f"YouTube upload successful: {upload_data.video_url}")
                else:
                    logger.warning(f"YouTube upload failed: {yt_result.message}")
            else:
                logger.warning("YouTube quota exhausted, skipping")

        # Step 4: Upload to Instagram
        if not self.dry_run:
            ig_quota = await check_instagram_quota()
            if ig_quota["can_upload"]:
                if settings.r2_public_base_url:
                    video_url = await upload_video_to_r2(video_data.video_path)
                ig_result = await self._run_step(
                    "instagram_upload",
                    lambda: upload_to_instagram(video_url, product)
                )

                if ig_result.success:
                    upload_data: InstagramUploadResult = ig_result.data
                    mark_instagram_published(product.meesho_product_id, upload_data.media_id, upload_data.permalink)
                    results["instagram"] = True
                    logger.info(f"Instagram upload successful: {upload_data.permalink}")
                else:
                    logger.warning(f"Instagram upload failed: {ig_result.message}")
            else:
                logger.warning("Instagram quota exhausted, skipping")

        return results

    async def _step_check_quotas(self) -> PipelineStepResult:
        """Check API quotas before starting."""
        start = datetime.now()

        try:
            yt_quota = await check_youtube_quota()
            ig_quota = await check_instagram_quota()

            return PipelineStepResult(
                step="check_quotas",
                success=True,
                message=f"YouTube: {yt_quota.remaining}/{yt_quota.limit}, Instagram: {ig_quota['remaining']}/{ig_quota['limit']}",
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )
        except Exception as e:
            return PipelineStepResult(
                step="check_quotas",
                success=False,
                message=str(e),
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )

    async def _step_get_products(self) -> PipelineStepResult:
        """Get unpublished products from database."""
        start = datetime.now()

        try:
            products = get_unpublished_products(limit=10)

            return PipelineStepResult(
                step="get_products",
                success=True,
                message=f"Found {len(products)} unpublished products",
                data=products,
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )
        except Exception as e:
            return PipelineStepResult(
                step="get_products",
                success=False,
                message=str(e),
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )

    async def _run_step(self, step_name: str, coro: Callable) -> PipelineStepResult:
        """Run a pipeline step with timing."""
        start = datetime.now()

        try:
            result = coro()
            if inspect.isawaitable(result):
                result = await result

            return PipelineStepResult(
                step=step_name,
                success=True,
                message="Completed",
                data=result,
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )
        except Exception as e:
            return PipelineStepResult(
                step=step_name,
                success=False,
                message=str(e),
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )

    def _finalize_run(self, result: PipelineResult) -> PipelineResult:
        """Finalize pipeline run and save to database."""
        result.completed_at = datetime.now()

        # Save to database
        status = (
            "failed" if not result.success
            else "partial" if result.errors
            else "success"
        )
        pipeline_run = PipelineRun(
            started_at=result.started_at,
            finished_at=result.completed_at,
            status=status,
            products_extracted=result.products_processed,
            videos_generated=result.videos_generated,
            youtube_uploaded=result.youtube_uploaded,
            instagram_uploaded=result.instagram_uploaded,
            errors="; ".join(result.errors),
            dry_run=result.dry_run,
        )
        save_pipeline_run(pipeline_run)

        # Update stats
        self.stats["total_runs"] += 1
        if result.success:
            self.stats["successful_runs"] += 1
            self.stats["last_success"] = result.completed_at.isoformat()
        else:
            self.stats["failed_runs"] += 1

        self.stats["products_processed"] += result.products_processed
        self.stats["youtube_uploaded"] += result.youtube_uploaded
        self.stats["instagram_uploaded"] += result.instagram_uploaded
        self.stats["last_run"] = result.completed_at.isoformat()

        self.current_run = None

        logger.info(f"Pipeline run completed: {result.run_id} - {status.upper()}")
        return result

    async def run_extraction(self) -> dict:
        """Run product extraction for all categories."""
        logger.info("Starting scheduled product extraction")

        try:
            results = await extract_products(headless=True)

            total = sum(len(p) for p in results.values())
            logger.info(f"Extraction completed: {total} products across {len(results)} categories")

            return {
                "success": True,
                "categories": results,
                "total_products": total
            }
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return {"success": False, "error": str(e)}

    async def health_check(self) -> dict:
        """Periodic health check."""
        try:
            from src.database import get_pipeline_stats
            stats = get_pipeline_stats()

            # Check if we've had too many consecutive errors
            if self.consecutive_errors >= self.max_consecutive_errors:
                logger.error(f"Max consecutive errors reached ({self.consecutive_errors}), stopping scheduler")
                self.stop()
                return {"status": "stopped", "reason": "max_errors"}

            return {
                "status": "healthy",
                "scheduler_running": self.is_running,
                "consecutive_errors": self.consecutive_errors,
                "stats": stats
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {"status": "error", "error": str(e)}

    async def check_quota_reset(self):
        """Check and log quota reset at midnight."""
        logger.info("Daily quota reset - new day started")
        # Quotas are automatically tracked per day in database

    def get_status(self) -> dict:
        """Get scheduler status."""
        jobs_info = {}
        for job_id, job in self.jobs.items():
            jobs_info[job_id] = {
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            }

        return {
            "running": self.is_running,
            "dry_run": self.dry_run,
            "consecutive_errors": self.consecutive_errors,
            "current_run": self.current_run.run_id if self.current_run else None,
            "jobs": jobs_info,
            "stats": self.stats
        }

    def trigger_pipeline_now(self) -> asyncio.Task:
        """Manually trigger a pipeline run."""
        return asyncio.create_task(self.run_pipeline())

    def trigger_extraction_now(self) -> asyncio.Task:
        """Manually trigger extraction."""
        return asyncio.create_task(self.run_extraction())


# =============================================================================
# Singleton Instance
# =============================================================================

_scheduler_instance: Optional[PipelineScheduler] = None


def get_scheduler(dry_run: bool = False) -> PipelineScheduler:
    """Get or create scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = PipelineScheduler(dry_run=dry_run)
    return _scheduler_instance


async def run_scheduler(dry_run: bool = False):
    """Run scheduler indefinitely."""
    scheduler = get_scheduler(dry_run=dry_run)
    scheduler.start()

    try:
        while scheduler.is_running:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        logger.info("Scheduler interrupted")
    finally:
        scheduler.stop()


if __name__ == "__main__":
    asyncio.run(run_scheduler())
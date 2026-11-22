"""
Tests for database module.
"""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, date

from src.database import (
    init_db, Product, save_product, get_product_by_id,
    get_unpublished_products, mark_youtube_published, mark_instagram_published,
    DailyQuota, get_daily_quota, increment_daily_quota, reset_daily_quota,
    PipelineRun, save_pipeline_run, get_pipeline_stats,
    IGDMLog, save_ig_dm_log
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Override the database path
    import src.database as db_module
    original_path = db_module.DB_PATH
    db_module.DB_PATH = db_path
    db_module._engine = None
    db_module._session_factory = None

    init_db()

    yield db_path

    # Cleanup
    db_module.DB_PATH = original_path
    db_module._engine = None
    db_module._session_factory = None
    db_path.unlink(missing_ok=True)


@pytest.fixture
def sample_product():
    """Create a sample product for testing."""
    return Product(
        meesho_product_id="TEST123",
        title="Test Cotton Kurti",
        category="kurtis",
        price=299,
        original_price=599,
        discount_percent=50,
        rating=4.5,
        review_count=100,
        image_urls=["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
        affiliate_url="https://meesho.com/product/TEST123",
        product_code="KUR-TEST12",
        fabric="Cotton",
        color="Red",
        size_options=["S", "M", "L", "XL"],
        in_stock=True,
        scraped_at=datetime.now()
    )


class TestProductCRUD:
    """Test product CRUD operations."""

    def test_save_and_get_product(self, temp_db, sample_product):
        """Test saving and retrieving a product."""
        # Save product
        is_new = save_product(sample_product)
        assert is_new is True

        # Retrieve product
        retrieved = get_product_by_id("TEST123")
        assert retrieved is not None
        assert retrieved.meesho_product_id == "TEST123"
        assert retrieved.title == "Test Cotton Kurti"
        assert retrieved.price == 299
        assert retrieved.category == "kurtis"

    def test_duplicate_product_not_saved_twice(self, temp_db, sample_product):
        """Test that duplicate products are not saved twice."""
        save_product(sample_product)
        is_new = save_product(sample_product)
        assert is_new is False

    def test_get_unpublished_products(self, temp_db, sample_product):
        """Test getting unpublished products."""
        save_product(sample_product)

        products = get_unpublished_products(limit=10)
        assert len(products) == 1
        assert products[0].meesho_product_id == "TEST123"

    def test_mark_youtube_published(self, temp_db, sample_product):
        """Test marking product as published on YouTube."""
        save_product(sample_product)

        mark_youtube_published("TEST123", "VIDEO123", "https://youtube.com/watch?v=VIDEO123")

        product = get_product_by_id("TEST123")
        assert product.published_youtube is True
        assert product.youtube_video_id == "VIDEO123"
        assert product.youtube_url == "https://youtube.com/watch?v=VIDEO123"

    def test_mark_instagram_published(self, temp_db, sample_product):
        """Test marking product as published on Instagram."""
        save_product(sample_product)

        mark_instagram_published("TEST123", "MEDIA123", "https://instagram.com/p/MEDIA123")

        product = get_product_by_id("TEST123")
        assert product.published_instagram is True
        assert product.instagram_media_id == "MEDIA123"
        assert product.instagram_permalink == "https://instagram.com/p/MEDIA123"


class TestDailyQuota:
    """Test daily quota tracking."""

    def test_increment_and_get_quota(self, temp_db):
        """Test incrementing and getting daily quota."""
        today = date.today().isoformat()

        # Initial quota should be 0
        quota = get_daily_quota(today, "youtube_uploads")
        assert quota is None or quota.count == 0

        # Increment
        increment_daily_quota(today, "youtube_uploads")
        increment_daily_quota(today, "youtube_uploads")

        quota = get_daily_quota(today, "youtube_uploads")
        assert quota is not None
        assert quota.count == 2

    def test_reset_quota(self, temp_db):
        """Test resetting daily quota."""
        today = date.today().isoformat()

        increment_daily_quota(today, "youtube_uploads")
        increment_daily_quota(today, "youtube_uploads")

        reset_daily_quota(today, "youtube_uploads")

        quota = get_daily_quota(today, "youtube_uploads")
        assert quota is None or quota.count == 0


class TestPipelineRun:
    """Test pipeline run tracking."""

    def test_save_pipeline_run(self, temp_db):
        """Test saving pipeline run."""
        run = PipelineRun(
            run_id="TEST_RUN_001",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            status="success",
            products_processed=5,
            youtube_uploaded=3,
            instagram_uploaded=5,
            errors=None
        )

        save_pipeline_run(run)

        stats = get_pipeline_stats()
        assert stats["total_runs"] >= 1
        assert stats["successful_runs"] >= 1


class TestIGDMLog:
    """Test Instagram DM log."""

    def test_save_ig_dm_log(self, temp_db):
        """Test saving IG DM log."""
        log = IGDMLog(
            ig_user_id="USER123",
            ig_username="testuser",
            comment_id="COMMENT123",
            media_id="MEDIA123",
            keyword="LINK",
            replied=True
        )

        save_ig_dm_log(log)

        # Try to save duplicate - should be ignored due to UNIQUE constraint
        save_ig_dm_log(log)

    def test_get_by_comment_id(self, temp_db):
        """Test getting DM log by comment ID."""
        log = IGDMLog(
            ig_user_id="USER123",
            ig_username="testuser",
            comment_id="COMMENT123",
            media_id="MEDIA123",
            keyword="LINK",
            replied=True
        )

        save_ig_dm_log(log)

        retrieved = IGDMLog.get_by_comment_id("COMMENT123")
        assert retrieved is not None
        assert retrieved.ig_user_id == "USER123"
        assert retrieved.keyword == "LINK"


class TestPipelineStats:
    """Test pipeline statistics."""

    def test_get_pipeline_stats(self, temp_db, sample_product):
        """Test getting pipeline stats."""
        save_product(sample_product)

        stats = get_pipeline_stats()
        assert "total_products" in stats
        assert stats["total_products"] >= 1
        assert "pending_products" in stats
        assert stats["pending_products"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
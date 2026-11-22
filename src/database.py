"""
SQLite database layer for the Meesho Telugu Fashion automation pipeline.
Handles products, published posts, DM logs, daily quotas, and pipeline runs.
"""
import sqlite3
import os
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import threading

from src.config import settings


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class Product:
    """Meesho product data."""
    meesho_product_id: str
    title: str
    category: str
    price: int
    original_price: int
    discount_percent: int
    rating: float
    review_count: int
    image_urls: List[str]
    affiliate_url: str
    product_code: str
    fabric: str
    color: str
    size_options: List[str]
    in_stock: bool
    scraped_at: Optional[datetime] = None
    used_for_video: bool = False
    used_at: Optional[datetime] = None

    def __post_init__(self):
        if self.scraped_at is None:
            self.scraped_at = datetime.now()


@dataclass
class PublishedPost:
    """Record of a published post on YouTube or Instagram."""
    id: Optional[int] = None
    product_id: str = ""
    platform: str = ""  # "youtube" or "instagram"
    platform_post_id: str = ""
    platform_url: str = ""
    video_path: str = ""
    caption: str = ""
    posted_at: Optional[datetime] = None
    status: str = "success"  # "success", "failed", "pending"

    def __post_init__(self):
        if self.posted_at is None:
            self.posted_at = datetime.now()


@dataclass
class IGDMLog:
    """Instagram DM log for deduplication."""
    id: Optional[int] = None
    comment_id: str = ""
    media_id: str = ""
    username: str = ""
    comment_text: str = ""
    product_id: str = ""
    dm_sent: bool = False
    sent_at: Optional[datetime] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.sent_at is None:
            self.sent_at = datetime.now()


@dataclass
class DailyQuota:
    """Daily API quota tracking."""
    id: Optional[int] = None
    quota_date: date = None
    youtube_uploads: int = 0
    instagram_uploads: int = 0
    youtube_quota_used: int = 0

    def __post_init__(self):
        if self.quota_date is None:
            self.quota_date = date.today()


@dataclass
class PipelineRun:
    """Pipeline execution log."""
    id: Optional[int] = None
    started_at: datetime = None
    finished_at: Optional[datetime] = None
    status: str = "running"  # "running", "success", "failed", "partial"
    products_extracted: int = 0
    videos_generated: int = 0
    youtube_uploaded: int = 0
    instagram_uploaded: int = 0
    errors: str = ""
    dry_run: bool = False

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.now()


# =============================================================================
# Database Connection Manager
# =============================================================================

class Database:
    """Thread-safe SQLite database manager."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.database_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_conn() as conn:
            conn.executescript("""
                -- Products table
                CREATE TABLE IF NOT EXISTS products (
                    meesho_product_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    original_price INTEGER NOT NULL,
                    discount_percent INTEGER NOT NULL,
                    rating REAL NOT NULL,
                    review_count INTEGER NOT NULL,
                    image_urls TEXT NOT NULL,  -- JSON array
                    affiliate_url TEXT NOT NULL,
                    product_code TEXT NOT NULL,
                    fabric TEXT NOT NULL,
                    color TEXT NOT NULL,
                    size_options TEXT NOT NULL,  -- JSON array
                    in_stock INTEGER NOT NULL DEFAULT 1,
                    scraped_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    used_for_video INTEGER NOT NULL DEFAULT 0,
                    used_at TIMESTAMP
                );

                -- Published posts table
                CREATE TABLE IF NOT EXISTS published_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    platform_post_id TEXT NOT NULL,
                    platform_url TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    posted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'success',
                    FOREIGN KEY (product_id) REFERENCES products (meesho_product_id)
                );

                CREATE INDEX IF NOT EXISTS idx_published_product ON published_posts(product_id);
                CREATE INDEX IF NOT EXISTS idx_published_platform ON published_posts(platform);
                CREATE INDEX IF NOT EXISTS idx_published_date ON published_posts(posted_at);

                -- Instagram DM log table
                CREATE TABLE IF NOT EXISTS ig_dm_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT NOT NULL UNIQUE,
                    media_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    comment_text TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    dm_sent INTEGER NOT NULL DEFAULT 0,
                    sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    error TEXT,
                    FOREIGN KEY (product_id) REFERENCES products (meesho_product_id)
                );

                CREATE INDEX IF NOT EXISTS idx_dm_comment ON ig_dm_log(comment_id);
                CREATE INDEX IF NOT EXISTS idx_dm_media ON ig_dm_log(media_id);
                CREATE INDEX IF NOT EXISTS idx_dm_product ON ig_dm_log(product_id);

                -- Daily quotas table
                CREATE TABLE IF NOT EXISTS daily_quotas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quota_date DATE NOT NULL UNIQUE,
                    youtube_uploads INTEGER NOT NULL DEFAULT 0,
                    instagram_uploads INTEGER NOT NULL DEFAULT 0,
                    youtube_quota_used INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_quota_date ON daily_quotas(quota_date);

                -- Pipeline runs table
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'running',
                    products_extracted INTEGER NOT NULL DEFAULT 0,
                    videos_generated INTEGER NOT NULL DEFAULT 0,
                    youtube_uploaded INTEGER NOT NULL DEFAULT 0,
                    instagram_uploaded INTEGER NOT NULL DEFAULT 0,
                    errors TEXT,
                    dry_run INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_pipeline_date ON pipeline_runs(started_at);
            """)

    @contextmanager
    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query and return cursor."""
        with self._lock:
            with self._get_conn() as conn:
                return conn.execute(query, params)

    def executemany(self, query: str, params_list: List[tuple]) -> sqlite3.Cursor:
        """Execute many queries."""
        with self._lock:
            with self._get_conn() as conn:
                return conn.executemany(query, params_list)

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Fetch single row."""
        with self._lock:
            with self._get_conn() as conn:
                return conn.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Fetch all rows."""
        with self._lock:
            with self._get_conn() as conn:
                return conn.execute(query, params).fetchall()


# =============================================================================
# Module-level DB_PATH for test compatibility
# =============================================================================

DB_PATH: Optional[Path] = None


# =============================================================================
# Global Database Instance
# =============================================================================

_db_instance: Optional[Database] = None


def get_db() -> Database:
    """Get global database instance (singleton)."""
    global _db_instance, DB_PATH
    if _db_instance is None:
        _db_instance = Database()
        DB_PATH = _db_instance.db_path
    return _db_instance


def init_db(db_path: Optional[Path] = None) -> Database:
    """Initialize database with custom path."""
    global _db_instance, DB_PATH
    _db_instance = Database(db_path)
    DB_PATH = _db_instance.db_path
    return _db_instance


# =============================================================================
# Product Operations
# =============================================================================

def save_product(product: Product) -> bool:
    """Save or update a product. Returns True if new, False if updated."""
    db = get_db()
    import json

    existing = db.fetchone(
        "SELECT meesho_product_id FROM products WHERE meesho_product_id = ?",
        (product.meesho_product_id,)
    )

    image_urls_json = json.dumps(product.image_urls)
    size_options_json = json.dumps(product.size_options)

    if existing:
        db.execute("""
            UPDATE products SET
                title = ?, category = ?, price = ?, original_price = ?,
                discount_percent = ?, rating = ?, review_count = ?,
                image_urls = ?, affiliate_url = ?, product_code = ?,
                fabric = ?, color = ?, size_options = ?, in_stock = ?,
                scraped_at = ?
            WHERE meesho_product_id = ?
        """, (
            product.title, product.category, product.price, product.original_price,
            product.discount_percent, product.rating, product.review_count,
            image_urls_json, product.affiliate_url, product.product_code,
            product.fabric, product.color, size_options_json,
            1 if product.in_stock else 0,
            product.scraped_at.isoformat() if product.scraped_at else datetime.now().isoformat(),
            product.meesho_product_id
        ))
        return False
    else:
        db.execute("""
            INSERT INTO products (
                meesho_product_id, title, category, price, original_price,
                discount_percent, rating, review_count, image_urls,
                affiliate_url, product_code, fabric, color, size_options,
                in_stock, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product.meesho_product_id, product.title, product.category,
            product.price, product.original_price, product.discount_percent,
            product.rating, product.review_count, image_urls_json,
            product.affiliate_url, product.product_code, product.fabric,
            product.color, size_options_json,
            1 if product.in_stock else 0,
            product.scraped_at.isoformat() if product.scraped_at else datetime.now().isoformat()
        ))
        return True


def get_product(product_id: str) -> Optional[Product]:
    """Get a product by ID."""
    db = get_db()
    import json

    row = db.fetchone(
        "SELECT * FROM products WHERE meesho_product_id = ?",
        (product_id,)
    )
    if not row:
        return None

    return Product(
        meesho_product_id=row["meesho_product_id"],
        title=row["title"],
        category=row["category"],
        price=row["price"],
        original_price=row["original_price"],
        discount_percent=row["discount_percent"],
        rating=row["rating"],
        review_count=row["review_count"],
        image_urls=json.loads(row["image_urls"]),
        affiliate_url=row["affiliate_url"],
        product_code=row["product_code"],
        fabric=row["fabric"],
        color=row["color"],
        size_options=json.loads(row["size_options"]),
        in_stock=bool(row["in_stock"]),
        scraped_at=datetime.fromisoformat(row["scraped_at"]) if row["scraped_at"] else None,
        used_for_video=bool(row["used_for_video"]),
        used_at=datetime.fromisoformat(row["used_at"]) if row["used_at"] else None
    )


def get_unused_products(limit: int = 10) -> List[Product]:
    """Get products not yet used for video, ordered by scraped_at desc."""
    db = get_db()
    import json

    rows = db.fetchall("""
        SELECT * FROM products
        WHERE used_for_video = 0 AND in_stock = 1
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (limit,))

    products = []
    for row in rows:
        products.append(Product(
            meesho_product_id=row["meesho_product_id"],
            title=row["title"],
            category=row["category"],
            price=row["price"],
            original_price=row["original_price"],
            discount_percent=row["discount_percent"],
            rating=row["rating"],
            review_count=row["review_count"],
            image_urls=json.loads(row["image_urls"]),
            affiliate_url=row["affiliate_url"],
            product_code=row["product_code"],
            fabric=row["fabric"],
            color=row["color"],
            size_options=json.loads(row["size_options"]),
            in_stock=bool(row["in_stock"]),
            scraped_at=datetime.fromisoformat(row["scraped_at"]) if row["scraped_at"] else None,
            used_for_video=bool(row["used_for_video"]),
            used_at=datetime.fromisoformat(row["used_at"]) if row["used_at"] else None
        ))
    return products


def mark_product_used(product_id: str) -> bool:
    """Mark a product as used for video."""
    db = get_db()
    cursor = db.execute("""
        UPDATE products SET used_for_video = 1, used_at = ?
        WHERE meesho_product_id = ? AND used_for_video = 0
    """, (datetime.now().isoformat(), product_id))
    return cursor.rowcount > 0


def get_product_count() -> int:
    """Get total product count."""
    db = get_db()
    row = db.fetchone("SELECT COUNT(*) as c FROM products")
    return row["c"] if row else 0


def get_product_by_id(product_id: str) -> Optional[Product]:
    """Get a product by ID (alias for get_product)."""
    return get_product(product_id)


def get_unpublished_products(limit: int = 10) -> List[Product]:
    """Get products not yet used for video (alias for get_unused_products)."""
    return get_unused_products(limit)


def get_unused_product_count() -> int:
    """Get count of unused products."""
    db = get_db()
    row = db.fetchone("SELECT COUNT(*) as c FROM products WHERE used_for_video = 0 AND in_stock = 1")
    return row["c"] if row else 0


# =============================================================================
# Published Post Operations
# =============================================================================

def save_published_post(post: PublishedPost) -> int:
    """Save a published post record. Returns the new ID."""
    db = get_db()
    cursor = db.execute("""
        INSERT INTO published_posts (
            product_id, platform, platform_post_id, platform_url,
            video_path, caption, posted_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        post.product_id, post.platform, post.platform_post_id,
        post.platform_url, post.video_path, post.caption,
        post.posted_at.isoformat() if post.posted_at else datetime.now().isoformat(),
        post.status
    ))
    return cursor.lastrowid


def get_published_posts(
    platform: Optional[str] = None,
    product_id: Optional[str] = None,
    limit: int = 100
) -> List[PublishedPost]:
    """Get published posts with optional filters."""
    db = get_db()

    query = "SELECT * FROM published_posts WHERE 1=1"
    params = []

    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if product_id:
        query += " AND product_id = ?"
        params.append(product_id)

    query += " ORDER BY posted_at DESC LIMIT ?"
    params.append(limit)

    rows = db.fetchall(query, tuple(params))
    return [
        PublishedPost(
            id=row["id"],
            product_id=row["product_id"],
            platform=row["platform"],
            platform_post_id=row["platform_post_id"],
            platform_url=row["platform_url"],
            video_path=row["video_path"],
            caption=row["caption"],
            posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
            status=row["status"]
        )
        for row in rows
    ]


def get_today_youtube_uploads() -> int:
    """Get count of YouTube uploads today."""
    db = get_db()
    today = date.today().isoformat()
    row = db.fetchone("""
        SELECT COUNT(*) as c FROM published_posts
        WHERE platform = 'youtube' AND date(posted_at) = ?
    """, (today,))
    return row["c"] if row else 0


def get_today_instagram_uploads() -> int:
    """Get count of Instagram uploads today."""
    db = get_db()
    today = date.today().isoformat()
    row = db.fetchone("""
        SELECT COUNT(*) as c FROM published_posts
        WHERE platform = 'instagram' AND date(posted_at) = ?
    """, (today,))
    return row["c"] if row else 0


# =============================================================================
# Instagram DM Log Operations
# =============================================================================

def log_dm_sent(
    comment_id: str,
    media_id: str,
    username: str,
    comment_text: str,
    product_id: str,
    success: bool = True,
    error: Optional[str] = None
) -> bool:
    """Log a DM attempt. Returns True if logged (not duplicate)."""
    db = get_db()

    try:
        db.execute("""
            INSERT INTO ig_dm_log (
                comment_id, media_id, username, comment_text,
                product_id, dm_sent, sent_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            comment_id, media_id, username, comment_text,
            product_id, 1 if success else 0,
            datetime.now().isoformat(), error
        ))
        return True
    except sqlite3.IntegrityError:
        # Duplicate comment_id
        return False


def is_dm_sent(comment_id: str) -> bool:
    """Check if DM was already sent for this comment."""
    db = get_db()
    row = db.fetchone(
        "SELECT 1 FROM ig_dm_log WHERE comment_id = ? AND dm_sent = 1",
        (comment_id,)
    )
    return row is not None


def get_ig_dm_log(comment_id: str) -> Optional[IGDMLog]:
    """Get a previously claimed DM attempt by its unique comment ID."""
    row = get_db().fetchone(
        "SELECT * FROM ig_dm_log WHERE comment_id = ?",
        (comment_id,)
    )
    if not row:
        return None
    return IGDMLog(
        id=row["id"],
        comment_id=row["comment_id"],
        media_id=row["media_id"],
        username=row["username"],
        comment_text=row["comment_text"],
        product_id=row["product_id"],
        dm_sent=bool(row["dm_sent"]),
        sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
        error=row["error"],
    )


def update_ig_dm_log(comment_id: str, success: bool, error: Optional[str] = None) -> bool:
    """Update the outcome of a claimed DM attempt."""
    cursor = get_db().execute(
        "UPDATE ig_dm_log SET dm_sent = ?, error = ?, sent_at = ? WHERE comment_id = ?",
        (1 if success else 0, error, datetime.now().isoformat(), comment_id)
    )
    return cursor.rowcount > 0


def get_product_for_media(media_id: str) -> Optional[str]:
    """Get product ID associated with an Instagram media ID."""
    db = get_db()
    row = db.fetchone(
        "SELECT product_id FROM published_posts WHERE platform_post_id = ? AND platform = 'instagram'",
        (media_id,)
    )
    return row["product_id"] if row else None


def get_dm_stats() -> Dict[str, int]:
    """Get DM statistics."""
    db = get_db()
    total = db.fetchone("SELECT COUNT(*) as c FROM ig_dm_log")["c"]
    sent = db.fetchone("SELECT COUNT(*) as c FROM ig_dm_log WHERE dm_sent = 1")["c"]
    failed = db.fetchone("SELECT COUNT(*) as c FROM ig_dm_log WHERE dm_sent = 0")["c"]
    return {"total": total, "sent": sent, "failed": failed}


def save_ig_dm_log(log: IGDMLog) -> bool:
    """
    Save an IG DM log entry.
    Returns True if saved, False if duplicate comment_id.
    """
    db = get_db()

    try:
        cursor = db.execute("""
            INSERT INTO ig_dm_log (
                comment_id, media_id, username, comment_text,
                product_id, dm_sent, sent_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log.comment_id,
            log.media_id,
            log.username,
            log.comment_text,
            log.product_id,
            1 if log.dm_sent else 0,
            log.sent_at.isoformat() if log.sent_at else datetime.now().isoformat(),
            log.error
        ))
        return True
    except sqlite3.IntegrityError:
        # Duplicate comment_id
        return False


def mark_youtube_published(product_id: str, video_id: str, video_url: str) -> int:
    """Mark a product as published on YouTube. Returns published post ID."""
    db = get_db()
    from src.database import PublishedPost

    post = PublishedPost(
        product_id=product_id,
        platform="youtube",
        platform_post_id=video_id,
        platform_url=video_url,
        video_path="",  # Will be filled by caller if needed
        caption="",
        posted_at=datetime.now(),
        status="success"
    )
    return save_published_post(post)


def mark_instagram_published(product_id: str, media_id: str, permalink: str) -> int:
    """Mark a product as published on Instagram. Returns published post ID."""
    db = get_db()
    from src.database import PublishedPost

    post = PublishedPost(
        product_id=product_id,
        platform="instagram",
        platform_post_id=media_id,
        platform_url=permalink,
        video_path="",
        caption="",
        posted_at=datetime.now(),
        status="success"
    )
    return save_published_post(post)


def save_pipeline_run(pipeline_run: PipelineRun) -> int:
    """Save a pipeline run record. Returns run ID."""
    db = get_db()
    cursor = db.execute("""
        INSERT INTO pipeline_runs (
            started_at, finished_at, status,
            products_extracted, videos_generated,
            youtube_uploaded, instagram_uploaded,
            errors, dry_run
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pipeline_run.started_at.isoformat() if pipeline_run.started_at else None,
        pipeline_run.finished_at.isoformat() if pipeline_run.finished_at else None,
        pipeline_run.status,
        pipeline_run.products_extracted,
        pipeline_run.videos_generated,
        pipeline_run.youtube_uploaded,
        pipeline_run.instagram_uploaded,
        pipeline_run.errors,
        1 if pipeline_run.dry_run else 0
    ))
    return cursor.lastrowid


def increment_daily_quota(quota_date: str, quota_type: str, amount: int = 1) -> DailyQuota:
    """Increment a daily quota counter."""
    db = get_db()

    if quota_type == "youtube_uploads":
        db.execute("""
            INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
            VALUES (?, ?, 0, 0)
            ON CONFLICT(quota_date) DO UPDATE SET
                youtube_uploads = youtube_uploads + ?
        """, (quota_date, amount, amount))
    elif quota_type == "ig_uploads":
        db.execute("""
            INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
            VALUES (?, 0, ?, 0)
            ON CONFLICT(quota_date) DO UPDATE SET
                instagram_uploads = instagram_uploads + ?
        """, (quota_date, amount, amount))
    elif quota_type == "youtube_quota":
        db.execute("""
            INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
            VALUES (?, 0, 0, ?)
            ON CONFLICT(quota_date) DO UPDATE SET
                youtube_quota_used = youtube_quota_used + ?
        """, (quota_date, amount, amount))
    else:
        raise ValueError(f"Unsupported quota type: {quota_type}")

    quota = get_daily_quota(quota_date, quota_type)
    if quota is None:
        raise RuntimeError(f"Failed to load quota record for {quota_date}")
    return quota


def get_pipeline_stats() -> Dict[str, Any]:
    """Get pipeline execution statistics."""
    db = get_db()

    total_products = db.fetchone("SELECT COUNT(*) as c FROM products")["c"]
    pending_products = db.fetchone(
        "SELECT COUNT(*) as c FROM products WHERE used_for_video = 0 AND in_stock = 1"
    )["c"]
    published_youtube = db.fetchone(
        "SELECT COUNT(*) as c FROM published_posts WHERE platform = 'youtube' AND status = 'success'"
    )["c"]
    published_instagram = db.fetchone(
        "SELECT COUNT(*) as c FROM published_posts WHERE platform = 'instagram' AND status = 'success'"
    )["c"]
    total_runs = db.fetchone("SELECT COUNT(*) as c FROM pipeline_runs")["c"]
    successful_runs = db.fetchone("SELECT COUNT(*) as c FROM pipeline_runs WHERE status = 'success'")["c"]
    failed_runs = db.fetchone("SELECT COUNT(*) as c FROM pipeline_runs WHERE status = 'failed'")["c"]

    recent_runs = db.fetchall("""
        SELECT * FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT 10
    """)

    total_products_extracted = db.fetchone("SELECT SUM(products_extracted) as s FROM pipeline_runs")["s"] or 0
    total_videos_generated = db.fetchone("SELECT SUM(videos_generated) as s FROM pipeline_runs")["s"] or 0
    total_youtube_uploaded = db.fetchone("SELECT SUM(youtube_uploaded) as s FROM pipeline_runs")["s"] or 0
    total_instagram_uploaded = db.fetchone("SELECT SUM(instagram_uploaded) as s FROM pipeline_runs")["s"] or 0

    last_run = None
    if recent_runs:
        r = recent_runs[0]
        last_run = {
            "id": r["id"],
            "started_at": r["started_at"],
            "status": r["status"],
            "products_processed": r["products_extracted"],
            "youtube_uploaded": r["youtube_uploaded"],
            "instagram_uploaded": r["instagram_uploaded"]
        }

    return {
        "total_products": total_products,
        "pending_products": pending_products,
        "published_youtube": published_youtube,
        "published_instagram": published_instagram,
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "failed_runs": failed_runs,
        "total_products_extracted": total_products_extracted,
        "total_videos_generated": total_videos_generated,
        "total_youtube_uploaded": total_youtube_uploaded,
        "total_instagram_uploaded": total_instagram_uploaded,
        "last_run": last_run
    }


def get_daily_quota(quota_date: str, quota_type: str) -> Optional[DailyQuota]:
    """Get daily quota for a specific date and type."""
    db = get_db()

    row = db.fetchone("SELECT * FROM daily_quotas WHERE quota_date = ?", (quota_date,))
    if row:
        return DailyQuota(
            id=row["id"],
            quota_date=date.fromisoformat(row["quota_date"]),
            youtube_uploads=row["youtube_uploads"],
            instagram_uploads=row["instagram_uploads"],
            youtube_quota_used=row["youtube_quota_used"]
        )
    return None


# =============================================================================
# Daily Quota Operations
# =============================================================================

def get_today_quota() -> DailyQuota:
    """Get or create today's quota record."""
    db = get_db()
    today = date.today().isoformat()

    row = db.fetchone("SELECT * FROM daily_quotas WHERE quota_date = ?", (today,))
    if row:
        return DailyQuota(
            id=row["id"],
            quota_date=date.fromisoformat(row["quota_date"]),
            youtube_uploads=row["youtube_uploads"],
            instagram_uploads=row["instagram_uploads"],
            youtube_quota_used=row["youtube_quota_used"]
        )

    # Create new
    cursor = db.execute("""
        INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
        VALUES (?, 0, 0, 0)
    """, (today,))
    return DailyQuota(
        id=cursor.lastrowid,
        quota_date=date.today(),
        youtube_uploads=0,
        instagram_uploads=0,
        youtube_quota_used=0
    )


def increment_youtube_quota(quota_used: int = 1600) -> DailyQuota:
    """Increment YouTube quota for today."""
    db = get_db()
    today = date.today().isoformat()

    db.execute("""
        INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
        VALUES (?, 1, 0, ?)
        ON CONFLICT(quota_date) DO UPDATE SET
            youtube_uploads = youtube_uploads + 1,
            youtube_quota_used = youtube_quota_used + ?
    """, (today, quota_used, quota_used))

    return get_today_quota()


def increment_instagram_quota() -> DailyQuota:
    """Increment Instagram upload count for today."""
    db = get_db()
    today = date.today().isoformat()

    db.execute("""
        INSERT INTO daily_quotas (quota_date, youtube_uploads, instagram_uploads, youtube_quota_used)
        VALUES (?, 0, 1, 0)
        ON CONFLICT(quota_date) DO UPDATE SET
            instagram_uploads = instagram_uploads + 1
    """, (today,))

    return get_today_quota()


def can_upload_youtube() -> bool:
    """Check if we can upload to YouTube today."""
    quota = get_today_quota()
    return quota.youtube_uploads < settings.max_yt_uploads_per_day


def can_upload_instagram() -> bool:
    """Check if we can upload to Instagram today."""
    quota = get_today_quota()
    return quota.instagram_uploads < settings.max_ig_uploads_per_day


def reset_daily_quota(quota_date: str, quota_type: str) -> bool:
    """Reset a specific daily quota counter to 0."""
    db = get_db()

    if quota_type == "youtube_uploads":
        db.execute("""
            UPDATE daily_quotas SET youtube_uploads = 0
            WHERE quota_date = ?
        """, (quota_date,))
    elif quota_type == "ig_uploads":
        db.execute("""
            UPDATE daily_quotas SET instagram_uploads = 0
            WHERE quota_date = ?
        """, (quota_date,))
    elif quota_type == "youtube_quota":
        db.execute("""
            UPDATE daily_quotas SET youtube_quota_used = 0
            WHERE quota_date = ?
        """, (quota_date,))
    else:
        return False

    return True


# =============================================================================
# Pipeline Run Operations
# =============================================================================

def start_pipeline_run(dry_run: bool = False) -> int:
    """Start a new pipeline run. Returns run ID."""
    db = get_db()
    cursor = db.execute("""
        INSERT INTO pipeline_runs (started_at, status, dry_run)
        VALUES (?, 'running', ?)
    """, (datetime.now().isoformat(), 1 if dry_run else 0))
    return cursor.lastrowid


def finish_pipeline_run(
    run_id: int,
    status: str,
    products_extracted: int = 0,
    videos_generated: int = 0,
    youtube_uploaded: int = 0,
    instagram_uploaded: int = 0,
    errors: str = ""
) -> bool:
    """Finish a pipeline run."""
    db = get_db()
    cursor = db.execute("""
        UPDATE pipeline_runs SET
            finished_at = ?, status = ?,
            products_extracted = ?, videos_generated = ?,
            youtube_uploaded = ?, instagram_uploaded = ?,
            errors = ?
        WHERE id = ?
    """, (
        datetime.now().isoformat(), status,
        products_extracted, videos_generated,
        youtube_uploaded, instagram_uploaded,
        errors, run_id
    ))
    return cursor.rowcount > 0


def get_recent_pipeline_runs(limit: int = 10) -> List[PipelineRun]:
    """Get recent pipeline runs."""
    db = get_db()
    rows = db.fetchall("""
        SELECT * FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT ?
    """, (limit,))

    return [
        PipelineRun(
            id=row["id"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            status=row["status"],
            products_extracted=row["products_extracted"],
            videos_generated=row["videos_generated"],
            youtube_uploaded=row["youtube_uploaded"],
            instagram_uploaded=row["instagram_uploaded"],
            errors=row["errors"] or "",
            dry_run=bool(row["dry_run"])
        )
        for row in rows
    ]


# =============================================================================
# Utility Functions
# =============================================================================

def get_stats() -> Dict[str, Any]:
    """Get overall statistics."""
    db = get_db()

    total_products = db.fetchone("SELECT COUNT(*) as c FROM products")["c"]
    used_products = db.fetchone("SELECT COUNT(*) as c FROM products WHERE used_for_video = 1")["c"]
    yt_posts = db.fetchone("SELECT COUNT(*) as c FROM published_posts WHERE platform = 'youtube'")["c"]
    ig_posts = db.fetchone("SELECT COUNT(*) as c FROM published_posts WHERE platform = 'instagram'")["c"]
    dm_stats = get_dm_stats()
    today_quota = get_today_quota()

    return {
        "products": {
            "total": total_products,
            "used": used_products,
            "unused": total_products - used_products
        },
        "published": {
            "youtube": yt_posts,
            "instagram": ig_posts,
            "total": yt_posts + ig_posts
        },
        "dm": dm_stats,
        "today_quota": {
            "youtube_uploads": today_quota.youtube_uploads,
            "instagram_uploads": today_quota.instagram_uploads,
            "youtube_quota_used": today_quota.youtube_quota_used,
            "max_youtube": settings.max_yt_uploads_per_day,
            "max_instagram": settings.max_ig_uploads_per_day
        }
    }


def cleanup_old_data(days: int = 30) -> int:
    """Clean up old pipeline runs and DM logs. Returns count of deleted rows."""
    db = get_db()
    cutoff = date.today().replace(day=date.today().day - days).isoformat()

    # Delete old pipeline runs
    cursor1 = db.execute("DELETE FROM pipeline_runs WHERE started_at < ?", (cutoff,))

    # Delete old DM logs (keep sent ones for longer)
    cursor2 = db.execute("""
        DELETE FROM ig_dm_log
        WHERE sent_at < ? AND dm_sent = 0
    """, (cutoff,))

    return cursor1.rowcount + cursor2.rowcount
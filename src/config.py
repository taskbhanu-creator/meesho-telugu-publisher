"""
Configuration management using Pydantic Settings.
All settings loaded from environment variables with validation.
"""
from pathlib import Path
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # -------------------------------------------------------------------------
    # Google Gemini API
    # -------------------------------------------------------------------------
    gemini_api_key: str = Field(default="", description="Google Gemini API key from AI Studio")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model to use")

    # -------------------------------------------------------------------------
    # Google OAuth / YouTube
    # -------------------------------------------------------------------------
    google_client_id: str = Field(default="", description="Google OAuth client ID")
    google_client_secret: str = Field(default="", description="Google OAuth client secret")
    google_refresh_token: str = Field(default="", description="Google OAuth refresh token")
    youtube_upload_scope: str = Field(
        default="https://www.googleapis.com/auth/youtube.upload",
        description="YouTube upload scope"
    )

    # -------------------------------------------------------------------------
    # Meta / Instagram Graph API
    # -------------------------------------------------------------------------
    meta_app_id: str = Field(default="", description="Meta App ID")
    meta_app_secret: str = Field(default="", description="Meta App Secret")
    meta_page_access_token: str = Field(default="", description="Long-lived Page Access Token")
    meta_page_id: str = Field(default="", description="Facebook Page ID connected to Instagram")
    meta_ig_user_id: str = Field(default="", description="Instagram Business Account ID")
    meta_graph_api_version: str = Field(default="v27.0", description="Meta Graph API version")
    meta_webhook_verify_token: str = Field(default="", description="Webhook verify token")

    # -------------------------------------------------------------------------
    # Cloudflare Tunnel / Server
    # -------------------------------------------------------------------------
    public_base_url: str = Field(default="", description="Public HTTPS URL from cloudflared tunnel")
    server_host: str = Field(default="0.0.0.0", description="Server host")
    server_port: int = Field(default=8000, description="Server port")
    admin_api_token: str = Field(default="", description="Bearer secret for admin endpoints")

    # -------------------------------------------------------------------------
    # Cloudflare R2 Media Storage
    # -------------------------------------------------------------------------
    r2_account_id: str = Field(default="", description="Cloudflare account ID")
    r2_access_key_id: str = Field(default="", description="R2 access key ID")
    r2_secret_access_key: str = Field(default="", description="R2 secret access key")
    r2_bucket_name: str = Field(default="", description="R2 bucket for generated media")
    r2_public_base_url: str = Field(default="", description="Public R2 custom domain")
    r2_object_prefix: str = Field(default="videos", description="R2 object key prefix")
    r2_state_bucket_name: str = Field(default="", description="Private R2 bucket for pipeline state")
    r2_state_object_key: str = Field(default="pipeline.db", description="SQLite state object key")
    r2_state_access_key_id: str = Field(default="", description="R2 state bucket access key ID")
    r2_state_secret_access_key: str = Field(default="", description="R2 state bucket secret access key")

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def server_base_url(self) -> str:
        return f"http://{self.server_host}:{self.server_port}"

    @property
    def media_base_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/media"

    # -------------------------------------------------------------------------
    # Database & Storage
    # -------------------------------------------------------------------------
    database_path: Path = Field(default=Path("data/pipeline.db"), description="SQLite database path")
    media_dir: Path = Field(default=Path("output"), description="Generated media output directory")
    assets_dir: Path = Field(default=Path("assets"), description="Assets directory")

    # -------------------------------------------------------------------------
    # Meesho Scraping
    # -------------------------------------------------------------------------
    meesho_base_url: str = Field(default="https://www.meesho.com", description="Meesho base URL")
    meesho_categories: List[str] = Field(
        default=["kurtis", "sarees", "ethnic-sets", "western-wear", "handbags"],
        description="Categories to scrape"
    )
    meesho_max_products_per_category: int = Field(default=20, ge=1, le=100)
    meesho_scrape_delay_ms: int = Field(default=2000, ge=500, description="Delay between requests")
    meesho_request_timeout_ms: int = Field(default=30000, ge=5000)

    innerwear_exclude_keywords: List[str] = Field(
        default=[
            "bra", "panty", "briefs", "boxer", "lingerie", "nightwear",
            "sleepwear", "innerwear", "underwear", "bikini", "thong",
            "g-string", "shapewear"
        ],
        description="Keywords that exclude a product (case-insensitive)"
    )

    @field_validator("meesho_categories", mode="before")
    @classmethod
    def parse_categories(cls, v):
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @field_validator("innerwear_exclude_keywords", mode="before")
    @classmethod
    def parse_innerwear_keywords(cls, v):
        if isinstance(v, str):
            return [k.strip().lower() for k in v.split(",") if k.strip()]
        return [k.lower() for k in v]

    # -------------------------------------------------------------------------
    # Script Generation
    # -------------------------------------------------------------------------
    script_target_words_min: int = Field(default=65, ge=10, description="Minimum words in script")
    script_target_words_max: int = Field(default=80, ge=10, description="Maximum words in script")
    script_target_duration_sec: int = Field(default=25, ge=15, le=60, description="Target video duration")
    script_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    script_max_tokens: int = Field(default=500, ge=100, le=2000)

    # -------------------------------------------------------------------------
    # TTS Configuration (Edge TTS)
    # -------------------------------------------------------------------------
    tts_voice: str = Field(default="te-IN-ShrutiNeural", description="Edge TTS voice")
    tts_rate: str = Field(default="+0%", description="Speech rate")
    tts_volume: str = Field(default="+0%", description="Speech volume")

    # -------------------------------------------------------------------------
    # Video Builder
    # -------------------------------------------------------------------------
    video_width: int = Field(default=1080, description="Video width")
    video_height: int = Field(default=1920, description="Video height")
    video_fps: int = Field(default=30, description="Frames per second")
    video_target_duration_sec: int = Field(default=25, description="Target video duration")
    video_codec: str = Field(default="libx264", description="Video codec")
    video_crf: int = Field(default=20, ge=0, le=51, description="Constant Rate Factor")
    video_preset: str = Field(default="medium", description="FFmpeg preset")
    video_pix_fmt: str = Field(default="yuv420p", description="Pixel format")

    # Subtitles
    subtitle_font_size: int = Field(default=48, ge=24, le=80)
    subtitle_color: str = Field(default="white")
    subtitle_outline_color: str = Field(default="black")
    subtitle_outline_width: int = Field(default=3, ge=1, le=10)
    subtitle_safe_zone_top: float = Field(default=0.15, ge=0.0, le=0.5)
    subtitle_safe_zone_bottom: float = Field(default=0.85, ge=0.5, le=1.0)
    subtitle_max_chars_per_line: int = Field(default=35, ge=20, le=50)
    subtitle_max_lines: int = Field(default=2, ge=1, le=4)

    # Ken Burns
    ken_burns_zoom_min: float = Field(default=1.0, ge=1.0, le=1.5)
    ken_burns_zoom_max: float = Field(default=1.15, ge=1.0, le=1.5)
    ken_burns_pan_range: float = Field(default=0.1, ge=0.0, le=0.3)

    # Background Music
    bgm_dir: Path = Field(default=Path("assets/bgm"), description="BGM directory")
    bgm_volume_db: int = Field(default=-18, le=0, description="BGM volume in dB (negative)")

    # Audio Ducking
    duck_threshold_db: int = Field(default=-20, le=0)
    duck_ratio: str = Field(default="4:1")
    duck_attack_ms: int = Field(default=10, ge=1)
    duck_release_ms: int = Field(default=100, ge=10)

    # -------------------------------------------------------------------------
    # YouTube Upload
    # -------------------------------------------------------------------------
    max_yt_uploads_per_day: int = Field(default=5, ge=1, le=10)
    yt_quota_limit_daily: int = Field(default=10000)
    yt_quota_per_upload: int = Field(default=1600)
    yt_category_id: int = Field(default=22, description="YouTube category ID")
    yt_privacy_status: str = Field(default="public")
    yt_tags: List[str] = Field(default=["fashion", "telugu", "meesho", "womenswear", "shorts"])
    yt_description_template: str = Field(
        default="Check out this amazing {category} from Meesho! 💃\n\nProduct: {title}\nPrice: ₹{price} (MRP ₹{original_price}, {discount}% OFF)\nFabric: {fabric}\nColor: {color}\nCode: {product_code}\n\n🔗 Buy here: {affiliate_url}\n\n#fashion #telugu #meesho #womensfashion #shorts"
    )

    @field_validator("yt_tags", mode="before")
    @classmethod
    def parse_yt_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    # -------------------------------------------------------------------------
    # Instagram Upload
    # -------------------------------------------------------------------------
    max_ig_uploads_per_day: int = Field(default=10, ge=1, le=50)
    ig_media_type: str = Field(default="REELS")
    ig_share_to_feed: bool = Field(default=False)
    ig_caption_template: str = Field(
        default="ఇదే అదభుతమైన {category}! 💃\n\nఉత్పత్తి: {title}\nధర: ₹{price} (MRP ₹{original_price}, {discount}% छूट)\nజీన్స్: {fabric}\nరంగు: {color}\nకోడ్: {product_code}\n\n🔗 లింక్ కోసం \"LINK\" కమెంట్ చేయండి - మాకు DM історlerle!\n\n#fashion #telugu #meesho #womensfashion #reels #కూర్తీ #సಾರಿ"
    )

    # -------------------------------------------------------------------------
    # Scheduler / Pipeline
    # -------------------------------------------------------------------------
    post_interval_minutes: int = Field(default=15, ge=5, le=1440)
    pipeline_dry_run: bool = Field(default=False)
    max_consecutive_errors: int = Field(default=5, ge=1)
    health_check_interval_minutes: int = Field(default=5, ge=1)

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_file: Path = Field(default=Path("logs/pipeline.log"))
    log_max_bytes: int = Field(default=10485760)
    log_backup_count: int = Field(default=5)

    # -------------------------------------------------------------------------
    # Affiliate
    # -------------------------------------------------------------------------
    meesho_affiliate_id: str = Field(default="", description="Meesho affiliate ID")
    meesho_affiliate_base_url: str = Field(default="https://meesho.com")

    def get_affiliate_url(self, product_id: str) -> str:
        """Generate affiliate URL for a product."""
        if self.meesho_affiliate_id:
            return f"{self.meesho_affiliate_base_url.rstrip('/')}/product/{product_id}?aff_id={self.meesho_affiliate_id}"
        return f"{self.meesho_affiliate_base_url.rstrip('/')}/product/{product_id}"


# Global settings instance
settings = Settings()
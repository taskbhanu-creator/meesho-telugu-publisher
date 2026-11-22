"""
Main CLI entry point for Meesho Fashion Channel automation.
Commands: init, extract, generate, upload-youtube, upload-instagram, pipeline, scheduler, server, quota, stats
"""
import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import settings
from src.database import init_db, get_pipeline_stats, get_daily_quota, DailyQuota
from src.extractor import extract_products, extract_category
from src.script_tts import generate_script_and_tts
from src.video_builder import build_video
from src.youtube_uploader import upload_to_youtube, check_youtube_quota
from src.instagram_uploader import upload_to_instagram, check_instagram_quota
from src.scheduler import run_scheduler, get_scheduler
from src.server_webhook import run_server
from src.state_storage import restore_database_from_r2, upload_database_to_r2
import structlog

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

app = typer.Typer(
    name="meesho-fashion",
    help="Telugu Fashion Channel Automation for Meesho Products",
    add_completion=False
)

console = Console()


# =============================================================================
# Helper Functions
# =============================================================================

def print_banner():
    """Print application banner."""
    console.print(Panel.fit(
        "[bold cyan]Meesho Fashion Channel Automation[/bold cyan]\n"
        "[dim]Telugu YouTube Shorts & Instagram Reels[/dim]",
        border_style="cyan"
    ))


async def ensure_directories():
    """Ensure all required directories exist."""
    dirs = [
        settings.media_dir,
        settings.assets_dir,
        settings.assets_dir / "fonts",
        settings.assets_dir / "bgm",
        settings.database_path.parent,
        Path("data"),
        Path("logs")
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def check_environment(required: Optional[list[str]] = None):
    """Check the settings needed by a command, using their actual field names."""
    setting_names = {
        "GEMINI_API_KEY": "gemini_api_key",
        "GOOGLE_CLIENT_ID": "google_client_id",
        "GOOGLE_CLIENT_SECRET": "google_client_secret",
        "GOOGLE_REFRESH_TOKEN": "google_refresh_token",
        "META_APP_SECRET": "meta_app_secret",
        "META_PAGE_ACCESS_TOKEN": "meta_page_access_token",
        "META_PAGE_ID": "meta_page_id",
        "META_IG_USER_ID": "meta_ig_user_id",
        "META_WEBHOOK_VERIFY_TOKEN": "meta_webhook_verify_token",
        "ADMIN_API_TOKEN": "admin_api_token",
        "PUBLIC_BASE_URL": "public_base_url",
    }
    required = required or list(setting_names)
    missing = []
    for var in required:
        if var == "PUBLIC_MEDIA_URL":
            if not settings.r2_public_base_url and not settings.public_base_url:
                missing.append("R2_PUBLIC_BASE_URL or PUBLIC_BASE_URL")
        elif not getattr(settings, setting_names[var], None):
            missing.append(var)

    if missing:
        console.print("[red]Missing required environment variables:[/red]")
        for var in missing:
            console.print(f"  - {var}")
        console.print("\nCopy [cyan].env.example[/cyan] to [cyan].env[/cyan] and fill in your values.")
        return False
    return True


# =============================================================================
# Commands
# =============================================================================

@app.command()
def init():
    """Initialize database and directories."""
    print_banner()
    console.print("[cyan]Initializing Meesho Fashion Channel...[/cyan]")

    asyncio.run(ensure_directories())
    init_db()

    console.print("[green]✓[/green] Directories created")
    console.print("[green]✓[/green] Database initialized")
    console.print("\n[dim]Next steps:[/dim]")
    console.print("1. Copy [cyan].env.example[/cyan] to [cyan].env[/cyan] and configure")
    console.print("2. Run [cyan]playwright install[/cyan] to install browsers")
    console.print("3. Download Noto Sans Telugu font to [cyan]assets/fonts/[/cyan]")
    console.print("4. Add royalty-free BGM to [cyan]assets/bgm/[/cyan]")
    console.print("5. Configure Google OAuth and Meta App credentials")


@app.command()
def extract(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Specific category to extract"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Run browser headless"),
    max_products: Optional[int] = typer.Option(None, "--max", "-m", help="Max products per category")
):
    """Extract products from Meesho."""
    print_banner()

    async def _extract():
        if category:
            console.print(f"[cyan]Extracting category: {category}[/cyan]")
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
                task = progress.add_task("Scraping...", total=None)
                products = await extract_category(category, headless=headless)
                progress.update(task, completed=True)
            console.print(f"[green]✓[/green] Extracted {len(products)} products from {category}")
        else:
            console.print("[cyan]Extracting all categories...[/cyan]")
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
                task = progress.add_task("Scraping all categories...", total=None)
                results = await extract_products(headless=headless)
                progress.update(task, completed=True)

            total = sum(len(p) for p in results.values())
            for cat, products in results.items():
                console.print(f"  [green]✓[/green] {cat}: {len(products)} products")
            console.print(f"\n[bold]Total: {total} products[/bold]")

    asyncio.run(_extract())


@app.command()
def generate(
    product_id: str = typer.Argument(..., help="Meesho product ID"),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory")
):
    """Generate script and TTS for a product."""
    print_banner()

    if not check_environment(["GEMINI_API_KEY"]):
        raise typer.Exit(1)

    from src.database import get_product_by_id

    product = get_product_by_id(product_id)
    if not product:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1)

    out_dir = Path(output_dir) if output_dir else settings.media_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    async def _generate():
        console.print(f"[cyan]Generating script and TTS for: {product.title}[/cyan]")
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Generating...", total=None)
            result = await generate_script_and_tts(product, str(out_dir))
            progress.update(task, completed=True)

        console.print(f"[green]✓[/green] Script: {result.word_count} words (~{result.estimated_duration:.1f}s)")
        console.print(f"[green]✓[/green] Audio: {result.audio_path}")
        console.print(f"\n[dim]Telugu:[/dim] {result.telugu_script}")
        console.print(f"[dim]English:[/dim] {result.english_translation}")

    asyncio.run(_generate())


@app.command()
def build_video(
    product_id: str = typer.Argument(..., help="Meesho product ID"),
    script: Optional[str] = typer.Option(None, "--script", "-s", help="Telugu script (uses generated if not provided)"),
    voiceover: Optional[str] = typer.Option(None, "--voiceover", "-v", help="Voiceover audio path"),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory")
):
    """Build video from product, script, and voiceover."""
    print_banner()

    from src.database import get_product_by_id

    product = get_product_by_id(product_id)
    if not product:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1)

    out_dir = Path(output_dir) if output_dir else settings.media_dir

    async def _build():
        # Get script and voiceover if not provided
        telugu_script = script
        voiceover_path = voiceover

        if not telugu_script or not voiceover_path:
            console.print("[cyan]Generating script and TTS...[/cyan]")
            result = await generate_script_and_tts(product, str(out_dir))
            telugu_script = telugu_script or result.telugu_script
            voiceover_path = voiceover_path or result.audio_path

        console.print(f"[cyan]Building video for: {product.title}[/cyan]")
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Building video...", total=None)
            result = await build_video(product, telugu_script, voiceover_path, str(out_dir))
            progress.update(task, completed=True)

        console.print(f"[green]✓[/green] Video: {result.video_path}")
        console.print(f"[green]✓[/green] Duration: {result.duration:.1f}s")
        console.print(f"[green]✓[/green] Size: {result.file_size_mb:.1f} MB")

    asyncio.run(_build())


@app.command("upload-youtube")
def upload_youtube(
    video_path: str = typer.Argument(..., help="Path to video file"),
    product_id: str = typer.Argument(..., help="Meesho product ID"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Custom title"),
    privacy: Optional[str] = typer.Option(None, "--privacy", "-p", help="Privacy status (public/unlisted/private)")
):
    """Upload video to YouTube."""
    print_banner()

    if not check_environment([
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"
    ]):
        raise typer.Exit(1)

    from src.database import get_product_by_id

    product = get_product_by_id(product_id)
    if not product:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1)

    video_file = Path(video_path)
    if not video_file.exists():
        console.print(f"[red]Video file not found: {video_path}[/red]")
        raise typer.Exit(1)

    async def _upload():
        console.print(f"[cyan]Uploading to YouTube: {video_file.name}[/cyan]")

        # Check quota
        quota = await check_youtube_quota()
        console.print(f"[dim]YouTube quota: {quota.remaining}/{quota.limit} remaining[/dim]")

        if not quota.can_upload:
            console.print("[red]Daily YouTube quota exhausted[/red]")
            raise typer.Exit(1)

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Uploading...", total=None)
            result = await upload_to_youtube(
                str(video_file), product,
                title=title,
                privacy_status=privacy
            )
            progress.update(task, completed=True)

        console.print(f"[green]✓[/green] Uploaded: {result.video_url}")
        console.print(f"[green]✓[/green] Video ID: {result.video_id}")

    asyncio.run(_upload())


@app.command("upload-instagram")
def upload_instagram(
    video_url: str = typer.Argument(..., help="Public URL of video (served via /media/)"),
    product_id: str = typer.Argument(..., help="Meesho product ID"),
    caption: Optional[str] = typer.Option(None, "--caption", "-c", help="Custom caption")
):
    """Upload video to Instagram Reels."""
    print_banner()

    if not check_environment([
        "META_PAGE_ACCESS_TOKEN", "META_IG_USER_ID"
    ]):
        raise typer.Exit(1)

    from src.database import get_product_by_id

    product = get_product_by_id(product_id)
    if not product:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1)

    async def _upload():
        console.print(f"[cyan]Uploading to Instagram Reels[/cyan]")

        # Check quota
        quota = await check_instagram_quota()
        console.print(f"[dim]Instagram quota: {quota['remaining']}/{quota['limit']} remaining[/dim]")

        if not quota["can_upload"]:
            console.print("[red]Daily Instagram quota exhausted[/red]")
            raise typer.Exit(1)

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Uploading (3-step process)...", total=None)
            result = await upload_to_instagram(video_url, product, caption=caption)
            progress.update(task, completed=True)

        console.print(f"[green]✓[/green] Published: {result.permalink}")
        console.print(f"[green]✓[/green] Media ID: {result.media_id}")

    asyncio.run(_upload())


@app.command()
def pipeline(
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without actual uploads"),
    once: bool = typer.Option(False, "--once", help="Run once and exit")
):
    """Run the full pipeline (extract -> generate -> build -> upload)."""
    print_banner()

    required_settings = ["GEMINI_API_KEY"]
    if not dry_run:
        required_settings.extend([
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
            "META_PAGE_ACCESS_TOKEN", "META_IG_USER_ID", "PUBLIC_MEDIA_URL",
        ])
    if not check_environment(required_settings):
        raise typer.Exit(1)

    if dry_run:
        console.print("[yellow]Running in DRY-RUN mode - no actual uploads[/yellow]")

    async def _pipeline():
        from src.scheduler import PipelineScheduler

        scheduler = PipelineScheduler(dry_run=dry_run)

        if once:
            console.print("[cyan]Running pipeline once...[/cyan]")
            result = await scheduler.run_pipeline()

            # Print results
            table = Table(title=f"Pipeline Run: {result.run_id}")
            table.add_column("Step", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Message")
            table.add_column("Duration (ms)")

            for step in result.steps:
                status = "[green]✓[/green]" if step.success else "[red]✗[/red]"
                table.add_row(step.step, status, step.message, str(step.duration_ms))

            console.print(table)
            console.print(f"\n[bold]Products processed: {result.products_processed}[/bold]")
            console.print(f"[bold]YouTube uploaded: {result.youtube_uploaded}[/bold]")
            console.print(f"[bold]Instagram uploaded: {result.instagram_uploaded}[/bold]")

            if result.errors:
                console.print("\n[red]Errors:[/red]")
                for err in result.errors:
                    console.print(f"  - {err}")
        else:
            console.print("[cyan]Starting continuous pipeline...[/cyan]")
            await run_scheduler(dry_run=dry_run)

    asyncio.run(_pipeline())


@app.command()
def scheduler(
    dry_run: bool = typer.Option(False, "--dry-run", help="Run in dry-run mode")
):
    """Start the continuous scheduler."""
    print_banner()

    required_settings = ["GEMINI_API_KEY"]
    if not dry_run:
        required_settings.extend([
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
            "META_PAGE_ACCESS_TOKEN", "META_IG_USER_ID", "PUBLIC_MEDIA_URL",
        ])
    if not check_environment(required_settings):
        raise typer.Exit(1)

    if dry_run:
        console.print("[yellow]Running scheduler in DRY-RUN mode[/yellow]")

    console.print("[cyan]Starting scheduler... Press Ctrl+C to stop[/cyan]")
    asyncio.run(run_scheduler(dry_run=dry_run))


@app.command()
def server(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
    port: int = typer.Option(8000, "--port", help="Port to bind")
):
    """Start the FastAPI server for media hosting and webhooks."""
    print_banner()

    if not check_environment([
        "META_APP_SECRET", "META_PAGE_ACCESS_TOKEN",
        "META_PAGE_ID", "META_WEBHOOK_VERIFY_TOKEN", "ADMIN_API_TOKEN",
    ]):
        raise typer.Exit(1)

    console.print(f"[cyan]Starting server on {host}:{port}[/cyan]")
    console.print(f"[dim]Media URL: {settings.media_base_url}[/dim]")
    console.print(f"[dim]Webhook URL: {settings.public_base_url}/webhook/instagram[/dim]")

    run_server(host=host, port=port)


@app.command()
def quota():
    """Check API quota status."""
    print_banner()

    async def _quota():
        yt_quota = await check_youtube_quota()
        ig_quota = await check_instagram_quota()

        table = Table(title="API Quota Status")
        table.add_column("Platform", style="cyan")
        table.add_column("Used Today", style="yellow")
        table.add_column("Limit", style="green")
        table.add_column("Remaining", style="green")
        table.add_column("Can Upload", style="bold")

        table.add_row(
            "YouTube",
            str(yt_quota.used_today),
            str(yt_quota.limit),
            str(yt_quota.remaining),
            "✓ Yes" if yt_quota.can_upload else "✗ No"
        )

        table.add_row(
            "Instagram",
            str(ig_quota["used_today"]),
            str(ig_quota["limit"]),
            str(ig_quota["remaining"]),
            "✓ Yes" if ig_quota["can_upload"] else "✗ No"
        )

        console.print(table)

        # Show reset time
        console.print(f"\n[dim]YouTube quota resets at: {yt_quota.reset_time.strftime('%Y-%m-%d %H:%M UTC')}[/dim]")

    asyncio.run(_quota())


@app.command()
def stats():
    """Show pipeline statistics."""
    print_banner()

    stats = get_pipeline_stats()

    table = Table(title="Pipeline Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Total Products", str(stats.get("total_products", 0)))
    table.add_row("Published YouTube", str(stats.get("published_youtube", 0)))
    table.add_row("Published Instagram", str(stats.get("published_instagram", 0)))
    table.add_row("Pending Products", str(stats.get("pending_products", 0)))
    table.add_row("Total Pipeline Runs", str(stats.get("total_runs", 0)))
    table.add_row("Successful Runs", str(stats.get("successful_runs", 0)))
    table.add_row("Failed Runs", str(stats.get("failed_runs", 0)))

    console.print(table)

    # Today's quota
    from src.database import get_daily_quota
    from datetime import date

    today = date.today().isoformat()
    for qtype in ["youtube_uploads", "ig_uploads", "youtube_quota"]:
        q = get_daily_quota(today, qtype)
        if q:
            count = {
                "youtube_uploads": q.youtube_uploads,
                "ig_uploads": q.instagram_uploads,
                "youtube_quota": q.youtube_quota_used,
            }[qtype]
            console.print(f"[dim]Today {qtype}: {count}[/dim]")


@app.command("restore-state")
def restore_state():
    """Restore pipeline SQLite state from the private R2 bucket."""
    restored = restore_database_from_r2()
    message = "Restored pipeline state from R2" if restored else "Initialized fresh pipeline database"
    console.print(f"[green]✓[/green] {message}")


@app.command("persist-state")
def persist_state():
    """Upload a consistent pipeline SQLite snapshot to the private R2 bucket."""
    upload_database_to_r2()
    console.print("[green]✓[/green] Pipeline state saved to R2")


@app.command()
def test_fonts():
    """Test Telugu font rendering."""
    print_banner()

    from src.video_builder import SubtitleRenderer
    from PIL import Image

    console.print("[cyan]Testing Telugu font...[/cyan]")

    renderer = SubtitleRenderer()
    console.print(f"[green]✓[/green] Font: {renderer.font_path}")

    # Create test frame
    img = Image.new("RGB", (1080, 1920), color=(50, 50, 100))
    test_text = "అదృష్టంగా కสิ్యం నుండి తయారుైన మలుపు కుర్తీ ఇప్పుడు ₹299 లే"

    result = renderer.render_subtitle_frame(img, test_text)

    output_path = settings.media_dir / "font_test.jpg"
    result.save(output_path, "JPEG", quality=95)

    console.print(f"[green]✓[/green] Test image saved: {output_path}")
    console.print(f"[dim]Text rendered: {test_text}[/dim]")


@app.command()
def test_tts(
    text: str = typer.Argument("అదృష్టంగా కších నుండి తయారుైన మలుపు కుర్తీ", help="Telugu text to synthesize"),
    voice: str = typer.Option("te-IN-ShrutiNeural", "--voice", help="Edge TTS voice"),
    output: str = typer.Option("test_tts.mp3", "--output", "-o", help="Output file")
):
    """Test Edge TTS synthesis."""
    print_banner()

    async def _test():
        import edge_tts

        console.print(f"[cyan]Synthesizing with {voice}...[/cyan]")

        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(output)

        console.print(f"[green]✓[/green] Audio saved: {output}")

        # Get duration
        import subprocess
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", output
        ], capture_output=True, text=True)

        if result.returncode == 0:
            duration = float(result.stdout.strip())
            console.print(f"[dim]Duration: {duration:.1f}s[/dim]")

    asyncio.run(_test())


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    app()
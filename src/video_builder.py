"""
Video builder using Pillow for frames and FFmpeg for encoding.
Creates 1080x1920 vertical videos with Telugu subtitles, Ken Burns effect, and audio ducking.
"""
import asyncio
from io import BytesIO
import os
import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont
import requests

from src.config import settings
from src.database import Product
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SubtitleLine:
    """A single subtitle line with timing."""
    text: str
    start_time: float
    end_time: float


@dataclass
class VideoResult:
    """Result of video generation."""
    video_path: str
    duration: float
    file_size_mb: float


# =============================================================================
# Subtitle Renderer
# =============================================================================

class SubtitleRenderer:
    """Renders Telugu subtitles on video frames using Pillow."""

    def __init__(self):
        self.font_path = self._find_telugu_font()
        self.font_size = settings.subtitle_font_size
        self.color = settings.subtitle_color
        self.outline_color = settings.subtitle_outline_color
        self.outline_width = settings.subtitle_outline_width
        self.max_chars_per_line = settings.subtitle_max_chars_per_line
        self.max_lines = settings.subtitle_max_lines

        # Safe zone (percentage of video height)
        self.safe_top = settings.subtitle_safe_zone_top
        self.safe_bottom = settings.subtitle_safe_zone_bottom

    def _find_telugu_font(self) -> str:
        """Find or download Noto Sans Telugu font."""
        # Check local assets/fonts first
        font_paths = [
            settings.assets_dir / "fonts" / "NotoSansTelugu-Regular.ttf",
            settings.assets_dir / "fonts" / "NotoSansTelugu-VariableFont_wdth,wght.ttf",
            Path("/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf"),  # Linux
            Path("/System/Library/Fonts/NotoSansTelugu-Regular.ttf"),  # macOS
            Path("C:/Windows/Fonts/NotoSansTelugu-Regular.ttf"),  # Windows
        ]

        for path in font_paths:
            if path.exists():
                logger.info(f"Found Telugu font at {path}")
                return str(path)

        # Download if not found
        return self._download_font()

    def _download_font(self) -> str:
        """Download Noto Sans Telugu font from Google Fonts."""
        font_dir = settings.assets_dir / "fonts"
        font_dir.mkdir(parents=True, exist_ok=True)
        font_path = font_dir / "NotoSansTelugu-Regular.ttf"

        if font_path.exists():
            return str(font_path)

        try:
            logger.info("Downloading Noto Sans Telugu font...")
            url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansTelugu/NotoSansTelugu-Regular.ttf"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            font_path.write_bytes(response.content)
            logger.info(f"Font downloaded to {font_path}")
            return str(font_path)
        except Exception as e:
            logger.error(f"Failed to download font: {e}")
            # Fallback to default
            return "arial.ttf"

    def _get_font(self, size: Optional[int] = None) -> ImageFont.FreeTypeFont:
        """Get font with specified size."""
        return ImageFont.truetype(self.font_path, size or self.font_size)

    def wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        """Wrap text to fit within max_width."""
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = font.getbbox(test_line)
            width = bbox[2] - bbox[0]

            if width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines[:self.max_lines]

    def render_subtitle_frame(
        self,
        base_image: Image.Image,
        text: str,
        progress: float = 1.0
    ) -> Image.Image:
        """Render subtitle text on a frame with outline."""
        if not text.strip():
            return base_image

        img = base_image.copy()
        draw = ImageDraw.Draw(img)
        font = self._get_font()

        # Calculate safe zone
        video_width, video_height = img.size
        safe_top_px = int(video_height * self.safe_top)
        safe_bottom_px = int(video_height * self.safe_bottom)
        safe_height = safe_bottom_px - safe_top_px
        max_width = int(video_width * 0.9)  # 90% of width

        # Wrap text
        lines = self.wrap_text(text, font, max_width)

        # Calculate total text height
        line_heights = []
        for line in lines:
            bbox = font.getbbox(line)
            line_heights.append(bbox[3] - bbox[1])
        total_text_height = sum(line_heights) + (len(lines) - 1) * 10  # 10px line spacing

        # Position: center vertically in safe zone
        start_y = safe_top_px + (safe_height - total_text_height) // 2

        # Draw each line with outline
        y = start_y
        for line in lines:
            bbox = font.getbbox(line)
            text_width = bbox[2] - bbox[0]
            x = (video_width - text_width) // 2

            # Draw outline (multiple offsets for thickness)
            for dx in range(-self.outline_width, self.outline_width + 1):
                for dy in range(-self.outline_width, self.outline_width + 1):
                    if dx != 0 or dy != 0:
                        draw.text(
                            (x + dx, y + dy),
                            line,
                            font=font,
                            fill=self.outline_color
                        )

            # Draw main text
            draw.text((x, y), line, font=font, fill=self.color)
            y += (bbox[3] - bbox[1]) + 10

        return img


# =============================================================================
# Frame Generator (Ken Burns Effect)
# =============================================================================

class FrameGenerator:
    """Generates video frames with Ken Burns pan/zoom effect."""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height
        self.zoom_min = settings.ken_burns_zoom_min
        self.zoom_max = settings.ken_burns_zoom_max
        self.pan_range = settings.ken_burns_pan_range

    def create_background_frame(self, image_path: str, zoom_factor: float = 1.0, pan_x: float = 0.0, pan_y: float = 0.0) -> Image.Image:
        """Create a single frame with zoom and pan applied."""
        try:
            img = Image.open(image_path).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to load image {image_path}: {e}, using fallback")
            return self._create_fallback_frame()

        # Resize to cover the video dimensions with zoom
        img_width, img_height = img.size
        video_aspect = self.width / self.height
        img_aspect = img_width / img_height

        if img_aspect > video_aspect:
            # Image is wider - fit height
            new_height = int(self.height * zoom_factor)
            new_width = int(new_height * img_aspect)
        else:
            # Image is taller - fit width
            new_width = int(self.width * zoom_factor)
            new_height = int(new_width / img_aspect)

        # Resize with high quality
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Calculate crop position with pan
        max_x = max(0, new_width - self.width)
        max_y = max(0, new_height - self.height)

        # Pan offsets (-1 to 1) mapped to available range
        crop_x = int(max_x * (0.5 + pan_x * self.pan_range))
        crop_y = int(max_y * (0.5 + pan_y * self.pan_range))

        # Ensure crop is within bounds
        crop_x = max(0, min(crop_x, max_x))
        crop_y = max(0, min(crop_y, max_y))

        # Crop to video dimensions
        frame = img.crop((crop_x, crop_y, crop_x + self.width, crop_y + self.height))

        return frame

    def _create_fallback_frame(self) -> Image.Image:
        """Create a gradient fallback frame."""
        img = Image.new("RGB", (self.width, self.height))
        draw = ImageDraw.Draw(img)
        # Create vertical gradient
        for y in range(self.height):
            r = int(100 + (y / self.height) * 50)
            g = int(50 + (y / self.height) * 50)
            b = int(150 + (y / self.height) * 50)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))
        return img

    def generate_frame_sequence(
        self,
        image_paths: List[str],
        duration: float,
        subtitle_text: str,
        subtitle_renderer: SubtitleRenderer,
        fps: int = 30
    ) -> List[Image.Image]:
        """Generate a frame list for callers that need in-memory frames."""
        return list(self.iter_frame_sequence(
            image_paths, duration, subtitle_text, subtitle_renderer, fps
        ))

    def iter_frame_sequence(
        self,
        image_paths: List[str],
        duration: float,
        subtitle_text: str,
        subtitle_renderer: SubtitleRenderer,
        fps: int = 30
    ):
        """Yield frames incrementally to keep video generation memory-bounded."""
        total_frames = int(duration * fps)
        if total_frames <= 0:
            raise ValueError("Video duration and frame rate must produce at least one frame")

        if not image_paths:
            base_frame = self._create_fallback_frame()
            for i in range(total_frames):
                progress = i / max(1, total_frames - 1)
                yield subtitle_renderer.render_subtitle_frame(base_frame, subtitle_text, progress)
            return

        frames_per_image = max(1, total_frames // len(image_paths))
        remaining_frames = total_frames
        generated_frames = 0

        for img_idx, img_path in enumerate(image_paths):
            if remaining_frames <= 0:
                break

            img_frames = min(frames_per_image, remaining_frames)
            if img_idx == len(image_paths) - 1:
                img_frames = remaining_frames

            # Random Ken Burns parameters for this image
            zoom_start = random.uniform(self.zoom_min, self.zoom_max)
            zoom_end = random.uniform(self.zoom_min, self.zoom_max)
            pan_x_start = random.uniform(-1, 1)
            pan_x_end = random.uniform(-1, 1)
            pan_y_start = random.uniform(-1, 1)
            pan_y_end = random.uniform(-1, 1)

            for frame_idx in range(img_frames):
                progress = frame_idx / max(1, img_frames - 1) if img_frames > 1 else 0.5

                # Interpolate zoom and pan
                zoom = zoom_start + (zoom_end - zoom_start) * progress
                pan_x = pan_x_start + (pan_x_end - pan_x_start) * progress
                pan_y = pan_y_start + (pan_y_end - pan_y_start) * progress

                base_frame = self.create_background_frame(img_path, zoom, pan_x, pan_y)
                yield subtitle_renderer.render_subtitle_frame(base_frame, subtitle_text, progress)
                generated_frames += 1

            remaining_frames -= img_frames

        while generated_frames < total_frames:
            yield self._create_fallback_frame()
            generated_frames += 1


# =============================================================================
# Audio Mixer (FFmpeg Sidechain Compression)
# =============================================================================

class AudioMixer:
    """Mixes voiceover with BGM using FFmpeg sidechain compression for ducking."""

    def __init__(self, bgm_dir: str):
        self.bgm_dir = Path(bgm_dir)
        self.bgm_volume_db = settings.bgm_volume_db
        self.duck_threshold_db = settings.duck_threshold_db
        self.duck_ratio = settings.duck_ratio
        self.duck_attack_ms = settings.duck_attack_ms
        self.duck_release_ms = settings.duck_release_ms

    def get_bgm_file(self) -> Optional[Path]:
        """Get a random BGM file from the directory."""
        if not self.bgm_dir.exists():
            return None

        bgm_files = list(self.bgm_dir.glob("*.mp3")) + list(self.bgm_dir.glob("*.wav"))
        if not bgm_files:
            return None

        return random.choice(bgm_files)

    async def mix_audio(
        self,
        voiceover_path: str,
        output_path: str,
        duration: float
    ) -> str:
        """
        Mix voiceover with BGM using sidechain compression.
        Voiceover is primary, BGM ducks when voice is present.
        """
        bgm_file = self.get_bgm_file()

        if not bgm_file:
            logger.warning("No BGM file found, copying voiceover only")
            shutil.copy2(voiceover_path, output_path)
            return output_path

        # Build FFmpeg filter complex for sidechain compression
        # [0:a] = voiceover, [1:a] = BGM
        filter_complex = (
            f"[1:a]volume={self.bgm_volume_db}dB[bgm];"
            f"[0:a][bgm]sidechaincompress="
            f"threshold={10 ** (self.duck_threshold_db / 20):.6f}:"
            f"ratio={float(self.duck_ratio.split(':', 1)[0])}:"
            f"attack={self.duck_attack_ms}:"
            f"release={self.duck_release_ms}"
        )

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i", voiceover_path,
            "-stream_loop", "-1",
            "-i", str(bgm_file),
            "-filter_complex", filter_complex,
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", str(duration),
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg audio mix failed: {stderr.decode(errors='replace')}")

        return output_path


# =============================================================================
# Video Encoder (FFmpeg)
# =============================================================================

class VideoEncoder:
    """Encodes frames to video using FFmpeg."""

    def __init__(self):
        self.fps = settings.video_fps
        self.codec = settings.video_codec
        self.crf = settings.video_crf
        self.preset = settings.video_preset
        self.pix_fmt = settings.video_pix_fmt
        self.width = settings.video_width
        self.height = settings.video_height

    async def encode(
        self,
        frames: Iterable[Image.Image],
        audio_path: str,
        output_path: str
    ) -> VideoResult:
        """Encode frames and audio to MP4 video."""
        frame_count = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, frame in enumerate(frames):
                frame_path = Path(tmpdir) / f"frame_{i:06d}.jpg"
                frame.save(frame_path, "JPEG", quality=95)
                frame_count = i + 1

            if frame_count == 0:
                raise ValueError("Cannot encode a video without frames")

            input_pattern = str(Path(tmpdir) / "frame_%06d.jpg")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-framerate", str(self.fps),
                "-i", input_pattern,
                "-i", audio_path,
                "-c:v", self.codec,
                "-crf", str(self.crf),
                "-preset", self.preset,
                "-pix_fmt", self.pix_fmt,
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                "-shortest",
                output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                error = stderr.decode(errors="replace")
                logger.error(f"FFmpeg encoding failed: {error}")
                raise RuntimeError(f"FFmpeg failed: {error}")

        # Get video info
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        duration = frame_count / self.fps

        logger.info(f"Video encoded: {output_path} ({file_size_mb:.1f} MB, {duration:.1f}s)")
        return VideoResult(
            video_path=output_path,
            duration=duration,
            file_size_mb=file_size_mb
        )


# =============================================================================
# Video Builder (Main Pipeline)
# =============================================================================

class VideoBuilder:
    """Main video building pipeline combining all components."""

    def __init__(self):
        self.subtitle_renderer = SubtitleRenderer()
        self.frame_generator = FrameGenerator(settings.video_width, settings.video_height)
        self.audio_mixer = AudioMixer(settings.bgm_dir)
        self.video_encoder = VideoEncoder()

    async def build_video(
        self,
        product: Product,
        telugu_script: str,
        voiceover_path: str,
        output_dir: str
    ) -> VideoResult:
        """Build complete video for a product."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_filename = f"{product.meesho_product_id}_video.mp4"
        output_path = output_dir / output_filename

        # Mixed audio path
        mixed_audio_path = output_dir / f"{product.meesho_product_id}_mixed.m4a"

        logger.info(f"Building video for {product.meesho_product_id}")

        try:
            voice_duration = await self._get_audio_duration(voiceover_path)
            target_duration = min(max(voice_duration, 20), settings.video_target_duration_sec)
            await self.audio_mixer.mix_audio(voiceover_path, str(mixed_audio_path), target_duration)

            mixed_duration = await self._get_audio_duration(str(mixed_audio_path))
            with tempfile.TemporaryDirectory() as image_tmpdir:
                image_paths = await self._prepare_images(product.image_urls, Path(image_tmpdir))
                frames = self.frame_generator.iter_frame_sequence(
                    image_paths=image_paths,
                    duration=mixed_duration,
                    subtitle_text=telugu_script,
                    subtitle_renderer=self.subtitle_renderer,
                    fps=settings.video_fps
                )
                result = await self.video_encoder.encode(
                    frames, str(mixed_audio_path), str(output_path)
                )
            return result
        finally:
            if mixed_audio_path.exists():
                try:
                    mixed_audio_path.unlink()
                except OSError as error:
                    logger.warning(f"Failed to remove temporary audio {mixed_audio_path}: {error}")

    async def _prepare_images(self, image_urls: List[str], temp_dir: Path) -> List[str]:
        """Resolve local image paths and download remote product images."""
        paths = []
        for index, image_url in enumerate(image_urls):
            parsed_url = urlparse(image_url)
            if parsed_url.scheme in {"http", "https"}:
                image_path = temp_dir / f"product_{index:02d}.image"
                try:
                    await asyncio.to_thread(self._download_image, image_url, image_path)
                    paths.append(str(image_path))
                except Exception as error:
                    logger.warning(f"Failed to download product image {image_url}: {error}")
            else:
                image_path = Path(image_url)
                if image_path.is_file():
                    paths.append(str(image_path))
                else:
                    logger.warning(f"Product image path does not exist: {image_url}")

        if image_urls and not paths:
            raise RuntimeError("Could not retrieve any product images; refusing to build a placeholder video")
        return paths

    @staticmethod
    def _download_image(image_url: str, image_path: Path):
        response = requests.get(
            image_url,
            timeout=(10, 30),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        content = response.content
        if not content:
            raise RuntimeError("Image response was empty")
        with Image.open(BytesIO(content)) as image:
            image.verify()
        image_path.write_bytes(content)

    async def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration using FFprobe."""
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {stderr.decode(errors='replace')}")
        try:
            duration = float(stdout.decode().strip())
        except ValueError as error:
            raise RuntimeError(f"FFprobe returned an invalid duration for {audio_path}") from error
        if duration <= 0:
            raise RuntimeError(f"Audio has an invalid duration: {audio_path}")
        return duration


# =============================================================================
# Convenience Functions
# =============================================================================

async def build_video(
    product: Product,
    telugu_script: str,
    voiceover_path: str,
    output_dir: str
) -> VideoResult:
    """Convenience function to build a video."""
    builder = VideoBuilder()
    return await builder.build_video(product, telugu_script, voiceover_path, output_dir)
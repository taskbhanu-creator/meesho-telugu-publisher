"""
Tests for video builder module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from pathlib import Path
import tempfile

from src.video_builder import (
    SubtitleRenderer, FrameGenerator, AudioMixer, VideoEncoder, VideoBuilder,
    SubtitleLine, VideoResult
)
from src.database import Product
from datetime import datetime


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


class TestSubtitleRenderer:
    """Test subtitle rendering."""

    def test_init_with_default_font(self):
        """Test initialization with default font path."""
        renderer = SubtitleRenderer()
        assert renderer.font_size == 48
        assert renderer.safe_zone_top == 0.15
        assert renderer.safe_zone_bottom == 0.85

    def test_init_with_custom_font(self):
        """Test initialization with custom font."""
        renderer = SubtitleRenderer(font_size=36, font_path="/custom/font.ttf")
        assert renderer.font_size == 36
        assert str(renderer.font_path) == "/custom/font.ttf"

    def test_wrap_text_single_line(self):
        """Test text wrapping for single line."""
        renderer = SubtitleRenderer()

        # Mock the font measurement
        with patch.object(renderer, '_get_text_width', return_value=500):
            lines = renderer._wrap_text("అదృష్టంగా కుర్తీ ₹299", max_width=1000)
            assert len(lines) == 1
            assert lines[0] == "అదృష్టங்கள் కుర్తీ ₹299"

    def test_wrap_text_multiple_lines(self):
        """Test text wrapping for multiple lines."""
        renderer = SubtitleRenderer()

        # Mock font measurement - first call > max_width, subsequent < max_width
        widths = [1200, 500, 500]  # First combined, then each word

        with patch.object(renderer, '_get_text_width', side_effect=widths):
            lines = renderer._wrap_text("అదృష్టంగా చాలా సుందరమైన కుర్తీ", max_width=1000)
            assert len(lines) >= 1

    def test_split_script_into_lines(self):
        """Test splitting script into subtitle lines."""
        renderer = SubtitleRenderer()

        script = "అదృష్టంగా కుర్తీ ₹299 లో. చాలా సుందరంగా ఉంది. YouTube description లో లింక్ ఉంది."
        lines = renderer.split_script_into_lines(script, duration=25.0)

        assert len(lines) >= 1
        for line in lines:
            assert isinstance(line, SubtitleLine)
            assert line.start_time >= 0
            assert line.end_time <= 25.0
            assert line.text

    def test_render_subtitle_frame(self, tmp_path):
        """Test rendering subtitle frame."""
        renderer = SubtitleRenderer(font_size=48)

        # Create a simple frame
        from PIL import Image
        frame = Image.new('RGB', (1080, 1920), color='black')

        line = SubtitleLine(
            text="అదృష్టంగా కుర్తీ",
            start_time=0.0,
            end_time=5.0
        )

        result = renderer.render_subtitle_frame(frame, line, current_time=2.5)
        assert result.size == (1080, 1920)
        assert result.mode == 'RGB'


class TestFrameGenerator:
    """Test frame generation with Ken Burns effect."""

    def test_init_defaults(self):
        """Test default initialization."""
        generator = FrameGenerator()
        assert generator.width == 1080
        assert generator.height == 1920
        assert generator.zoom_range == (1.0, 1.15)
        assert generator.pan_range == 0.1

    def test_init_custom(self):
        """Test custom initialization."""
        generator = FrameGenerator(width=720, height=1280, zoom_range=(1.0, 1.2), pan_range=0.2)
        assert generator.width == 720
        assert generator.height == 1280
        assert generator.zoom_range == (1.0, 1.2)
        assert generator.pan_range == 0.2

    def test_calculate_ken_burns_transform(self):
        """Test Ken Burns transform calculation."""
        generator = FrameGenerator()

        # Test at start (progress=0)
        transform = generator._calculate_ken_burns_transform(
            img_width=1080, img_height=1920, progress=0.0
        )
        assert 'scale' in transform
        assert 'x' in transform
        assert 'y' in transform
        assert transform['scale'] >= 1.0
        assert transform['scale'] <= 1.15

        # Test at end (progress=1.0)
        transform = generator._calculate_ken_burns_transform(
            img_width=1080, img_height=1920, progress=1.0
        )
        assert transform['scale'] >= 1.0
        assert transform['scale'] <= 1.15

    def test_generate_frames_for_image(self, tmp_path):
        """Test frame generation for an image."""
        generator = FrameGenerator()

        # Create a test image
        from PIL import Image
        test_img = tmp_path / "test.jpg"
        img = Image.new('RGB', (1080, 1920), color='red')
        img.save(test_img)

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        frame_paths = generator.generate_frames_for_image(
            str(test_img),
            str(frames_dir),
            duration=5.0,
            fps=30
        )

        assert len(frame_paths) == 150  # 5 seconds * 30 fps
        assert all(Path(p).exists() for p in frame_paths)


class TestAudioMixer:
    """Test audio mixing with sidechain compression."""

    def test_init_defaults(self):
        """Test default initialization."""
        mixer = AudioMixer()
        assert mixer.bgm_volume_db == -18
        assert mixer.sidechain_threshold_db == -20
        assert mixer.sidechain_ratio == 4
        assert mixer.attack_ms == 10
        assert mixer.release_ms == 100

    def test_init_custom(self):
        """Test custom initialization."""
        mixer = AudioMixer(
            bgm_volume_db=-12,
            sidechain_threshold_db=-15,
            sidechain_ratio=3,
            attack_ms=5,
            release_ms=50
        )
        assert mixer.bgm_volume_db == -12
        assert mixer.sidechain_threshold_db == -15
        assert mixer.sidechain_ratio == 3
        assert mixer.attack_ms == 5
        assert mixer.release_ms == 50

    @pytest.mark.asyncio
    async def test_mix_audio(self, tmp_path):
        """Test audio mixing."""
        mixer = AudioMixer()

        # Create dummy audio files
        voice_path = tmp_path / "voice.mp3"
        bgm_path = tmp_path / "bgm.mp3"
        output_path = tmp_path / "output.mp3"

        # Create empty files
        voice_path.write_bytes(b"dummy voice")
        bgm_path.write_bytes(b"dummy bgm")

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await mixer.mix_audio(str(voice_path), str(bgm_path), str(output_path), duration=30.0)

            mock_subprocess.assert_called_once()
            # Check ffmpeg command includes sidechain parameters
            call_args = mock_subprocess.call_args[0]
            assert 'ffmpeg' in call_args[0]
            assert 'sidechain' in ' '.join(call_args).lower() or 'acompressor' in ' '.join(call_args).lower()


class TestVideoEncoder:
    """Test video encoding."""

    def test_init_defaults(self):
        """Test default initialization."""
        encoder = VideoEncoder()
        assert encoder.crf == 20
        assert encoder.preset == 'medium'
        assert encoder.faststart is True
        assert encoder.target_size_mb == (4, 8)

    def test_init_custom(self):
        """Test custom initialization."""
        encoder = VideoEncoder(crf=23, preset='fast', faststart=False, target_size_mb=(2, 4))
        assert encoder.crf == 23
        assert encoder.preset == 'fast'
        assert encoder.faststart is False
        assert encoder.target_size_mb == (2, 4)

    @pytest.mark.asyncio
    async def test_encode_video(self, tmp_path):
        """Test video encoding."""
        encoder = VideoEncoder()

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        # Create dummy frames
        from PIL import Image
        for i in range(10):
            frame = frames_dir / f"frame_{i:04d}.png"
            img = Image.new('RGB', (1080, 1920), color='blue')
            img.save(frame)

        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"dummy audio")

        output_path = tmp_path / "output.mp4"

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            result_path = await encoder.encode_video(
                str(frames_dir),
                str(audio_path),
                str(output_path),
                fps=30
            )

            assert result_path == str(output_path)
            mock_subprocess.assert_called_once()
            call_args = ' '.join(mock_subprocess.call_args[0])
            assert 'libx264' in call_args
            assert 'crf' in call_args
            assert 'faststart' in call_args


class TestVideoBuilder:
    """Test VideoBuilder orchestration."""

    def test_init(self):
        """Test initialization."""
        builder = VideoBuilder()
        assert builder.subtitle_renderer is not None
        assert builder.frame_generator is not None
        assert builder.audio_mixer is not None
        assert builder.video_encoder is not None

    @pytest.mark.asyncio
    async def test_build_video_flow(self, sample_product, tmp_path):
        """Test full video build flow (mocked)."""
        with patch.object(VideoBuilder, '_download_images') as mock_download, \
             patch.object(VideoBuilder, '_generate_frames') as mock_frames, \
             patch.object(VideoBuilder, '_mix_audio') as mock_audio, \
             patch.object(VideoBuilder, '_encode_video') as mock_encode, \
             patch.object(VideoBuilder, '_cleanup_frames') as mock_cleanup:

            # Mock image download
            mock_download.return_value = [str(tmp_path / "img1.jpg")]
            (tmp_path / "img1.jpg").write_bytes(b"dummy")

            # Mock frame generation
            frames_dir = tmp_path / "frames"
            frames_dir.mkdir()
            frame_paths = [str(frames_dir / f"frame_{i:04d}.png") for i in range(30)]
            for p in frame_paths:
                Path(p).write_bytes(b"dummy")
            mock_frames.return_value = frame_paths

            # Mock audio mixing
            audio_output = tmp_path / "mixed_audio.mp3"
            audio_output.write_bytes(b"dummy")
            mock_audio.return_value = str(audio_output)

            # Mock video encoding
            video_output = tmp_path / "output.mp4"
            video_output.write_bytes(b"dummy")
            mock_encode.return_value = str(video_output)

            builder = VideoBuilder()
            result = await builder.build_video(
                product=sample_product,
                script="అదృష్టంగా కుర్తీ ₹299 లో YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి",
                audio_path=str(tmp_path / "voice.mp3"),
                output_dir=str(tmp_path)
            )

            assert isinstance(result, VideoResult)
            assert result.video_path == str(video_output)
            assert result.duration > 0
            assert result.size_mb > 0


class TestVideoResult:
    """Test VideoResult dataclass."""

    def test_video_result_creation(self):
        """Test VideoResult creation."""
        result = VideoResult(
            video_path="/path/to/video.mp4",
            duration=28.5,
            size_mb=5.2,
            fps=30,
            resolution=(1080, 1920)
        )

        assert result.video_path == "/path/to/video.mp4"
        assert result.duration == 28.5
        assert result.size_mb == 5.2
        assert result.fps == 30
        assert result.resolution == (1080, 1920)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
import asyncio
from io import BytesIO

import pytest
from PIL import Image

from src.video_builder import AudioMixer, FrameGenerator, VideoBuilder, VideoEncoder


def test_frame_sequence_can_be_consumed_incrementally():
    class Renderer:
        def render_subtitle_frame(self, image, text, progress):
            return image

    generator = FrameGenerator(width=16, height=24)
    frames = generator.iter_frame_sequence([], 0.2, "text", Renderer(), fps=10)

    assert iter(frames) is frames
    assert len(list(frames)) == 2


def test_prepare_images_downloads_and_validates_remote_image(tmp_path, monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")

    class Response:
        content = buffer.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr("src.video_builder.requests.get", lambda *args, **kwargs: Response())
    builder = VideoBuilder()
    paths = asyncio.run(builder._prepare_images(["https://images.example/item"], tmp_path))

    assert len(paths) == 1
    with Image.open(paths[0]) as image:
        assert image.size == (2, 2)


def test_prepare_images_fails_when_all_remote_images_fail(tmp_path, monkeypatch):
    def fail_download(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr("src.video_builder.requests.get", fail_download)

    with pytest.raises(RuntimeError, match="Could not retrieve"):
        asyncio.run(VideoBuilder()._prepare_images(["https://images.example/item"], tmp_path))


def test_video_encoder_accepts_a_frame_iterator(tmp_path, monkeypatch):
    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def create_process(*args, **kwargs):
        return Process()

    monkeypatch.setattr("src.video_builder.asyncio.create_subprocess_exec", create_process)
    monkeypatch.setattr("src.video_builder.os.path.getsize", lambda path: 1024)
    frames = (Image.new("RGB", (4, 4), "blue") for _ in range(3))

    result = asyncio.run(VideoEncoder().encode(frames, "audio.m4a", str(tmp_path / "out.mp4")))

    assert result.duration == 3 / VideoEncoder().fps


def test_audio_mixer_surfaces_ffmpeg_failure(tmp_path, monkeypatch):
    bgm_dir = tmp_path / "bgm"
    bgm_dir.mkdir()
    (bgm_dir / "track.wav").write_bytes(b"music")

    class Process:
        returncode = 1

        async def communicate(self):
            return b"", b"invalid audio"

    async def create_process(*args, **kwargs):
        return Process()

    monkeypatch.setattr("src.video_builder.asyncio.create_subprocess_exec", create_process)
    mixer = AudioMixer(str(bgm_dir))

    with pytest.raises(RuntimeError, match="invalid audio"):
        asyncio.run(mixer.mix_audio("voice.mp3", str(tmp_path / "mixed.m4a"), 10))

"""
Tests for script generation and TTS module.
"""
import asyncio
from types import SimpleNamespace

import base64
import asyncio
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.script_tts import ScriptGenerator, TTSEngine, ScriptResult
from src.database import Product
from datetime import datetime
from google.genai.errors import APIError
from google.genai.errors import APIError


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


class TestScriptGenerator:
    """Test script generation."""

    def test_clean_script_removes_markdown(self):
        """Test script cleaning removes markdown."""
        generator = ScriptGenerator()

        script = "**అదృష్టంగా** _కుర్తీ_ `కോടీ` #ఫ్యాషన్"
        cleaned = generator._clean_script(script)

        assert "*" not in cleaned
        assert "_" not in cleaned
        assert "`" not in cleaned
        assert "#" not in cleaned

    def test_clean_script_removes_english(self):
        """Test script cleaning removes English words."""
        generator = ScriptGenerator()

        script = "అదృష్టంగా Cotton Kurti ₹299"
        cleaned = generator._clean_script(script)

        assert "Cotton" not in cleaned
        assert "Kurti" not in cleaned
        # Numbers and ₹ should remain
        assert "299" in cleaned or "₹" in cleaned

    def test_clean_script_normalizes_whitespace(self):
        """Test script cleaning normalizes whitespace."""
        generator = ScriptGenerator()

        script = "అదృష్టంగా   కుర్తీ    ₹299"
        cleaned = generator._clean_script(script)

        assert "  " not in cleaned  # No double spaces
        assert cleaned == "అదృష్టంగా కుర్తీ ₹299"

    def test_count_telugu_words(self):
        """Test Telugu word counting."""
        generator = ScriptGenerator()

        # Telugu words separated by spaces
        script = "అదృష్టంగా కుర్తీ ₹299 నేరచుకు"
        count = generator._count_telugu_words(script)
        assert count == 4

    def test_estimate_duration(self):
        """Test duration estimation."""
        generator = ScriptGenerator()

        # ~3 words per second
        duration = generator._estimate_duration(60)
        assert duration == 20.0

        duration = generator._estimate_duration(75)
        assert duration == 25.0

    def test_fallback_script_contains_required_elements(self, sample_product):
        """Test fallback script has all required elements."""
        generator = ScriptGenerator()
        script = generator._fallback_script(sample_product)

        # Should contain product details
        assert "కుర్తీ" in script or "కొట్టൺ" in script.lower()
        assert "₹299" in script or "299" in script
        assert "50%" in script or "50" in script
        assert "Red" in script or "ఎరుపు" in script
        assert "KUR-TEST12" in script

        # Should contain dual CTA
        assert "YouTube description లో లింక్ ఉంది" in script
        assert "Instagram మీద Comment LINK" in script
        assert "DM చేయండి" in script
        cleaned_script = generator._clean_script(script)
        assert 65 <= generator._count_telugu_words(cleaned_script) <= 80

    @pytest.mark.asyncio
    async def test_generate_retries_temporary_gemini_error(self, sample_product, monkeypatch):
        generator = ScriptGenerator.__new__(ScriptGenerator)
        generate_content = MagicMock(
            side_effect=[
                APIError(
                    503,
                    {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}},
                ),
                SimpleNamespace(text="తగిన తెలుగు స్క్రిప్ట్"),
            ]
        )
        generator.client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )
        generator.model = "gemini-3.8-flash"
        sleep = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        script = await generator._generate_with_gemini(sample_product)

        assert script == "తగిన తెలుగు స్క్రిప్ట్"
        assert generate_content.call_count == 2
        sleep.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_generate_retries_temporary_gemini_error(self, sample_product, monkeypatch):
        generator = ScriptGenerator.__new__(ScriptGenerator)
        generate_content = MagicMock(
            side_effect=[
                APIError(
                    503,
                    {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}},
                ),
                SimpleNamespace(text="తగిన తెలుగు స్క్రిప్ట్"),
            ]
        )
        generator.client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )
        generator.model = "gemini-3.8-flash"
        sleep = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        script = await generator._generate_with_gemini(sample_product)

        assert script == "తగిన తెలుగు స్క్రిప్ట్"
        assert generate_content.call_count == 2
        sleep.assert_awaited_once_with(2)

    def test_build_prompt_includes_all_product_details(self, sample_product):
        """Test prompt building includes all product details."""
        generator = ScriptGenerator()
        prompt = generator._build_prompt(sample_product)

        assert "Cotton Kurti" in prompt
        assert "kurtis" in prompt
        assert "₹299" in prompt
        assert "₹599" in prompt
        assert "50%" in prompt
        assert "Cotton" in prompt
        assert "Red" in prompt
        assert "KUR-TEST12" in prompt
        assert "4.5" in prompt
        assert "100" in prompt


class TestTTSEngine:
    """Test TTS engine."""

    def test_default_voice(self):
        """Test default voice is Gemini's female voice."""
        engine = TTSEngine()
        assert engine.voice == "Kore"

    def test_custom_voice(self):
        """Map the existing male voice setting to Gemini's male voice."""
        engine = TTSEngine(voice="te-IN-MohanNeural")
        assert engine.voice == "Puck"

    def test_list_voices(self):
        """Test listing available voices."""
        voices = TTSEngine.list_voices()
        assert "female" in voices
        assert "male" in voices
        assert voices["female"] == "Kore"
        assert voices["male"] == "Puck"

    @pytest.mark.asyncio
    async def test_synthesize_creates_file(self, tmp_path):
        """Test TTS synthesis creates audio file."""
        audio_bytes = b"RIFF test wav data"
        client = MagicMock()
        client.interactions.create.return_value = SimpleNamespace(
            output_audio=SimpleNamespace(
                data=base64.b64encode(audio_bytes).decode("ascii")
            )
        )
        output_path = tmp_path / "test_output.wav"

        with patch("src.script_tts.genai.Client", return_value=client) as create_client:
            await TTSEngine().synthesize("టెస్ట్ స్క్రిప్ట్", str(output_path))

        create_client.assert_called_once()
        assert output_path.read_bytes() == audio_bytes
        assert client.interactions.create.call_args.kwargs["model"] == "gemini-3.8-flash-lite-tts"


class TestScriptResult:
    """Test ScriptResult dataclass."""

    def test_script_result_creation(self):
        """Test ScriptResult can be created with all fields."""
        result = ScriptResult(
            telugu_script="అదృష్టంగా కుర్తీ",
            english_translation="Lucky kurti",
            word_count=3,
            estimated_duration=1.0,
            audio_path="/path/to/audio.mp3"
        )

        assert result.telugu_script == "అదృష్టంగా కుర్తీ"
        assert result.word_count == 3
        assert result.estimated_duration == 1.0


class TestIntegration:
    """Integration tests (mocked)."""

    @pytest.mark.asyncio
    async def test_generate_script_and_tts_flow(self, sample_product, tmp_path):
        """Test full script generation and TTS flow."""
        with patch('src.script_tts.ScriptGenerator._generate_with_gemini') as mock_gemini, \
             patch('src.script_tts.ScriptGenerator._translate_to_english') as mock_translate, \
             patch('src.script_tts.genai.Client') as mock_client_factory:

            # Mock Gemini response
            mock_gemini.return_value = "అదృష్టంగా కుర్తీ ₹299 లో YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"

            # Mock translation
            mock_translate.return_value = "Lucky kurti at ₹299. Link in YouTube description / Comment LINK on Instagram for DM."

            # Mock Gemini audio output
            mock_client = MagicMock()
            mock_client.interactions.create.return_value = SimpleNamespace(
                output_audio=SimpleNamespace(
                    data=base64.b64encode(b"RIFF test wav data").decode("ascii")
                )
            )
            mock_client_factory.return_value = mock_client

            from src.script_tts import generate_script_and_tts

            result = await generate_script_and_tts(sample_product, str(tmp_path))

            assert isinstance(result, ScriptResult)
            assert result.word_count >= 65  # Should meet minimum
            assert result.word_count <= 80  # Should not exceed maximum
            assert result.audio_path.endswith("_voice.wav")
            assert "YouTube description లో లింక్ ఉంది" in result.telugu_script
            assert "Instagram మీద Comment LINK" in result.telugu_script


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
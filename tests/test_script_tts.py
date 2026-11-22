"""
Tests for script generation and TTS module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.script_tts import ScriptGenerator, TTSEngine, ScriptResult
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
        assert "Red" in script or "రెడ్" in script
        assert "KUR-TEST12" in script

        # Should contain dual CTA
        assert "YouTube description లో లింక్ ఉంది" in script
        assert "Instagram మీద Comment LINK" in script
        assert "DM చేయండి" in script

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
        """Test default voice is Telugu female."""
        engine = TTSEngine()
        assert engine.voice == "te-IN-ShrutiNeural"

    def test_custom_voice(self):
        """Test custom voice selection."""
        engine = TTSEngine(voice="te-IN-MohanNeural")
        assert engine.voice == "te-IN-MohanNeural"

    def test_list_voices(self):
        """Test listing available voices."""
        voices = TTSEngine.list_voices()
        assert "female" in voices
        assert "male" in voices
        assert voices["female"] == "te-IN-ShrutiNeural"
        assert voices["male"] == "te-IN-MohanNeural"

    @pytest.mark.asyncio
    async def test_synthesize_creates_file(self, sample_product, tmp_path):
        """Test TTS synthesis creates audio file."""
        # This test requires edge-tts to be installed
        # We'll mock it to avoid network calls
        engine = TTSEngine()

        output_path = tmp_path / "test_output.mp3"

        with patch('edge_tts.Communicate') as mock_communicate:
            mock_instance = AsyncMock()
            mock_instance.save = AsyncMock()
            mock_communicate.return_value = mock_instance

            await engine.synthesize("టెస్ట్ స్క్రిప్ట్", str(output_path))

            mock_communicate.assert_called_once()
            mock_instance.save.assert_called_once_with(str(output_path))


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
             patch('edge_tts.Communicate') as mock_communicate:

            # Mock Gemini response
            mock_gemini.return_value = "అదృష్టంగా కుర్తీ ₹299 లో YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"

            # Mock translation
            mock_translate.return_value = "Lucky kurti at ₹299. Link in YouTube description / Comment LINK on Instagram for DM."

            # Mock TTS
            mock_instance = AsyncMock()
            mock_instance.save = AsyncMock()
            mock_communicate.return_value = mock_instance

            from src.script_tts import generate_script_and_tts

            result = await generate_script_and_tts(sample_product, str(tmp_path))

            assert isinstance(result, ScriptResult)
            assert result.word_count >= 65  # Should meet minimum
            assert result.word_count <= 80  # Should not exceed maximum
            assert result.audio_path.endswith("_voice.mp3")
            assert "YouTube description లో లింక్ ఉంది" in result.telugu_script
            assert "Instagram మీద Comment LINK" in result.telugu_script


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
"""Generate Telugu scripts and audio using Google's Gemini API."""
import base64
import asyncio
import os
import re
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

from src.config import settings
from src.database import Product
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ScriptResult:
    """Result of script generation and TTS."""
    telugu_script: str
    english_translation: str
    word_count: int
    estimated_duration: float  # seconds
    audio_path: str


# =============================================================================
# Script Generator (Gemini)
# =============================================================================

class ScriptGenerator:
    """Generates Telugu fashion video scripts using Google Gemini."""

    # System prompt for Telugu fashion scripts
    SYSTEM_PROMPT = """You are a Telugu fashion content creator writing scripts for 25-28 second YouTube Shorts/Instagram Reels.

    CRITICAL RULES:
    1. Write ONLY in Telugu (Telugu script, not transliteration)
    2. Target 65-80 Telugu words (≈25-28 seconds at normal speech rate)
    3. Structure: HOOK (3-4s) → FABRIC/PRICE DETAILS (15-18s) → DUAL CTA (4-5s)
    4. NO English words, NO markdown, NO bullet points, NO emojis in the Telugu script
    5. Natural spoken Telugu, conversational tone
    6. Include: fabric, price (₹), discount %, product code, color
    7. Dual CTA MUST be: "YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"

    Example style:
    "అదృష్టంగా సтелర్ చేతులలో కాటిన ಹా క esfuer్టీ్స్టర్ విలసి 광역 DelhiBombay HyderabadChennai BangaloreKolkataPuneAhmedabadJaipurLucknowKanpurNagpurIndoreThaneBhopalVisakhapatnamPimpri-ChinchwadPatnaVadodaraGhaziabadLudhianaAgraNashikFaridabadMeerutRajkotKalyan-DombivliVasai-VirarVaranasiSrinagarAurangabadDhanbadAmritsarNavi MumbaiAllahabadRanchiHowrahCoimbatoreJabalpurGwaliorVijayawadaJodhpurMaduraiRaipurKotaGuwahatiChandigarhSolapurHubli-DharwadBareillyMoradabadMysoreTiruppurGurgaonAligarhJalandharBhubaneswarSalemWarangalGunturBhiwandiSaharanpurGorakhpurBikanerAmravatiNoidaJamshedpurBhilaiCuttackFirozabadKochiNelloreBhavnagarDehradunDurgapurAsansolRourkelaNandedKolhapurAjmerAkolaGulbargaJamnagarUjjainLoniSiliguriJhansiUlhasnagarJammuSangli-Miraj & KupwadMangaloreErodeBelgaumAmbatturTirunelveliMalegaonGayaJalgaonUdaipurMaheshtala"""

    def __init__(self):
        self.client = None
        self.model = settings.gemini_model

    def _get_client(self):
        if self.client is None:
            self.client = genai.Client(api_key=settings.gemini_api_key)
        return self.client

    def _build_prompt(self, product: Product) -> str:
        """Build the prompt for Gemini."""
        return f"""Write a Telugu script for a 25-28 second fashion video about this product:

Product: {product.title}
Category: {product.category}
Price: ₹{product.price} (MRP ₹{product.original_price}, {product.discount_percent}% OFF)
Fabric: {product.fabric}
Color: {product.color}
Product Code: {product.product_code}
Rating: {product.rating}/5 ({product.review_count} reviews)

Requirements:
- 65-80 Telugu words only
- NO English words
- NO markdown/bullets
- Structure: Hook → Fabric/Price details → Dual CTA
- CTA: "YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"
- Natural spoken Telugu"""

    def _clean_script(self, script: str) -> str:
        """Clean the generated script."""
        # Remove markdown formatting
        script = re.sub(r'[\*_`#]', '', script)

        # Preserve the required CTA pattern before removing English words
        cta_pattern = "YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"

        # Check if CTA is present in script (case insensitive)
        cta_present = "YouTube description" in script and "Instagram మీద Comment LINK" in script and "DM చేయండి" in script
        if cta_present:
            script = script.replace(cta_pattern, " ")

        # Remove English words (keep Telugu, numbers, punctuation, ₹)
        script = re.sub(r'[a-zA-Z]+', '', script)

        # If CTA was present, append it back (cleaned of markdown but keeping English CTA words)
        if cta_present:
            # Clean CTA of markdown only
            clean_cta = re.sub(r'[\*_`#]', '', cta_pattern)
            script = script.strip() + " " + clean_cta

        # Normalize whitespace
        script = re.sub(r'\s+', ' ', script).strip()
        return script

    def _count_telugu_words(self, text: str) -> int:
        """Count Telugu words (separated by spaces)."""
        # Telugu words are separated by spaces
        words = [w for w in text.split() if w.strip()]
        return len(words)

    def _estimate_duration(self, word_count: int) -> float:
        """Estimate speech duration in seconds."""
        # Average Telugu speech rate: ~3 words/second
        return word_count / 3.0

    def _fallback_script(self, product: Product) -> str:
        """Generate fallback script if Gemini fails."""
        category_telugu = {
            "kurtis": "కుర్తీ",
            "sarees": "సారి",
            "ethnic-sets": "ఎత్నిక్ సెట్",
            "western-wear": "వెస్టర్న్ వేర్",
            "handbags": "హాండ్బాగ్"
        }.get(product.category, "ఉత్పత్తి")

        # Convert fabric to Telugu if possible
        fabric_telugu = {
            "Cotton": "కొట్టన్",
            "Silk": "పట్టు",
            "Chiffon": "షిఫాన్",
            "Georgette": "జార్జెట్",
            "Crepe": "క్రేప్",
            "Linen": "లినెన్",
            "Rayon": "రేయాన్",
            "Viscose": "విస్కోస్",
            "Polyester": "పాలీఎస్టర్",
            "Blend": "బ్లెండ్",
            "Khaddar": "ఖదర్",
            "Handloom": "హ్యాండ్లోమ్",
            "Organza": "ఆర్ంజా",
            "Satin": "సాటిన్",
            "Velvet": "వెల్వెట్",
            "Net": "నెట్",
            "Lace": "లేస్",
            "Embroidered": "ఎంబ్రాయిడర आँకెడ్"
        }.get(product.fabric, product.fabric)

        color_telugu = {
            "Red": "ఎరుపు",
            "Blue": "నీలి",
            "Green": "ఆకుపచ్చ",
            "Yellow": "పసుపు",
            "Pink": "గులాబీ",
            "Black": "నల్ల",
            "White": "తెలుపు",
            "Orange": "కమల",
            "Purple": "మ*;",
            "Brown": "కాఫీ",
            "Grey": "గ్రే",
            "Gray": "గ్రే",
            "Maroon": "మారూన్",
            "Navy": "నెయివీ",
            "Teal": "టీల్",
            "Olive": "ఆలివ్",
            "Beige": "బీజ్",
            "Cream": "క్రీమ్",
            "Gold": "బంగారం",
            "Silver": "వెండి",
            "Peach": "పీచ్",
            "Magenta": "మజెంటా",
            "Turquoise": "టూర్కోయిస్",
            "Indigo": "ఇన్డిగో",
            "Violet": "వయలెట్",
            "Coral": "కోరల్",
            "Mustard": "ఆవాలు",
            "Multicolor": "బహురంగు"
        }.get(product.color, product.color)

        return (
            f"ఈ రోజు మీ కోసం {fabric_telugu}తో చేసిన అందమైన {category_telugu}. "
            f"{color_telugu} రంగు, చక్కని పనితనం ఈ దుస్తుకు ప్రత్యేక ఆకర్షణ ఇస్తాయి. "
            f"తేలికైన వస్త్రం రోజువారీ వాడకానికి, వేడుకలకు అనుకూలంగా ఉంటుంది. "
            f"సౌకర్యవంతమైన రూపంతో హాయిగా ధరించవచ్చు. ఇతర దుస్తులతో సులభంగా "
            f"జత చేసుకుని కొత్తగా కనిపించండి. ఇప్పుడు ₹{product.price} మాత్రమే, "
            f"అసలు ధర ₹{product.original_price}. {product.discount_percent}% "
            f"తగ్గింపుతో ఈ ఎంపికను ఇప్పుడే చూడండి. రేటింగ్ {product.rating} "
            f"నక్షత్రాలు, {product.review_count} మంది కొనుగోలుదారుల అభిప్రాయాలు. "
            f"ఉత్పత్తి కోడ్ {product.product_code}. ధర, లభ్యత కొనుగోలు ముందు పరిశీలించండి. "
            f"YouTube description లో లింక్ ఉంది / Instagram మీద Comment LINK అంచుకోవడానికి DM చేయండి"
        )
    async def generate_script(self, product: Product) -> ScriptResult:
        """Generate Telugu script and convert to speech."""
        # Generate script using Gemini
        telugu_script = await self._generate_with_gemini(product)

        # Clean and validate
        telugu_script = self._clean_script(telugu_script)
        word_count = self._count_telugu_words(telugu_script)

        # Ensure word count is in range
        if word_count < settings.script_target_words_min:
            logger.warning(f"Script too short ({word_count} words), using fallback")
            telugu_script = self._fallback_script(product)
            telugu_script = self._clean_script(telugu_script)
            word_count = self._count_telugu_words(telugu_script)
        elif word_count > settings.script_target_words_max:
            # Truncate to max words
            words = telugu_script.split()
            telugu_script = " ".join(words[:settings.script_target_words_max])
            word_count = settings.script_target_words_max

        estimated_duration = self._estimate_duration(word_count)

        # Generate English translation for reference
        english_translation = await self._translate_to_english(telugu_script)

        logger.info(f"Generated script: {word_count} words, ~{estimated_duration:.1f}s")
        logger.debug(f"Telugu: {telugu_script}")

        return ScriptResult(
            telugu_script=telugu_script,
            english_translation=english_translation,
            word_count=word_count,
            estimated_duration=estimated_duration,
            audio_path=""  # Will be filled by TTS engine
        )

    async def _generate_with_gemini(self, product: Product) -> str:
        """Generate script using Gemini API."""
        try:
            for attempt in range(3):
                try:
                    response = await asyncio.to_thread(
                        self._get_client().models.generate_content,
                        model=self.model,
                        contents=[
                            types.Content(role="user", parts=[types.Part(text=self._build_prompt(product))])
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=self.SYSTEM_PROMPT,
                            temperature=settings.script_temperature,
                            max_output_tokens=settings.script_max_tokens,
                        )
                    )
                    return response.text.strip()
                except errors.APIError as error:
                    if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                        raise
                    delay = 2 ** (attempt + 1)
                    logger.warning(
                        f"Gemini temporarily unavailable (HTTP {error.code}); "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            return self._fallback_script(product)

    async def _translate_to_english(self, telugu_script: str) -> str:
        """Translate Telugu script to English for reference."""
        try:
            response = await asyncio.to_thread(
                self._get_client().models.generate_content,
                model=self.model,
                contents=[
                    types.Content(role="user", parts=[types.Part(text=f"Translate to English (brief): {telugu_script}")])
                ],
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=200,
                )
            )
            return response.text.strip()
        except Exception:
            return "Translation unavailable"


# =============================================================================
# TTS Engine (Gemini)
# =============================================================================

class TTSEngine:
    """Text-to-Speech using Gemini's Telugu-capable audio generation model."""

    VOICES = {
        "female": "Kore",
        "male": "Puck",
    }
    LEGACY_VOICES = {
        "te-IN-ShrutiNeural": "Kore",
        "te-IN-MohanNeural": "Puck",
    }

    def __init__(self, voice: str = "Kore"):
        self.voice = self.LEGACY_VOICES.get(voice, voice)
        self.client = None

    async def synthesize(self, text: str, output_path: str) -> str:
        """Synthesize Telugu text to speech."""
        if self.client is None:
            self.client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options={"api_version": "v1beta"},
            )

        for attempt in range(3):
            try:
                interaction = await asyncio.to_thread(
                    self.client.interactions.create,
                    model=settings.gemini_tts_model,
                    input=[{
                        "type": "user_input",
                        "content": [{
                            "type": "text",
                            "text": text,
                            "annotations": [{
                                "type": "speech_metadata",
                                "style": "Warm, clear, conversational Telugu narration.",
                            }],
                        }],
                    }],
                    response_format={"type": "audio"},
                    generation_config={"speech_config": [{"voice": self.voice}]},
                )
                audio_data = interaction.output_audio.data if interaction.output_audio else None
                if not audio_data:
                    raise RuntimeError("Gemini TTS returned no audio data.")
                audio_bytes = base64.b64decode(audio_data, validate=True)
                break
            except errors.APIError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                delay = 2 ** (attempt + 1)
                logger.warning(
                    f"Gemini TTS temporarily unavailable (HTTP {error.code}); "
                    f"retrying in {delay}s"
                )
                await asyncio.sleep(delay)

        output = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "wb") as audio_file:
            audio_file.write(audio_bytes)
        logger.info(f"TTS saved to {output_path}")
        return output_path

    async def synthesize_with_subtitles(
        self,
        text: str,
        output_path: str
    ) -> tuple[str, list]:
        """
        Synthesize and return word-level timestamps for subtitle sync.
        Returns (audio_path, word_timestamps)
        """
        # Word timestamps would require streaming support from the TTS API.
        # We'll estimate timestamps based on word count and duration.
        await self.synthesize(text, output_path)

        # Estimate word timestamps
        words = text.split()
        # We'll calculate in video_builder based on actual audio duration
        return output_path, []

    @classmethod
    def list_voices(cls) -> dict:
        """List available Telugu voices."""
        return cls.VOICES.copy()


# =============================================================================
# Convenience Functions
# =============================================================================

async def generate_script_and_tts(
    product: Product,
    output_dir: str
) -> ScriptResult:
    """Generate script and TTS audio for a product."""
    script_gen = ScriptGenerator()
    tts_engine = TTSEngine(voice=settings.tts_voice)

    # Generate script
    script_result = await script_gen.generate_script(product)

    # Generate audio
    audio_filename = f"{product.meesho_product_id}_voice.wav"
    audio_path = os.path.join(output_dir, audio_filename)

    await tts_engine.synthesize(script_result.telugu_script, audio_path)

    script_result.audio_path = audio_path
    return script_result
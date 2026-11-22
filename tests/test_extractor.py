"""
Tests for Meesho extractor module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.extractor import MeeshoExtractor, ExtractedProduct
from src.config import settings


class TestMeeshoExtractor:
    """Test Meesho extractor functionality."""

    @pytest.fixture
    def extractor(self):
        """Create extractor instance."""
        return MeeshoExtractor(headless=True)

    def test_innerwear_exclusion_keywords(self):
        """Test innerwear exclusion keyword list."""
        keywords = settings.innerwear_exclude_keywords

        # Check all 13 keywords are present
        expected = [
            "bra", "panty", "briefs", "boxer", "lingerie", "nightwear",
            "sleepwear", "innerwear", "underwear", "bikini", "thong",
            "g-string", "shapewear"
        ]

        for kw in expected:
            assert kw in keywords, f"Missing keyword: {kw}"

        assert len(keywords) == 13

    def test_is_innerwear_text_english(self, extractor):
        """Test innerwear detection with English keywords."""
        test_cases = [
            ("Cotton Bra Set", True),
            ("Lace Panty Pack", True),
            ("Men's Briefs", True),
            ("Boxer Shorts", True),
            ("Lingerie Set", True),
            ("Nightwear Dress", True),
            ("Sleepwear Pajamas", True),
            ("Innerwear Combo", True),
            ("Cotton Underwear", True),
            ("Bikini Bottom", True),
            ("Thong Pack", True),
            ("G-String Set", True),
            ("Shapewear Bodysuit", True),
            ("Cotton Kurti", False),
            ("Silk Saree", False),
            ("Ethnic Set", False),
        ]

        for text, expected in test_cases:
            result = extractor._is_innerwear_text(text)
            assert result == expected, f"Failed for '{text}': expected {expected}, got {result}"

    def test_is_innerwear_text_case_insensitive(self, extractor):
        """Test innerwear detection is case insensitive."""
        assert extractor._is_innerwear_text("BRA") is True
        assert extractor._is_innerwear_text("Bra") is True
        assert extractor._is_innerwear_text("bra") is True
        assert extractor._is_innerwear_text("BrA") is True

    def test_extract_product_id_from_urls(self, extractor):
        """Test product ID extraction from various URL formats."""
        test_cases = [
            ("https://meesho.com/product/ABC123", "ABC123"),
            ("https://meesho.com/p/XYZ789", "XYZ789"),
            ("https://meesho.com/item?pid=PROD456", "PROD456"),
            ("https://meesho.com/product?productId=ITEM789", "ITEM789"),
        ]

        for url, expected_id in test_cases:
            result = extractor._extract_product_id(url)
            assert result == expected_id, f"Failed for '{url}': expected {expected_id}, got {result}"

    def test_generate_product_code(self, extractor):
        """Test product code generation."""
        code = extractor._generate_product_code("abc123", "kurtis")
        assert code == "KUR-ABC123"

        code = extractor._generate_product_code("xyz789", "sarees")
        assert code == "SAR-XYZ789"

    def test_extract_fabric(self, extractor):
        """Test fabric extraction from title."""
        test_cases = [
            ("Pure Cotton Kurti", "Cotton"),
            ("Silk Blend Saree", "Silk"),
            ("Chiffon Dupatta", "Chiffon"),
            ("Georgette Kurta", "Georgette"),
            ("Crepe Top", "Crepe"),
            ("Linen Shirt", "Linen"),
            ("Rayon Dress", "Rayon"),
            ("Viscose Kurti", "Viscose"),
            ("Polyester Blend", "Polyester"),
            ("Khaddar Saree", "Khaddar"),
            ("Handloom Cotton", "Handloom"),
            ("Organza Dupatta", "Organza"),
            ("Satin Nightwear", "Satin"),  # This would be excluded as innerwear
            ("Velvet Blazer", "Velvet"),
            ("Net Saree", "Net"),
            ("Lace Top", "Lace"),
            ("Embroidered Kurti", "Embroidered"),
            ("Unknown Fabric Item", "Cotton"),  # Default
        ]

        for title, expected_fabric in test_cases:
            result = extractor._extract_fabric(title)
            assert result == expected_fabric, f"Failed for '{title}': expected {expected_fabric}, got {result}"

    def test_extract_color(self, extractor):
        """Test color extraction from title."""
        test_cases = [
            ("Red Kurti", "Red"),
            ("Blue Saree", "Blue"),
            ("Green Top", "Green"),
            ("Yellow Dress", "Yellow"),
            ("Pink Dupatta", "Pink"),
            ("Black Shirt", "Black"),
            ("White Kurta", "White"),
            ("Orange Top", "Orange"),
            ("Purple Saree", "Purple"),
            ("Brown Bag", "Brown"),
            ("Grey Jacket", "Grey"),
            ("Maroon Kurti", "Maroon"),
            ("Navy Blue Dress", "Navy"),
            ("Teal Top", "Teal"),
            ("Olive Green", "Olive"),
            ("Beige Saree", "Beige"),
            ("Cream Kurti", "Cream"),
            ("Gold Border", "Gold"),
            ("Silver Embroidery", "Silver"),
            ("Peach Dress", "Peach"),
            ("Multicolor Print", "Multicolor"),
            ("No Color Here", "Multicolor"),  # Default
        ]

        for title, expected_color in test_cases:
            result = extractor._extract_color(title)
            assert result == expected_color, f"Failed for '{title}': expected {expected_color}, got {result}"

    def test_get_default_sizes(self, extractor):
        """Test default size options per category."""
        assert extractor._get_default_sizes("kurtis") == ["S", "M", "L", "XL", "XXL"]
        assert extractor._get_default_sizes("ethnic-sets") == ["S", "M", "L", "XL", "XXL"]
        assert extractor._get_default_sizes("western-wear") == ["S", "M", "L", "XL", "XXL"]
        assert extractor._get_default_sizes("sarees") == ["Free Size"]
        assert extractor._get_default_sizes("handbags") == ["One Size"]
        assert extractor._get_default_sizes("unknown") == ["S", "M", "L", "XL"]


class TestCategoryURLs:
    """Test category URL mappings."""

    def test_category_urls_exist(self):
        """Test all expected categories have URLs."""
        expected_categories = ["kurtis", "sarees", "ethnic-sets", "western-wear", "handbags"]

        for cat in expected_categories:
            assert cat in MeeshoExtractor.CATEGORY_URLS
            assert MeeshoExtractor.CATEGORY_URLS[cat].startswith("/")


class TestSelectors:
    """Test CSS selectors."""

    def test_selectors_defined(self):
        """Test all required selectors are defined."""
        required_selectors = [
            "product_card", "product_title", "product_price",
            "product_original_price", "product_discount", "product_rating",
            "product_image", "product_link", "next_page", "out_of_stock"
        ]

        for selector in required_selectors:
            assert selector in MeeshoExtractor.SELECTORS
            assert MeeshoExtractor.SELECTORS[selector]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
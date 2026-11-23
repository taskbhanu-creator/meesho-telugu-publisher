"""
Meesho product extractor using Playwright.
Scrapes trending ladies' fashion collections with strict innerwear exclusion.
"""
import asyncio
import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    ElementHandle,
    TimeoutError as PlaywrightTimeoutError,
)
from src.config import settings
from src.database import Product, save_product
import structlog

logger = structlog.get_logger(__name__)


class ProductExtractionError(RuntimeError):
    """Raised when the product catalog cannot be extracted."""


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ExtractedProduct:
    """Raw extracted product data before validation."""
    meesho_product_id: str
    title: str
    category: str
    price: int
    original_price: int
    discount_percent: int
    rating: float
    review_count: int
    image_urls: List[str]
    product_url: str
    product_code: str
    fabric: str
    color: str
    size_options: List[str]
    in_stock: bool


# =============================================================================
# Meesho Extractor
# =============================================================================

class MeeshoExtractor:
    """Playwright-based Meesho product extractor."""

    # Category URL mappings
    CATEGORY_URLS = {
        "kurtis": "/women-kurtis/pl/3j0",
        "sarees": "/sarees/pl/3iy",
        "ethnic-sets": "/women-ethnic-wear/pl/1hjl",
        "western-wear": "/western-wear-women/pl/4aus",
        "handbags": "/women-handbags/pl/1oof",
    }

    # Selectors (may need updates if Meesho changes DOM)
    SELECTORS = {
        "product_card": "a[href*='/p/']",
        "product_title": "img[alt], h3, h2",
        "product_price": "[data-testid='product-price'], .ProductCard__price, .product-price",
        "product_original_price": "[data-testid='original-price'], .ProductCard__original-price, .original-price",
        "product_discount": "[data-testid='discount'], .ProductCard__discount, .discount",
        "product_rating": "[data-testid='rating'], .ProductCard__rating, .rating",
        "product_image": "img",
        "product_link": "a[href*='/p/']",
        "next_page": "[data-testid='next-page'], .pagination-next, a[aria-label='Next']",
        "out_of_stock": "[data-testid='out-of-stock'], .out-of-stock, .sold-out",
    }

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.products_extracted = 0
        self.errors: List[str] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Start Playwright browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()
        # Block unnecessary resources for speed
        await self.page.route("**/*.{png,jpg,jpeg,gif,webp,css,woff,woff2}", lambda route: route.abort())
        logger.info("Playwright browser started")

    async def close(self):
        """Close browser and playwright."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright'):
            await self.playwright.stop()
        logger.info("Playwright browser closed")

    async def scrape_category(
        self,
        category: str,
        max_products: Optional[int] = None
    ) -> List[Product]:
        """Scrape products from a category."""
        max_products = max_products or settings.meesho_max_products_per_category
        category_url = self.CATEGORY_URLS.get(category)

        if not category_url:
            logger.warning(f"Unknown category: {category}")
            return []

        full_url = urljoin(settings.meesho_base_url, category_url)
        logger.info(f"Scraping category: {category} from {full_url}")

        products = []
        page_num = 1

        try:
            response = await self.page.goto(
                full_url,
                wait_until="domcontentloaded",
                timeout=settings.meesho_request_timeout_ms
            )
            if response and response.status in {404, 410}:
                discovered_url = await self._discover_category_url(category)
                if discovered_url:
                    full_url = discovered_url
                    response = await self.page.goto(
                        full_url,
                        wait_until="domcontentloaded",
                        timeout=settings.meesho_request_timeout_ms
                    )
            if response and response.status >= 400:
                raise RuntimeError(f"Meesho returned HTTP {response.status} for {full_url}")
            await self._wait_for_products()

            while len(products) < max_products:
                logger.info(f"Scraping page {page_num} for {category}")

                page_products = await self._extract_products_from_page(category, max_products)
                products.extend(page_products)

                if len(products) >= max_products:
                    products = products[:max_products]
                    break

                # Try to go to next page
                if not await self._go_to_next_page():
                    break

                page_num += 1
                await asyncio.sleep(settings.meesho_scrape_delay_ms / 1000)

        except Exception as e:
            logger.error(f"Error scraping category {category}: {e}")
            self.errors.append(f"{category}: {str(e)}")

        # Save to database
        saved_count = 0
        for product in products:
            try:
                is_new = save_product(product)
                if is_new:
                    saved_count += 1
            except Exception as e:
                logger.error(f"Error saving product {product.meesho_product_id}: {e}")

        logger.info(f"Category {category}: extracted {len(products)}, saved {saved_count} new products")
        self.products_extracted += len(products)
        return products

    async def _discover_category_url(self, category: str) -> Optional[str]:
        """Resolve a category from Meesho's live navigation when a stored URL is stale."""
        link_labels = {
            "kurtis": {"all kurtis"},
            "sarees": {"all sarees"},
            "ethnic-sets": {"women ethnic wear", "kurta pant sets"},
            "western-wear": {"all topwear"},
            "handbags": {"handbags"},
        }
        try:
            await self.page.goto(
                settings.meesho_base_url,
                wait_until="domcontentloaded",
                timeout=settings.meesho_request_timeout_ms,
            )
            links = await self.page.locator("a[href*='/pl/']").evaluate_all(
                "(anchors) => anchors.map(a => ({href: a.getAttribute('href'), label: a.innerText.trim().toLowerCase()}))"
            )
            for link in links:
                if link["label"] in link_labels.get(category, set()) and link["href"]:
                    return urljoin(settings.meesho_base_url, link["href"])
        except Exception as error:
            logger.warning(f"Could not discover current URL for category {category}: {error}")
        return None

    async def scrape_all_categories(self, max_products: Optional[int] = None) -> Dict[str, List[Product]]:
        """Scrape all configured categories."""
        results = {}
        for category in settings.meesho_categories:
            results[category] = await self.scrape_category(category, max_products=max_products)
        if not any(results.values()):
            details = "; ".join(self.errors) or "No valid products matched the current page selectors."
            raise ProductExtractionError(
                f"No products were extracted from any Meesho category. {details}"
            )
        return results

    async def _wait_for_products(self):
        """Wait for product cards to load."""
        try:
            await self.page.wait_for_selector(
                self.SELECTORS["product_card"],
                timeout=10000
            )
        except PlaywrightTimeoutError:
            await self.page.wait_for_load_state("networkidle")
            cards = await self.page.query_selector_all(self.SELECTORS["product_card"])
            if not cards:
                title = await self.page.title()
                raise RuntimeError(
                    "No product cards appeared after the page finished loading "
                    f"(url={self.page.url!r}, title={title!r}); the page may be "
                    "blocked or the product-card selectors may be outdated."
                )

    async def _extract_products_from_page(self, category: str, max_products: Optional[int] = None) -> List[Product]:
        """Extract product data from current page."""
        products = []

        try:
            cards = await self.page.query_selector_all(self.SELECTORS["product_card"])
            logger.debug(f"Found {len(cards)} product cards")

            for card in cards:
                if len(products) >= (max_products or settings.meesho_max_products_per_category):
                    break

                try:
                    product = await self._extract_product_from_card(card, category)
                    if product and not self._is_innerwear(product):
                        products.append(product)
                except Exception as e:
                    logger.debug(f"Error extracting product: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error extracting products from page: {e}")

        return products

    async def _extract_product_from_card(self, card: ElementHandle, category: str) -> Optional[Product]:
        """Extract product data from a single card."""
        try:
            # Get product link and ID
            link_el = await card.query_selector(self.SELECTORS["product_link"])
            href = await card.get_attribute("href")
            if not href and link_el:
                href = await link_el.get_attribute("href")
            if not href:
                return None

            product_url = urljoin(settings.meesho_base_url, href)
            product_id = self._extract_product_id(product_url)

            # Get title
            title_el = await card.query_selector("img[alt]")
            title = (await title_el.get_attribute("alt") or "").strip() if title_el else ""
            card_text = await card.inner_text()
            if not title:
                title = next(
                    (
                        line.strip()
                        for line in card_text.splitlines()
                        if line.strip()
                        and not re.match(r"^\+\s*\d+\s+More$", line.strip(), re.IGNORECASE)
                        and not re.search(r"₹|Reviews?$|^\d+(?:\.\d+)?$", line.strip(), re.IGNORECASE)
                    ),
                    "",
                )
            if not title:
                return None

            # Skip if innerwear
            if self._is_innerwear_text(title):
                return None

            # Get prices
            card_prices = [
                int(value.replace(",", ""))
                for value in re.findall(r"₹\s*([\d,]+)", card_text)
            ]
            price = card_prices[0] if card_prices else await self._extract_price(card, self.SELECTORS["product_price"])
            original_price = (
                card_prices[1]
                if len(card_prices) > 1
                else await self._extract_price(card, self.SELECTORS["product_original_price"])
            )

            if original_price <= 0:
                original_price = price

            discount = 0
            if original_price > price:
                discount = int(((original_price - price) / original_price) * 100)

            # Get discount from badge if available
            discount_text = ""
            discount_el = await card.query_selector(self.SELECTORS["product_discount"])
            if discount_el:
                discount_text = await discount_el.inner_text()
            if not discount_text:
                discount_match = re.search(r"(\d+)\s*%\s*off", card_text, re.IGNORECASE)
                discount_text = discount_match.group(0) if discount_match else ""
            discount_match = re.search(r"(\d+)\s*%", discount_text)
            if discount_match:
                discount = int(discount_match.group(1))

            # Get rating
            rating = 0.0
            review_count = 0
            rating_match = re.search(r"(?:^|\n)\s*([0-5]\.\d)\s*(?:\n|$)", card_text)
            if rating_match:
                rating = float(rating_match.group(1))
            review_match = re.search(r"([\d,]+)\s+Reviews?", card_text, re.IGNORECASE)
            if review_match:
                review_count = int(review_match.group(1).replace(",", ""))
            if not price or not product_id:
                return None

            # Get images
            image_urls = []
            img_els = await card.query_selector_all(self.SELECTORS["product_image"])
            for img in img_els[:3]:  # Max 3 images
                src = await img.get_attribute("src") or await img.get_attribute("data-src")
                if src and src.startswith("http") and src not in image_urls:
                    image_urls.append(src)

            # Get product code (from URL or generate)
            product_code = self._generate_product_code(product_id, category)

            # Get fabric, color from title or use defaults
            fabric = self._extract_fabric(title)
            color = self._extract_color(title)
            size_options = self._get_default_sizes(category)

            # Check stock
            in_stock = True
            oos_el = await card.query_selector(self.SELECTORS["out_of_stock"])
            if oos_el:
                in_stock = False

            # Create affiliate URL
            product_parts = urlsplit(product_url)
            if settings.meesho_affiliate_id:
                query = dict(parse_qsl(product_parts.query))
                query["aff_id"] = settings.meesho_affiliate_id
                affiliate_url = urlunsplit(product_parts._replace(query=urlencode(query)))
            else:
                affiliate_url = product_url

            return Product(
                meesho_product_id=product_id,
                title=title,
                category=category,
                price=price,
                original_price=original_price,
                discount_percent=discount,
                rating=rating,
                review_count=review_count,
                image_urls=image_urls,
                affiliate_url=affiliate_url,
                product_code=product_code,
                fabric=fabric,
                color=color,
                size_options=size_options,
                in_stock=in_stock,
                scraped_at=datetime.now()
            )

        except Exception as e:
            logger.debug(f"Error extracting product from card: {e}")
            return None

    def _extract_product_id(self, url: str) -> str:
        """Extract product ID from URL."""
        # Meesho URLs typically have /product/PRODUCT_ID or similar
        patterns = [
            r'/product/([a-zA-Z0-9]+)',
            r'/p/([a-zA-Z0-9]+)',
            r'pid=([a-zA-Z0-9]+)',
            r'productId=([a-zA-Z0-9]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        # Fallback: use hash of URL
        return str(hash(url))[-10:]

    def _generate_product_code(self, product_id: str, category: str) -> str:
        """Generate short product code."""
        prefix = category[:3].upper()
        return f"{prefix}-{product_id[:6].upper()}"

    async def _extract_price(self, card: ElementHandle, selector: str) -> int:
        """Extract price from element."""
        try:
            el = await card.query_selector(selector)
            if el:
                text = await el.inner_text()
                # Extract numbers from text like "₹299" or "Rs. 299"
                numbers = re.findall(r'[\d,]+', text.replace(',', ''))
                if numbers:
                    return int(numbers[0])
        except Exception:
            pass
        return 0

    def _extract_fabric(self, title: str) -> str:
        """Extract fabric from title."""
        fabrics = [
            "handloom", "cotton", "silk", "chiffon", "georgette", "crepe",
            "linen", "rayon", "viscose", "polyester", "blend", "khaddar",
            "organza", "satin", "velvet", "net", "lace", "embroidered"
        ]
        title_lower = title.lower()
        for fabric in fabrics:
            if fabric in title_lower:
                return fabric.capitalize()
        return "Cotton"  # Default

    def _extract_color(self, title: str) -> str:
        """Extract color from title."""
        title_lower = title.lower()
        compound_colors = {
            "navy blue": "Navy",
            "olive green": "Olive",
            "navy": "Navy",
            "olive": "Olive",
        }
        for color, label in compound_colors.items():
            if f" {color} " in f" {title_lower} " or title_lower.endswith(f" {color}") or title_lower.startswith(f"{color} "):
                return label

        colors = [
            "red", "blue", "green", "yellow", "pink", "black", "white",
            "orange", "purple", "brown", "grey", "gray", "maroon", "teal",
            "beige", "cream", "gold", "silver", "peach", "magenta",
            "turquoise", "indigo", "violet", "coral", "mustard"
        ]
        title_lower = title.lower()
        for color in colors:
            if f" {color} " in f" {title_lower} " or title_lower.endswith(f" {color}") or title_lower.startswith(f"{color} "):
                return color.capitalize()
        return "Multicolor"  # Default

    def _get_default_sizes(self, category: str) -> List[str]:
        """Get default size options for category."""
        if category in ["kurtis", "ethnic-sets", "western-wear"]:
            return ["S", "M", "L", "XL", "XXL"]
        elif category == "sarees":
            return ["Free Size"]
        elif category == "handbags":
            return ["One Size"]
        return ["S", "M", "L", "XL"]

    def _is_innerwear(self, product: Product) -> bool:
        """Check if product is innerwear (strict exclusion)."""
        # Check title
        if self._is_innerwear_text(product.title):
            return True
        # Check category
        innerwear_categories = ["innerwear", "lingerie", "nightwear", "sleepwear"]
        if product.category.lower() in innerwear_categories:
            return True
        return False

    def _is_innerwear_text(self, text: str) -> bool:
        """Check if text contains innerwear keywords."""
        text_lower = text.lower()
        for keyword in settings.innerwear_exclude_keywords:
            if keyword in text_lower:
                logger.debug(f"Excluding innerwear: '{keyword}' found in '{text}'")
                return True
        return False

    async def _go_to_next_page(self) -> bool:
        """Navigate to next page."""
        try:
            next_btn = await self.page.query_selector(self.SELECTORS["next_page"])
            if next_btn:
                await next_btn.click()
                await self.page.wait_for_load_state("networkidle")
                await self._wait_for_products()
                return True
        except Exception as e:
            logger.debug(f"No next page or error: {e}")
        return False

    async def get_product_details(self, product_url: str) -> Optional[Dict[str, Any]]:
        """Get detailed product info from product page (for Ken Burns images)."""
        try:
            await self.page.goto(product_url, wait_until="networkidle", timeout=settings.meesho_request_timeout_ms)

            # Extract high-res images for Ken Burns effect
            image_urls = []
            img_selectors = [
                "[data-testid='product-image-gallery'] img",
                ".product-gallery img",
                "#product-images img",
                ".ProductImages img"
            ]

            for selector in img_selectors:
                imgs = await self.page.query_selector_all(selector)
                for img in imgs:
                    src = await img.get_attribute("src") or await img.get_attribute("data-src")
                    if src and src.startswith("http") and src not in image_urls:
                        image_urls.append(src)

            # Extract more details
            title_el = await self.page.query_selector("h1, [data-testid='product-title']")
            title = (await title_el.inner_text()).strip() if title_el else ""

            return {
                "image_urls": image_urls[:5],  # Max 5 for Ken Burns
                "title": title
            }
        except Exception as e:
            logger.error(f"Error getting product details: {e}")
            return None


# =============================================================================
# Convenience Functions
# =============================================================================

async def extract_products(headless: bool = True, max_products: Optional[int] = None) -> Dict[str, List[Product]]:
    """Convenience function to extract products from all categories."""
    async with MeeshoExtractor(headless=headless) as extractor:
        return await extractor.scrape_all_categories(max_products=max_products)


async def extract_category(category: str, headless: bool = True, max_products: Optional[int] = None) -> List[Product]:
    """Convenience function to extract products from single category."""
    async with MeeshoExtractor(headless=headless) as extractor:
        return await extractor.scrape_category(category, max_products=max_products)
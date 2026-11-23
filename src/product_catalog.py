"""Export and import a weekly product catalog for the publishing pipeline."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

from src.database import Product, save_product


CATALOG_SCHEMA_VERSION = 1
CATALOG_VALID_DAYS = 7
MIN_WEEKLY_PRODUCTS = 21

_PRODUCT_FIELDS = (
    "meesho_product_id",
    "title",
    "category",
    "price",
    "original_price",
    "discount_percent",
    "rating",
    "review_count",
    "image_urls",
    "affiliate_url",
    "product_code",
    "fabric",
    "color",
    "size_options",
    "in_stock",
    "scraped_at",
)


class ProductCatalogError(ValueError):
    """Raised when a weekly product catalog is missing or invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ProductCatalogError(f"Catalog {field_name} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProductCatalogError(f"Catalog {field_name} is not a valid ISO timestamp.") from error
    if parsed.tzinfo is None:
        raise ProductCatalogError(f"Catalog {field_name} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _product_to_record(product: Product) -> dict:
    record = {field: getattr(product, field) for field in _PRODUCT_FIELDS}
    record["scraped_at"] = _format_timestamp(product.scraped_at or _utc_now())
    return record


def write_product_catalog(
    products_by_category: Mapping[str, Sequence[Product]],
    output_path: Path,
    generated_at: Optional[datetime] = None,
) -> int:
    """Write a fresh, de-duplicated catalog; leave any old file intact on failure."""
    unique_products = {}
    for products in products_by_category.values():
        for product in products:
            if (
                product.in_stock
                and product.title.strip()
                and product.price > 0
                and product.affiliate_url.startswith(("https://", "http://"))
                and any(url.startswith(("https://", "http://")) for url in product.image_urls)
            ):
                unique_products.setdefault(product.meesho_product_id, product)

    products = list(unique_products.values())
    if len(products) < MIN_WEEKLY_PRODUCTS:
        raise ProductCatalogError(
            f"Only {len(products)} usable products were extracted; at least "
            f"{MIN_WEEKLY_PRODUCTS} are required for a week. The catalog was not written."
        )

    created = generated_at or _utc_now()
    if created.tzinfo is None:
        created = created.astimezone()
    created = created.astimezone(timezone.utc)
    expires = created + timedelta(days=CATALOG_VALID_DAYS)
    document = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": _format_timestamp(created),
        "expires_at": _format_timestamp(expires),
        "product_count": len(products),
        "products": [_product_to_record(product) for product in products],
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(output_path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return len(products)


def _record_to_product(record: object, index: int) -> Product:
    if not isinstance(record, dict):
        raise ProductCatalogError(f"Product entry {index} must be a JSON object.")

    missing = [field for field in _PRODUCT_FIELDS if field not in record]
    if missing:
        raise ProductCatalogError(
            f"Product entry {index} is missing required fields: {', '.join(missing)}."
        )

    text_fields = (
        "meesho_product_id", "title", "category", "affiliate_url",
        "product_code", "fabric", "color",
    )
    for field in text_fields:
        if not isinstance(record[field], str) or not record[field].strip():
            raise ProductCatalogError(f"Product entry {index} has an invalid {field}.")

    integer_fields = ("price", "original_price", "discount_percent", "review_count")
    for field in integer_fields:
        if isinstance(record[field], bool) or not isinstance(record[field], int):
            raise ProductCatalogError(f"Product entry {index} has an invalid {field}.")
    if (
        record["price"] <= 0
        or record["original_price"] < record["price"]
        or not 0 <= record["discount_percent"] <= 100
        or record["review_count"] < 0
    ):
        raise ProductCatalogError(f"Product entry {index} has invalid price values.")

    if isinstance(record["rating"], bool) or not isinstance(record["rating"], (int, float)):
        raise ProductCatalogError(f"Product entry {index} has an invalid rating.")
    if not 0 <= record["rating"] <= 5:
        raise ProductCatalogError(f"Product entry {index} rating must be between 0 and 5.")

    image_urls = record["image_urls"]
    if not isinstance(image_urls, list) or not image_urls or any(
        not isinstance(url, str) or not url.startswith(("https://", "http://"))
        for url in image_urls
    ):
        raise ProductCatalogError(f"Product entry {index} must have valid image URLs.")

    sizes = record["size_options"]
    if not isinstance(sizes, list) or any(not isinstance(size, str) for size in sizes):
        raise ProductCatalogError(f"Product entry {index} has invalid size options.")
    if not isinstance(record["in_stock"], bool) or not record["in_stock"]:
        raise ProductCatalogError(f"Product entry {index} must be in stock.")
    if not record["affiliate_url"].startswith(("https://", "http://")):
        raise ProductCatalogError(f"Product entry {index} has an invalid product link.")

    return Product(
        meesho_product_id=record["meesho_product_id"],
        title=record["title"],
        category=record["category"],
        price=record["price"],
        original_price=record["original_price"],
        discount_percent=record["discount_percent"],
        rating=float(record["rating"]),
        review_count=record["review_count"],
        image_urls=image_urls,
        affiliate_url=record["affiliate_url"],
        product_code=record["product_code"],
        fabric=record["fabric"],
        color=record["color"],
        size_options=sizes,
        in_stock=True,
        scraped_at=_parse_timestamp(record["scraped_at"], f"products[{index}].scraped_at"),
    )


def read_product_catalog(
    catalog_path: Path,
    now: Optional[datetime] = None,
) -> list[Product]:
    """Load and validate a non-expired weekly catalog."""
    catalog_path = Path(catalog_path)
    try:
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProductCatalogError(f"Product catalog not found: {catalog_path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProductCatalogError(f"Could not read product catalog {catalog_path}: {error}") from error

    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != CATALOG_SCHEMA_VERSION
    ):
        raise ProductCatalogError(f"Unsupported or invalid catalog schema in {catalog_path}.")

    generated_at = _parse_timestamp(document.get("generated_at"), "generated_at")
    expires_at = _parse_timestamp(document.get("expires_at"), "expires_at")
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.astimezone()
    current_time = current_time.astimezone(timezone.utc)
    if generated_at > current_time + timedelta(minutes=5):
        raise ProductCatalogError("Product catalog generation time is in the future.")
    if expires_at <= generated_at or expires_at > generated_at + timedelta(days=CATALOG_VALID_DAYS):
        raise ProductCatalogError("Product catalog has an invalid expiry timestamp.")
    if (
        expires_at <= current_time
        or generated_at + timedelta(days=CATALOG_VALID_DAYS) <= current_time
    ):
        raise ProductCatalogError(
            f"Product catalog expired at {_format_timestamp(expires_at)}; "
            "regenerate and upload it before running the pipeline."
        )

    raw_products = document.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ProductCatalogError("Product catalog must contain a non-empty products list.")
    if (
        type(document.get("product_count")) is not int
        or document["product_count"] != len(raw_products)
    ):
        raise ProductCatalogError("Catalog product_count does not match the products list.")

    products = [_record_to_product(record, index) for index, record in enumerate(raw_products, start=1)]
    ids = [product.meesho_product_id for product in products]
    if len(ids) != len(set(ids)):
        raise ProductCatalogError("Product catalog contains duplicate Meesho product IDs.")
    if len(products) < MIN_WEEKLY_PRODUCTS:
        raise ProductCatalogError(
            f"Product catalog has only {len(products)} usable products; at least "
            f"{MIN_WEEKLY_PRODUCTS} are required for a week."
        )
    return products


def import_product_catalog(catalog_path: Path) -> int:
    """Import validated catalog products into the pipeline database."""
    products = read_product_catalog(catalog_path)
    for product in products:
        save_product(product)
    return len(products)

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.database import Product
import src.product_catalog as catalog
from src.product_catalog import (
    ProductCatalogError,
    import_product_catalog,
    read_product_catalog,
    write_product_catalog,
)


def make_product(product_id: str = "ITEM-1", **overrides) -> Product:
    values = {
        "meesho_product_id": product_id,
        "title": f"Product {product_id}",
        "category": "kurtis",
        "price": 299,
        "original_price": 599,
        "discount_percent": 50,
        "rating": 4.5,
        "review_count": 120,
        "image_urls": ["https://images.example.com/product.jpg"],
        "affiliate_url": f"https://meesho.com/p/{product_id}",
        "product_code": f"KUR-{product_id}",
        "fabric": "Cotton",
        "color": "Blue",
        "size_options": ["S", "M", "L"],
        "in_stock": True,
        "scraped_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Product(**values)


def test_catalog_round_trip_and_import(tmp_path, monkeypatch):
    output = tmp_path / "data" / "product_catalog.json"
    products = [make_product(f"ITEM-{index}") for index in range(21)]

    count = write_product_catalog({"kurtis": products}, output)

    assert count == 21
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["product_count"] == 21
    assert "used_for_video" not in document["products"][0]
    loaded = read_product_catalog(output)
    assert [product.meesho_product_id for product in loaded] == [
        f"ITEM-{index}" for index in range(21)
    ]

    imported = []
    monkeypatch.setattr(catalog, "save_product", imported.append)
    assert import_product_catalog(output) == 21
    assert [product.meesho_product_id for product in imported] == [
        f"ITEM-{index}" for index in range(21)
    ]


def test_expired_catalog_is_rejected(tmp_path):
    generated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    output = tmp_path / "product_catalog.json"
    write_product_catalog(
        {"kurtis": [make_product(f"ITEM-{index}") for index in range(21)]},
        output,
        generated_at=generated_at,
    )

    with pytest.raises(ProductCatalogError, match="expired"):
        read_product_catalog(output, now=generated_at + timedelta(days=7))


def test_failed_refresh_does_not_replace_existing_catalog(tmp_path):
    output = tmp_path / "product_catalog.json"
    output.write_text("existing catalog", encoding="utf-8")

    with pytest.raises(ProductCatalogError, match="at least 21"):
        write_product_catalog(
            {"kurtis": [make_product(in_stock=False)]},
            output,
        )

    assert output.read_text(encoding="utf-8") == "existing catalog"


def test_import_rejects_catalog_with_too_few_products(tmp_path):
    output = tmp_path / "product_catalog.json"
    products = [make_product(f"ITEM-{index}") for index in range(2)]
    generated_at = datetime.now(timezone.utc)
    document = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(days=7)).isoformat(),
        "product_count": len(products),
        "products": [
            {
                **product.__dict__,
                "scraped_at": product.scraped_at.isoformat(),
                "used_for_video": None,
                "used_at": None,
            }
            for product in products
        ],
    }
    output.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProductCatalogError, match="at least 21"):
        read_product_catalog(output)

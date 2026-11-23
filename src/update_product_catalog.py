"""Generate the weekly Meesho product catalog JSON on a machine that can access Meesho."""
import argparse
import asyncio
import sys
from pathlib import Path

from src.config import settings
from src.extractor import ProductExtractionError, extract_products
from src.product_catalog import ProductCatalogError, write_product_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/product_catalog.json"),
        help="Output JSON path (default: data/product_catalog.json)",
    )
    parser.add_argument(
        "--max-per-category",
        type=int,
        default=settings.meesho_max_products_per_category,
        help=f"Maximum products per category (default: {settings.meesho_max_products_per_category})",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser while extracting",
    )
    args = parser.parse_args()

    if args.max_per_category < 1:
        parser.error("--max-per-category must be at least 1")
    try:
        products_by_category = asyncio.run(
            extract_products(
                headless=not args.no_headless,
                max_products=args.max_per_category,
            )
        )
        count = write_product_catalog(
            products_by_category,
            args.output,
        )
    except (ProductExtractionError, ProductCatalogError) as error:
        print(f"Catalog update failed: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {count} products to {args.output}")
    print("Upload this file to GitHub before the current catalog expires in 7 days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

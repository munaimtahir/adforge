#!/usr/bin/env python3
"""Create or update a Product and import its Product Truth from products/<slug>/.

The application only ever auto-bootstraps the one hardcoded `warranty-vault`
product (see `adforge.bootstrap.ensure_warranty_vault_product`); there is no way,
through the web UI or otherwise, to register any other product. This script fills
that operational gap using the same public `Services`/`ProductTruthService` APIs
the application itself uses -- no new domain logic, just wiring already-tested
building blocks together for an operation the app doesn't otherwise expose.

Usage:
    python3 scripts/provision_product.py <slug> [--record path] [--truth path]

Reads, by default:
    products/<slug>/product-record.json
    products/<slug>/truth/PRODUCT_TRUTH.json  (or .md)

Sets ADFORGE_DATA_ROOT / ADFORGE_SCHEMA_ROOT from the environment, matching the
running application's own configuration (defaults: .adforge-runtime, schemas).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from adforge.models import Product  # noqa: E402
from adforge.product_truth import ProductTruthService  # noqa: E402
from adforge.services import Services  # noqa: E402


def find_truth_file(truth_dir: Path) -> Path:
    for candidate in ("PRODUCT_TRUTH.json", "PRODUCT_TRUTH.md"):
        path = truth_dir / candidate
        if path.is_file():
            return path
    raise FileNotFoundError(f"no PRODUCT_TRUTH.json or .md found under {truth_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="product slug, e.g. demotask")
    parser.add_argument("--record", type=Path, default=None)
    parser.add_argument("--truth", type=Path, default=None)
    args = parser.parse_args()

    product_dir = Path("products") / args.slug
    record_path = args.record or product_dir / "product-record.json"
    truth_path = args.truth or find_truth_file(product_dir / "truth")

    runtime_root = Path(os.environ.get("ADFORGE_DATA_ROOT", ".adforge-runtime"))
    schema_root = Path(os.environ.get("ADFORGE_SCHEMA_ROOT", "schemas"))
    services = Services(runtime_root, schema_root)
    services.initialize()

    record = json.loads(record_path.read_text())
    product = Product.model_validate(record)
    existing = services.products.get(product.id)
    saved = services.products.save(existing.model_copy(update=record) if existing else product)

    truth_service = ProductTruthService(services)
    truth = truth_service.parse(truth_path)
    updated = truth_service.import_for_product(saved, truth_path)

    print(
        json.dumps(
            {
                "product_id": updated.id,
                "slug": updated.slug,
                "truth_readiness": updated.truth_readiness,
                "truth_source_path": updated.truth_source_path,
                "approved_features": truth["approved_features"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

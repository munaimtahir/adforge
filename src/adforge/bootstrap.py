"""Idempotent first-product bootstrap without speculative Product Truth."""

from __future__ import annotations

import json
from pathlib import Path

from adforge.models import Product
from adforge.services import Services


def ensure_warranty_vault_product(
    services: Services, record_path: Path | None = None
) -> Product:
    existing = services.products.get("warranty-vault")
    if existing is not None:
        return existing
    source = record_path or Path("products/warranty-vault/product-record.json")
    product = Product.model_validate(json.loads(source.read_text()))
    runtime_product = services.storage.root / "products" / product.slug
    for directory in ("truth", "assets/brand", "assets/references", "apk"):
        (runtime_product / directory).mkdir(parents=True, exist_ok=True)
    return services.products.save(product)

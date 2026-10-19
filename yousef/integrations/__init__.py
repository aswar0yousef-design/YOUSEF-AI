"""Official-API connectors used by the agents (GitHub, Vercel; stores come in phase 4)."""

from .github import GitHub
from .shopify import Shopify, StoreError
from .stores import Stores, sales_summary
from .vercel import Vercel
from .woocommerce import WooCommerce

__all__ = ["GitHub", "Shopify", "StoreError", "Stores", "sales_summary", "Vercel", "WooCommerce"]

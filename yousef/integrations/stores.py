"""Connected stores: settings entry + secret in keys.dat + a client for the platform."""

from __future__ import annotations

import secrets
from collections import Counter, defaultdict

from ..models.base import ProviderError
from .shopify import Shopify, StoreError
from .woocommerce import WooCommerce

PLATFORM_LABEL = {"shopify": "Shopify", "woocommerce": "WooCommerce"}


def secret_name(store_id: str) -> str:
    return f"store:{store_id}"


def make_client(entry: dict, secret: str):
    if entry["platform"] == "shopify":
        return Shopify(entry["url"], secret, entry.get("api_version") or None)
    if entry["platform"] == "woocommerce":
        return WooCommerce(entry["url"], secret)
    raise StoreError(f"منصة غير مدعومة: {entry['platform']}")


class Stores:
    """Lookup and management of the user's connected stores."""

    def __init__(self, settings, keystore, factory=make_client):
        self.settings = settings
        self.keystore = keystore
        self.factory = factory

    def entries(self) -> list[dict]:
        return self.settings.get()["stores"]

    def public(self) -> list[dict]:
        return [dict(e, label=PLATFORM_LABEL[e["platform"]], connected=self.keystore.has(secret_name(e["id"])))
                for e in self.entries()]

    def find(self, ref: str | None) -> dict | None:
        """By id or name (case-insensitive); with one store, an empty ref means that store."""
        stores = self.entries()
        if not ref:
            return stores[0] if len(stores) == 1 else None
        ref_l = str(ref).strip().lower()
        for e in stores:
            if e["id"] == ref_l or e["name"].lower() == ref_l:
                return e
        return None

    def client(self, entry: dict):
        secret = self.keystore.get(secret_name(entry["id"]))
        if not secret:
            raise StoreError(f"مفتاح متجر «{entry['name']}» غير محفوظ على هذا الجهاز")
        return self.factory(entry, secret)

    def add(self, name: str, platform: str, url: str, secret: str, api_version: str = "") -> tuple[dict, dict]:
        """Test the connection first; save only if it works. Returns (entry, shop info)."""
        name = (name or "").strip()
        if not name:
            raise StoreError("اكتب اسمًا للمتجر")
        if platform not in PLATFORM_LABEL:
            raise StoreError("اختر Shopify أو WooCommerce")
        if any(e["name"].lower() == name.lower() for e in self.entries()):
            raise StoreError(f"يوجد متجر باسم «{name}»")
        entry = {"id": secrets.token_hex(4), "name": name[:60], "platform": platform, "url": url.strip(),
                 "api_version": (api_version or "").strip()}
        try:
            info = self.factory(entry, secret).info()
        except ProviderError as e:
            raise StoreError(f"تعذّر الاتصال بالمتجر: {e}") from None
        self.keystore.set(secret_name(entry["id"]), secret)
        self.settings.update({"stores": self.entries() + [entry]})
        return entry, info

    def test(self, store_id: str) -> dict:
        entry = self.find(store_id)
        if not entry:
            raise StoreError("المتجر غير موجود")
        return self.client(entry).info()

    def remove(self, store_id: str) -> bool:
        current = self.entries()
        kept = [e for e in current if e["id"] != store_id]
        if len(kept) == len(current):
            return False
        self.settings.update({"stores": kept})
        self.keystore.delete(secret_name(store_id))
        return True


def sales_summary(orders: list[dict], top: int = 10) -> dict:
    revenue: dict[str, float] = defaultdict(float)
    statuses: Counter = Counter()
    sold: Counter = Counter()
    for o in orders:
        revenue[o.get("currency") or "?"] += float(o.get("total") or 0)
        statuses[o.get("paid") or "?"] += 1
        for item in o.get("items") or []:
            sold[item.get("title") or "?"] += int(item.get("quantity") or 0)
    count = len(orders)
    return {
        "orders": count,
        "revenue": {k: round(v, 2) for k, v in revenue.items()},
        "average_order": {k: round(v / count, 2) for k, v in revenue.items()} if count else {},
        "statuses": dict(statuses),
        "top_products": [{"title": t, "quantity": q} for t, q in sold.most_common(top)],
    }

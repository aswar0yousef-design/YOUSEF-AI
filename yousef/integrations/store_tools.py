"""Tools the store manager agent calls, one JSON object at a time.

Reads go straight to the store. Product creations and updates are only
recorded as proposals (with before/after values); the orchestrator runs them
after the user approves, and an update is skipped if the product changed in
the store meanwhile. Nothing here can delete products or touch orders.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..models.base import ProviderError
from .shopify import StoreError
from .stores import PLATFORM_LABEL, Stores, sales_summary

OBSERVATION_LIMIT = 8000
MAX_PROPOSALS = 25
PRICE_FIELDS = ("price", "compare_at_price", "sale_price")
FIELDS = {
    "shopify": ("title", "description", "price", "compare_at_price", "sku", "status", "tags", "vendor", "product_type"),
    "woocommerce": ("title", "description", "price", "sale_price", "sku", "status", "tags"),
}
VARIANT_FIELDS = ("price", "compare_at_price", "sku")  # Shopify keeps these on the variant

TOOL_HELP = """{"tool": "list_stores"}                                                connected stores
{"tool": "store_info", "store": "name"}                                    store name, currency, product count
{"tool": "list_products", "store": "name", "search": "عود", "limit": 20}  recent products (search optional)
{"tool": "get_product", "store": "name", "id": "..."}                      one product with its variants and description
{"tool": "list_orders", "store": "name", "days": 30, "limit": 20}         recent orders (numbers, totals, items; no customer data)
{"tool": "sales_summary", "store": "name", "days": 30}                     order count, revenue, best sellers
{"tool": "propose_product", "store": "name", "title": "...", "price": "9.500", "description": "...", "sku": "...", "tags": ["..."], "status": "draft"}   propose a new product
{"tool": "propose_update", "store": "name", "id": "...", "price": "8.000", "title": "..."}   propose changes to an existing product (only the fields to change)
{"done": "your answer for the user"}                                       finish"""

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
_TAG = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?|-?\.\d+")


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... (cut at {limit} characters)"


def plain(html: str | None, limit: int = 600) -> str:
    text = " ".join(_TAG.sub(" ", html or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def parse_price(value) -> str:
    """'٩٫٥', 9.5 or '9.500' -> '9.500'-style string; rejects negatives and nonsense."""
    if isinstance(value, bool) or value is None:
        raise StoreError("price must be a number")
    text = str(value).translate(_ARABIC_DIGITS).replace(",", "")
    numbers = _NUMBER.findall(text)  # currency text around the number is ignored
    if len(numbers) != 1:
        raise StoreError(f"invalid price: {value}")
    try:
        number = Decimal(numbers[0])
    except InvalidOperation:
        raise StoreError(f"invalid price: {value}") from None
    if not number.is_finite() or number < 0 or number >= Decimal("10000000"):
        raise StoreError(f"invalid price: {value}")
    if number.as_tuple().exponent < -3:
        number = number.quantize(Decimal("0.001"))
    return str(number)


def _tags(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("،", ",").split(",")
    if not isinstance(value, list):
        raise StoreError("tags must be a list")
    tags = [str(t).strip() for t in value if str(t).strip()]
    if len(tags) > 50 or any(len(t) > 80 for t in tags):
        raise StoreError("too many or too long tags")
    return list(dict.fromkeys(tags))


def clean_fields(platform: str, raw: dict, creating: bool) -> dict:
    """Keep the fields this platform supports, validated."""
    out = {}
    for field in FIELDS[platform]:
        if field not in raw or raw[field] is None:
            continue
        value = raw[field]
        if field in PRICE_FIELDS:
            value = "" if value == "" and field != "price" else parse_price(value)
        elif field == "status":
            value = str(value).strip().lower()
            value = {"publish": "active", "published": "active", "draft": "draft", "active": "active"}.get(value, "")
            if not value:
                raise StoreError('status must be "draft" or "active"')
        elif field == "tags":
            value = _tags(value)
        else:
            value = str(value).strip()
            limit = 20000 if field == "description" else 255
            if len(value) > limit:
                raise StoreError(f"{field} is too long")
            if field == "title" and not value:
                raise StoreError("title cannot be empty")
        out[field] = value
    unknown = [k for k in raw if k not in FIELDS[platform] and k not in ("tool", "store", "id", "variant_id")]
    if unknown:
        raise StoreError(f"{PLATFORM_LABEL[platform]} products do not support: {', '.join(unknown)}. "
                         f"Supported: {', '.join(FIELDS[platform])}")
    if creating:
        if not out.get("title"):
            raise StoreError("a new product needs a title")
        out.setdefault("status", "draft")
    return out


def current_values(product: dict, fields, variant_id: str | None = None) -> dict:
    """The product's present values for `fields`, in the same shape as proposals."""
    variant = None
    for v in product.get("variants") or []:
        if variant_id is None or v.get("id") == variant_id:
            variant = v
            break
    out = {}
    for field in fields:
        if field in VARIANT_FIELDS and variant is not None:
            out[field] = variant.get(field) if field != "sku" else (variant.get("sku") or "")
        elif field == "description":
            out[field] = product.get("description") or ""
        elif field == "tags":
            out[field] = list(product.get("tags") or [])
        else:
            value = product.get(field)
            out[field] = "" if value is None else value
    return out


def comparable(field: str, value):
    if value is None:
        return ""
    if field in PRICE_FIELDS:
        try:
            return str(Decimal(str(value)).normalize()) if str(value) != "" else ""
        except InvalidOperation:
            return str(value)
    if field == "tags":
        return sorted(str(t).strip() for t in (value or []))
    return str(value).strip()


PRICE_LABEL = {"price": "السعر", "compare_at_price": "السعر قبل الخصم", "sale_price": "سعر التخفيض"}


def price_warnings(before: dict, after: dict) -> list[str]:
    """Arabic notes shown in the approval card for prices worth a second look."""
    warnings = []
    for field in PRICE_FIELDS:
        if field not in after or after[field] == "":
            continue
        new = Decimal(after[field])
        label = PRICE_LABEL[field]
        if new == 0:
            warnings.append(f"{label}: القيمة الجديدة صفر")
            continue
        try:
            old = Decimal(str(before.get(field) or ""))
        except InvalidOperation:
            continue
        if old > 0 and abs(new - old) / old > Decimal("0.5"):
            pct = int((new - old) / old * 100)
            direction = "ارتفاع" if pct > 0 else "انخفاض"
            warnings.append(f"{label}: {direction} كبير بنسبة {abs(pct)}%")
    return warnings


class StoreToolbox:
    def __init__(self, stores: Stores, default_store: str | None = None):
        self.stores = stores
        self.default_store = default_store
        self.trace: list[dict] = []
        self.proposals: list[dict] = []  # {"kind", "payload"} in the order they were made
        self._clients: dict[str, object] = {}

    def help(self) -> str:
        return TOOL_HELP

    def describe_stores(self) -> str:
        lines = []
        for e in self.stores.entries():
            mark = " (default)" if self.default_store and e["id"] == self.default_store else ""
            lines.append(f'- "{e["name"]}" ({PLATFORM_LABEL[e["platform"]]}){mark}')
        return "\n".join(lines) or "(none)"

    # ---- helpers ------------------------------------------------------------------
    def _store(self, call: dict) -> tuple[dict, object]:
        ref = str(call.get("store") or "").strip() or self.default_store
        entry = self.stores.find(ref)
        if entry is None:
            names = ", ".join(f'"{e["name"]}"' for e in self.stores.entries()) or "none"
            if ref:
                raise StoreError(f'store "{ref}" not found. Connected stores: {names}')
            raise StoreError(f'several stores are connected; say which one with "store". Connected stores: {names}')
        if entry["id"] not in self._clients:
            self._clients[entry["id"]] = self.stores.client(entry)
        return entry, self._clients[entry["id"]]

    @staticmethod
    def _limit(call: dict, key: str, default: int, top: int) -> int:
        try:
            return max(1, min(int(call.get(key) or default), top))
        except (TypeError, ValueError):
            return default

    # ---- dispatch ---------------------------------------------------------------
    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("title") or call.get("id") or call.get("search") or call.get("store") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise StoreError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            out = handler(call)
            self.trace.append({"tool": tool, "target": target[:200], "ok": True})
            return _clip(out)
        except (ProviderError, ValueError, TypeError) as e:
            self.trace.append({"tool": tool, "target": target[:200], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    # ---- reading ------------------------------------------------------------------
    def tool_list_stores(self, call: dict) -> str:
        return self.describe_stores()

    def tool_store_info(self, call: dict) -> str:
        entry, client = self._store(call)
        info = client.info()
        count = f", {info['products']} products" if info.get("products") is not None else ""
        return f'{entry["name"]} ({PLATFORM_LABEL[entry["platform"]]}): {info.get("name")}, currency {info.get("currency")}{count}'

    def tool_list_products(self, call: dict) -> str:
        entry, client = self._store(call)
        search = str(call.get("search") or "").strip() or None
        products = client.products(search, self._limit(call, "limit", 20, 50))
        if not products:
            return "No products found."
        lines = [f"{len(products)} products in {entry['name']} (id | title | status | price | sku | inventory):"]
        for p in products:
            extra = f" | {len(p['variants'])} variants" if len(p.get("variants") or []) > 1 else ""
            lines.append(f"{p['id']} | {p['title']} | {p['status']} | {p.get('price')} | {p.get('sku') or '-'} | "
                         f"{p.get('inventory') if p.get('inventory') is not None else '-'}{extra}")
        return "\n".join(lines)

    def tool_get_product(self, call: dict) -> str:
        entry, client = self._store(call)
        pid = str(call.get("id") or "").strip()
        if not pid:
            raise StoreError("get_product needs an id")
        p = client.product(pid)
        lines = [f"id: {p['id']}", f"title: {p['title']}", f"status: {p['status']}", f"price: {p.get('price')}",
                 f"sku: {p.get('sku') or '-'}", f"inventory: {p.get('inventory')}", f"tags: {', '.join(p.get('tags') or []) or '-'}"]
        for key in ("vendor", "product_type", "sale_price", "categories"):
            if p.get(key):
                lines.append(f"{key}: {p[key] if not isinstance(p[key], list) else ', '.join(p[key])}")
        if len(p.get("variants") or []) > 1:
            lines.append("variants (id | title | price | sku | inventory):")
            lines.extend(f"  {v['id']} | {v['title']} | {v['price']} | {v.get('sku') or '-'} | {v.get('inventory')}"
                         for v in p["variants"])
        lines.append(f"description: {plain(p.get('description'), 1500) or '-'}")
        return "\n".join(lines)

    def tool_list_orders(self, call: dict) -> str:
        entry, client = self._store(call)
        days = self._limit(call, "days", 30, 365)
        orders = client.orders(days=days, limit=self._limit(call, "limit", 20, 100))
        if not orders:
            return f"No orders in the last {days} days."
        lines = [f"{len(orders)} orders in the last {days} days (number | date | total | payment | fulfillment | items):"]
        for o in orders:
            items = ", ".join(f"{i['title']} ×{i['quantity']}" for i in o.get("items") or [])
            lines.append(f"{o['number']} | {str(o.get('created') or '')[:10]} | {o['total']} {o.get('currency', '')} | "
                         f"{o.get('paid')} | {o.get('fulfillment')} | {items}")
        return "\n".join(lines)

    def tool_sales_summary(self, call: dict) -> str:
        entry, client = self._store(call)
        days = self._limit(call, "days", 30, 365)
        s = sales_summary(client.orders(days=days, limit=250))
        lines = [f"{entry['name']}, last {days} days: {s['orders']} orders"]
        for cur, total in s["revenue"].items():
            lines.append(f"revenue {cur}: {total} (average order {s['average_order'].get(cur)})")
        if s["statuses"]:
            lines.append("payment status: " + ", ".join(f"{k} {v}" for k, v in s["statuses"].items()))
        if s["top_products"]:
            lines.append("best sellers: " + ", ".join(f"{t['title']} ({t['quantity']})" for t in s["top_products"]))
        if s["orders"] >= 250:
            lines.append("(only the latest 250 orders were counted)")
        return "\n".join(lines)

    # ---- proposals ----------------------------------------------------------------
    def _add(self, kind: str, payload: dict) -> None:
        if len(self.proposals) >= MAX_PROPOSALS:
            raise StoreError(f"at most {MAX_PROPOSALS} proposals per request")
        self.proposals.append({"kind": kind, "payload": payload})

    def tool_propose_product(self, call: dict) -> str:
        entry, client = self._store(call)
        fields = clean_fields(entry["platform"], call, creating=True)
        payload = {"store_id": entry["id"], "store": entry["name"], "platform": entry["platform"],
                   "title": fields["title"], "before": {}, "after": fields,
                   "warnings": price_warnings({}, fields)}
        self._add("store_create_product", payload)
        return (f'Proposed new product "{fields["title"]}" in {entry["name"]} as {fields["status"]}. '
                "It is created only if the user approves.")

    def tool_propose_update(self, call: dict) -> str:
        entry, client = self._store(call)
        pid = str(call.get("id") or "").strip()
        if not pid:
            raise StoreError("propose_update needs the product id (use list_products or get_product)")
        fields = clean_fields(entry["platform"], call, creating=False)
        if not fields:
            raise StoreError(f"say which fields to change: {', '.join(FIELDS[entry['platform']])}")
        product = client.product(pid)
        variant_id = str(call.get("variant_id") or "").strip() or None
        variants = product.get("variants") or []
        if entry["platform"] == "shopify" and any(f in fields for f in VARIANT_FIELDS):
            if variant_id is None and len(variants) > 1:
                listed = "; ".join(f"{v['id']} ({v['title']}, {v['price']})" for v in variants)
                raise StoreError(f"this product has {len(variants)} variants; add \"variant_id\". Variants: {listed}")
            if variant_id is None and variants:
                variant_id = variants[0]["id"]
            if variant_id not in {v["id"] for v in variants}:
                raise StoreError(f"variant {variant_id} is not part of this product")
        before = current_values(product, fields, variant_id)
        changed = {k: v for k, v in fields.items() if comparable(k, v) != comparable(k, before.get(k))}
        if not changed:
            return f'No change: "{product["title"]}" already has these values.'
        before = {k: before[k] for k in changed}
        # several proposals for the same product (and variant) become one
        for p in self.proposals:
            pl = p["payload"]
            same_variant = variant_id is None or pl.get("variant_id") in (None, variant_id)
            if (p["kind"] == "store_update_product" and pl["store_id"] == entry["id"]
                    and str(pl["product_id"]) == str(product["id"]) and same_variant):
                if variant_id and not pl.get("variant_id"):
                    pl["variant_id"] = variant_id
                for k, v in changed.items():
                    pl["before"].setdefault(k, before[k])
                    pl["after"][k] = v
                pl["warnings"] = price_warnings(pl["before"], pl["after"])
                return f'Updated the proposal for "{product["title"]}": ' + ", ".join(f"{k} → {v}" for k, v in pl["after"].items())
        payload = {"store_id": entry["id"], "store": entry["name"], "platform": entry["platform"],
                   "product_id": product["id"], "variant_id": variant_id, "title": product["title"],
                   "before": before, "after": changed, "warnings": price_warnings(before, changed)}
        self._add("store_update_product", payload)
        summary = ", ".join(f"{k}: {before[k]!s:.40} → {v!s:.40}" for k, v in changed.items())
        return f'Proposed update for "{product["title"]}": {summary}. It runs only if the user approves.'

    def action_list(self) -> list[dict]:
        return list(self.proposals)

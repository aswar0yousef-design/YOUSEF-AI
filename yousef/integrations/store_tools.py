"""Tools the store manager agent calls, one JSON object at a time.

Reads go straight to the store. Product creations and updates, offers
(discounts and coupons) and bundles are only recorded as proposals; the
orchestrator runs them after the user approves, and a product update is
skipped if the product changed in the store meanwhile. Nothing here can
delete products or touch orders.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from ..models.base import ProviderError
from .shopify import StoreError
from .stores import PLATFORM_LABEL, Stores, sales_summary

OBSERVATION_LIMIT = 8000
MAX_PROPOSALS = 25
PRICE_FIELDS = ("price", "compare_at_price", "sale_price")
FIELDS = {
    "shopify": ("title", "description", "price", "compare_at_price", "sku", "status", "tags", "vendor", "product_type",
                "seo_title", "seo_description"),
    "woocommerce": ("title", "description", "price", "sale_price", "sale_from", "sale_to", "sku", "status", "tags"),
}
DATE_FIELDS = ("sale_from", "sale_to")
TEXT_LIMITS = {"description": 20000, "seo_description": 1000}
MAX_BUNDLE_ITEMS = 30
MAX_DISCOUNT_PRODUCTS = 50
VARIANT_FIELDS = ("price", "compare_at_price", "sku")  # Shopify keeps these on the variant

TOOL_HELP = """{"tool": "list_stores"}                                                connected stores
{"tool": "store_info", "store": "name"}                                    store name, currency, product count
{"tool": "list_products", "store": "name", "search": "عود", "limit": 20}  recent products (search optional)
{"tool": "get_product", "store": "name", "id": "..."}                      one product with its variants and description
{"tool": "list_orders", "store": "name", "days": 30, "limit": 20}         recent orders (numbers, totals, items; no customer data)
{"tool": "sales_summary", "store": "name", "days": 30}                     order count, revenue, best sellers
{"tool": "propose_product", "store": "name", "title": "...", "price": "9.500", "description": "...", "sku": "...", "tags": ["..."], "status": "draft"}   propose a new product
{"tool": "propose_update", "store": "name", "id": "...", "price": "8.000", "title": "..."}   propose changes to an existing product (only the fields to change)
{"tool": "list_discounts", "store": "name"}                                current and past offers (discounts / coupons)
{"tool": "propose_discount", "store": "name", "title": "عرض الخريف", "type": "percentage", "value": 20, "code": "AUTUMN20", "products": ["id"], "starts": "2026-10-20", "ends": "2026-10-31", "minimum": "10", "usage_limit": 100, "once_per_customer": true}   propose an offer: "type" percentage or fixed; omit "code" for an automatic Shopify discount; omit "products" for the whole order
{"tool": "propose_end_discount", "store": "name", "id": "..."}              propose ending an offer now
{"tool": "propose_bundle", "store": "name", "title": "باقة ...", "items": [{"id": "...", "quantity": 1}, {"id": "...", "quantity": 2}], "price": "12.000", "description": "..."}   propose selling several products together as one bundle product (draft)
{"done": "your answer for the user"}                                       finish"""
FIELD_NOTES = """Product fields: Shopify: title, description, price, compare_at_price (the old price shown crossed out), sku, status, tags, vendor, product_type, seo_title, seo_description. WooCommerce: title, description, price, sale_price, sale_from and sale_to (dates that schedule the sale price), sku, status, tags.
Offers: on Shopify a discount with "code" needs the code at checkout and one without "code" applies automatically; WooCommerce offers are coupons and always need a code (for an automatic WooCommerce offer, schedule sale_price with sale_from/sale_to on each product instead)."""

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


def local_tz():
    return dt.datetime.now().astimezone().tzinfo


def parse_when(value, end: bool = False) -> dt.datetime:
    """'2026-10-31', '٢٠٢٦-١٠-٣١ 18:00' or an ISO time -> aware datetime in the PC's time zone.
    A bare date means the start of that day, or its last second when `end` is set."""
    text = str(value or "").translate(_ARABIC_DIGITS).strip().replace("/", "-")
    if not text:
        raise StoreError("date is empty")
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
            day = dt.date.fromisoformat("-".join(p.zfill(2) for p in text.split("-")))
            moment = dt.datetime.combine(day, dt.time(23, 59, 59) if end else dt.time(0, 0))
        else:
            moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        raise StoreError(f"invalid date: {value} (use YYYY-MM-DD)") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local_tz())
    return moment


def show_when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if moment.tzinfo is not None:
        moment = moment.astimezone(local_tz())
    if (moment.hour, moment.minute) in ((0, 0), (23, 59)):
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M")


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
        elif field in DATE_FIELDS:
            value = "" if value == "" else parse_when(value, end=field == "sale_to").strftime("%Y-%m-%dT%H:%M:%S")
        elif field == "status":
            value = str(value).strip().lower()
            value = {"publish": "active", "published": "active", "draft": "draft", "active": "active"}.get(value, "")
            if not value:
                raise StoreError('status must be "draft" or "active"')
        elif field == "tags":
            value = _tags(value)
        else:
            value = str(value).strip()
            limit = TEXT_LIMITS.get(field, 255)
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


def field_warnings(before: dict, after: dict) -> list[str]:
    warnings = price_warnings(before, after)
    if len(after.get("seo_title") or "") > 70:
        warnings.append("عنوان SEO أطول من 70 حرفًا، وقد يُقص في نتائج البحث")
    if len(after.get("seo_description") or "") > 160:
        warnings.append("وصف SEO أطول من 160 حرفًا، وقد يُقص في نتائج البحث")
    if after.get("sale_to") and after.get("sale_from") and after["sale_to"] < after["sale_from"]:
        warnings.append("تاريخ نهاية التخفيض قبل بدايته")
    return warnings


def _money(value, currency: str) -> str:
    return f"{value} {currency}".strip()


class StoreToolbox:
    def __init__(self, stores: Stores, default_store: str | None = None):
        self.stores = stores
        self.default_store = default_store
        self.trace: list[dict] = []
        self.proposals: list[dict] = []  # {"kind", "payload"} in the order they were made
        self._clients: dict[str, object] = {}
        self._currency: dict[str, str] = {}

    def help(self) -> str:
        return TOOL_HELP + "\n\n" + FIELD_NOTES

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
                   "warnings": field_warnings({}, fields)}
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
                pl["warnings"] = field_warnings(pl["before"], pl["after"])
                return f'Updated the proposal for "{product["title"]}": ' + ", ".join(f"{k} → {v}" for k, v in pl["after"].items())
        payload = {"store_id": entry["id"], "store": entry["name"], "platform": entry["platform"],
                   "product_id": product["id"], "variant_id": variant_id, "title": product["title"],
                   "before": before, "after": changed, "warnings": field_warnings(before, changed)}
        self._add("store_update_product", payload)
        summary = ", ".join(f"{k}: {before[k]!s:.40} → {v!s:.40}" for k, v in changed.items())
        return f'Proposed update for "{product["title"]}": {summary}. It runs only if the user approves.'

    # ---- offers and bundles ----------------------------------------------------------
    def currency(self, entry: dict, client) -> str:
        if entry["id"] not in self._currency:
            try:
                self._currency[entry["id"]] = client.info().get("currency") or ""
            except ProviderError:
                self._currency[entry["id"]] = ""
        return self._currency[entry["id"]]

    def tool_list_discounts(self, call: dict) -> str:
        entry, client = self._store(call)
        rows = client.discounts(self._limit(call, "limit", 25, 50))
        if not rows:
            return f"No offers found in {entry['name']}."
        lines = [f"{len(rows)} offers in {entry['name']} (id | title | status | code | details | dates | used):"]
        for d in rows:
            method = f"code {d['code']}" if d.get("code") else "automatic"
            dates = f"{show_when(d.get('starts')) or '?'} → {show_when(d.get('ends')) or 'no end'}"
            used = d.get("used")
            lines.append(f"{d['id']} | {d['title']} | {d['status']} | {method} | {d.get('summary') or '-'} | {dates}"
                         f" | {used if used is not None else '-'}")
        return "\n".join(lines)

    def tool_propose_discount(self, call: dict) -> str:
        entry, client = self._store(call)
        platform = entry["platform"]
        kind = str(call.get("type") or call.get("kind") or "percentage").strip().lower()
        kind = {"percent": "percentage", "%": "percentage", "amount": "fixed", "fixed_amount": "fixed"}.get(kind, kind)
        if kind not in ("percentage", "fixed"):
            raise StoreError('"type" must be "percentage" or "fixed"')
        value = parse_price(call.get("value"))
        if Decimal(value) <= 0 or (kind == "percentage" and Decimal(value) > 100):
            raise StoreError("a percentage must be between 1 and 100, and a fixed amount above 0")
        code = str(call.get("code") or "").strip()
        if code and not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", code):
            raise StoreError("code: 3-40 English letters, digits, - or _")
        if platform == "woocommerce" and not code:
            raise StoreError("WooCommerce offers are coupons and need a code. For an automatic offer, use "
                             "propose_update with sale_price, sale_from and sale_to on each product.")
        if code:
            existing = {str(d.get("code") or "").lower() for d in client.discounts(50)}
            if code.lower() in existing:
                raise StoreError(f"the code {code} is already used in this store")
        now = dt.datetime.now(local_tz())
        starts = parse_when(call["starts"]) if call.get("starts") else now
        if starts < now - dt.timedelta(hours=1):
            starts = now
        ends = parse_when(call["ends"], end=True) if call.get("ends") else None
        if ends is not None and (ends <= starts or ends <= now):
            raise StoreError("the end date must be in the future and after the start")
        raw_products = call.get("products") or []
        if isinstance(raw_products, (str, int)):
            raw_products = [raw_products]
        if len(raw_products) > MAX_DISCOUNT_PRODUCTS:
            raise StoreError(f"at most {MAX_DISCOUNT_PRODUCTS} products per offer")
        products = [client.product(str(pid)) for pid in dict.fromkeys(str(p) for p in raw_products)]
        minimum = parse_price(call["minimum"]) if call.get("minimum") not in (None, "", 0, "0") else None
        try:
            usage_limit = int(call["usage_limit"]) if call.get("usage_limit") else None
        except (TypeError, ValueError):
            raise StoreError("usage_limit must be a whole number") from None
        title = str(call.get("title") or "").strip()[:255] or code or f"خصم {value}{'%' if kind == 'percentage' else ''}"
        currency = self.currency(entry, client)
        warnings = []
        if kind == "percentage" and Decimal(value) > 50:
            warnings.append(f"خصم كبير: {value}%")
        if kind == "fixed":
            cheapest = [Decimal(str(p["price"])) for p in products if p.get("price") not in (None, "")]
            if cheapest and Decimal(value) >= min(cheapest):
                warnings.append("قيمة الخصم تساوي سعر أحد المنتجات أو تزيد عليه")
        if platform == "woocommerce" and starts > now + dt.timedelta(hours=1):
            warnings.append("كوبونات WooCommerce لا تدعم تاريخ بداية: سيعمل الكود فور الموافقة")
        spec = {"title": title, "kind": kind, "value": value, "code": code or None,
                "product_ids": [p["id"] for p in products], "starts": starts.isoformat(timespec="seconds"),
                "ends": ends.isoformat(timespec="seconds") if ends else None, "minimum": minimum,
                "usage_limit": usage_limit, "once_per_customer": bool(call.get("once_per_customer"))}
        amount = f"{value}%" if kind == "percentage" else _money(value, currency) + (" على كل قطعة" if products else " من الطلب")
        rows = [{"label": "العرض", "value": title}, {"label": "الخصم", "value": amount},
                {"label": "الطريقة", "value": f"كود: {code}" if code else "تلقائي عند الدفع (بدون كود)"},
                {"label": "يشمل", "value": "، ".join(p["title"] for p in products) if products else "كل الطلب"}]
        if minimum:
            rows.append({"label": "الحد الأدنى للطلب", "value": _money(minimum, currency)})
        rows.append({"label": "يبدأ", "value": "عند الموافقة" if starts <= now + dt.timedelta(minutes=5) else show_when(spec["starts"])})
        rows.append({"label": "ينتهي", "value": show_when(spec["ends"]) if ends else "بدون تاريخ انتهاء"})
        if usage_limit:
            rows.append({"label": "حد الاستخدام", "value": f"{usage_limit} مرة"})
        if spec["once_per_customer"]:
            rows.append({"label": "لكل عميل", "value": "مرة واحدة"})
        self._add("store_create_discount", {"store_id": entry["id"], "store": entry["name"], "platform": platform,
                                            "title": title, "rows": rows, "warnings": warnings, "spec": spec})
        return f'Proposed offer "{title}" ({amount}) in {entry["name"]}. It is created only if the user approves.'

    def tool_propose_end_discount(self, call: dict) -> str:
        entry, client = self._store(call)
        did = str(call.get("id") or "").strip()
        if not did:
            raise StoreError("propose_end_discount needs the offer id (use list_discounts)")
        found = next((d for d in client.discounts(50) if str(d["id"]) == did), None)
        if found is None:
            raise StoreError(f"offer {did} not found; use list_discounts")
        if found["status"] == "expired":
            return f'No change: "{found["title"]}" has already ended.'
        state = {"active": "نشط", "scheduled": "مجدول"}.get(found["status"], found["status"])
        rows = [{"label": "العرض", "value": found["title"]},
                {"label": "التفاصيل", "value": found.get("summary") or "-"},
                {"label": "الحالة الآن", "value": state}, {"label": "بعد الموافقة", "value": "يتوقف فورًا"}]
        self._add("store_end_discount", {"store_id": entry["id"], "store": entry["name"], "platform": entry["platform"],
                                         "title": found["title"], "rows": rows, "warnings": [],
                                         "spec": {"id": found["id"]}})
        return f'Proposed ending the offer "{found["title"]}". It ends only if the user approves.'

    def tool_propose_bundle(self, call: dict) -> str:
        entry, client = self._store(call)
        raw = call.get("items") or []
        if not isinstance(raw, list) or not raw:
            raise StoreError('propose_bundle needs "items": [{"id": "...", "quantity": 1}, ...]')
        if len(raw) > MAX_BUNDLE_ITEMS:
            raise StoreError(f"a bundle can hold at most {MAX_BUNDLE_ITEMS} products")
        items, seen = [], set()
        for it in raw:
            pid = str((it or {}).get("id") or (it or {}).get("product_id") or "").strip()
            try:
                qty = int((it or {}).get("quantity") or 1)
            except (TypeError, ValueError):
                raise StoreError("quantity must be a whole number") from None
            if not pid or not 1 <= qty <= 20:
                raise StoreError("each item needs an id and a quantity between 1 and 20")
            if pid in seen:
                raise StoreError("each product can appear once; raise its quantity instead")
            seen.add(pid)
            product = client.product(pid)
            items.append({"product_id": product["id"], "title": product["title"], "quantity": qty,
                          "price": product.get("price"), "variants": len(product.get("variants") or [])})
        if sum(i["quantity"] for i in items) < 2:
            raise StoreError("a bundle needs at least two units")
        price = parse_price(call.get("price"))
        if Decimal(price) <= 0:
            raise StoreError("the bundle needs a price above 0")
        title = str(call.get("title") or "").strip()[:255]
        if not title:
            raise StoreError("the bundle needs a title")
        check = client.bundle_check([{"product_id": i["product_id"], "quantity": i["quantity"]} for i in items])
        currency = self.currency(entry, client)
        priced = [i for i in items if i["price"] not in (None, "")]
        separate = sum((Decimal(str(i["price"])) * i["quantity"] for i in priced), Decimal(0))
        warnings = []
        rows = [{"label": "الباقة", "value": title},
                {"label": "المكوّنات", "value": "، ".join(f"{i['title']} × {i['quantity']}" for i in items)},
                {"label": "سعر الباقة", "value": _money(price, currency)}]
        if len(priced) == len(items) and separate > 0:
            saving = int((separate - Decimal(price)) / separate * 100)
            rows.append({"label": "لو بيعت منفصلة", "value": _money(separate, currency)
                         + (f" (توفير {saving}%)" if saving > 0 else "")})
            if Decimal(price) >= separate:
                warnings.append("سعر الباقة لا يقل عن مجموع منتجاتها منفصلة")
            elif saving > 50:
                warnings.append(f"توفير كبير: {saving}%")
        if any(i["variants"] > 1 for i in items):
            warnings.append("بعض المنتجات لها خيارات بأسعار مختلفة؛ المجموع محسوب بسعر أول خيار")
        status = "active" if str(call.get("status") or "").lower() in ("active", "publish") else "draft"
        rows.append({"label": "الحالة", "value": "منشور" if status == "active" else "مسودة"})
        rows.append({"label": "المخزون", "value": check["notes"][0]})
        rows += [{"label": "ملاحظة", "value": n} for n in check["notes"][1:]]
        spec = {"title": title, "items": [{k: i[k] for k in ("product_id", "title", "quantity")} for i in items],
                "price": price, "description": str(call.get("description") or "")[:20000],
                "sku": str(call.get("sku") or "")[:255], "status": status, "native": check["native"]}
        self._add("store_create_bundle", {"store_id": entry["id"], "store": entry["name"], "platform": entry["platform"],
                                          "title": title, "rows": rows, "warnings": warnings, "spec": spec})
        return f'Proposed bundle "{title}" ({len(items)} products, {price}). It is created only if the user approves.'

    def action_list(self) -> list[dict]:
        return list(self.proposals)

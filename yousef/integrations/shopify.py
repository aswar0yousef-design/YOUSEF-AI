"""Shopify Admin GraphQL API (custom app access token).

Needed scopes: read_products, write_products, read_orders.
Orders are read without any customer fields (no names, emails, phones or
addresses), so personal data never reaches a model.
"""

from __future__ import annotations

import re
import time
import urllib.parse

from ..models.base import ProviderError, http_json

API_VERSION = "2026-10"  # latest stable on 2026-10-19; each version is supported for about a year
PAGE = 50

_PRODUCT_FIELDS = """id title handle status vendor productType tags totalInventory updatedAt
variants(first: 10) { nodes { id title price compareAtPrice sku inventoryQuantity } }"""


class StoreError(ProviderError):
    pass


def auth_hint(message: str, platform: str) -> str:
    if "HTTP 401" in message or "HTTP 403" in message:
        return f"رُفض الدخول ({message[:60]}). تأكد أن مفتاح {platform} صحيح وله صلاحيات المنتجات والطلبات."
    if "HTTP 404" in message:
        return f"العنوان غير موجود ({message[:80]}). تأكد من رابط المتجر."
    return message


def shop_domain(url: str) -> str:
    """'my-shop', 'my-shop.myshopify.com' or an admin URL -> 'my-shop.myshopify.com'."""
    text = (url or "").strip().lower()
    m = re.search(r"admin\.shopify\.com/store/([a-z0-9][a-z0-9-]*)", text)
    if m:
        return f"{m.group(1)}.myshopify.com"
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if not text:
        raise StoreError("اكتب رابط المتجر، مثل my-shop.myshopify.com")
    if "." not in text:
        text += ".myshopify.com"
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", text):
        raise StoreError(f"رابط متجر غير صالح: {url}")
    return text


def _local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return url.startswith("http://") and host in ("127.0.0.1", "localhost")


def _gid_number(gid: str) -> str:
    return str(gid or "").rsplit("/", 1)[-1]


def _money(m: dict | None) -> tuple[float, str]:
    shop = (m or {}).get("shopMoney") or {}
    try:
        return float(shop.get("amount") or 0), shop.get("currencyCode") or ""
    except ValueError:
        return 0.0, shop.get("currencyCode") or ""


class Shopify:
    platform = "shopify"

    def __init__(self, url: str, token: str, api_version: str = API_VERSION):
        self.token = token
        self.api_version = api_version or API_VERSION
        if _local(url):  # tests and local mocks
            self.domain = urllib.parse.urlparse(url).netloc
            self.endpoint = f"{url.rstrip('/')}/admin/api/{self.api_version}/graphql.json"
        else:
            self.domain = shop_domain(url)
            self.endpoint = f"https://{self.domain}/admin/api/{self.api_version}/graphql.json"

    def gql(self, query: str, variables: dict | None = None) -> dict:
        try:
            data = http_json("POST", self.endpoint, headers={"X-Shopify-Access-Token": self.token},
                             body={"query": query, "variables": variables or {}}, timeout=30) or {}
        except ProviderError as e:
            raise StoreError(f"Shopify: {auth_hint(str(e), 'Shopify')}") from None
        if data.get("errors"):
            first = data["errors"][0]
            message = first.get("message") if isinstance(first, dict) else str(first)
            raise StoreError(f"Shopify: {message}")
        return data.get("data") or {}

    @staticmethod
    def _user_errors(payload: dict) -> None:
        errors = (payload or {}).get("userErrors") or []
        if errors:
            raise StoreError("Shopify: " + "; ".join(f"{'.'.join(e.get('field') or [])} {e.get('message')}".strip()
                                                     for e in errors))

    def admin_url(self, product_id: str) -> str:
        handle = self.domain.split(".")[0]
        return f"https://admin.shopify.com/store/{handle}/products/{_gid_number(product_id)}"

    # ---- reading ---------------------------------------------------------------
    def info(self) -> dict:
        data = self.gql("{ shop { name currencyCode myshopifyDomain } productsCount { count } }")
        shop = data.get("shop") or {}
        return {"name": shop.get("name", ""), "currency": shop.get("currencyCode", ""),
                "domain": shop.get("myshopifyDomain", self.domain),
                "products": ((data.get("productsCount") or {}).get("count"))}

    @staticmethod
    def _product(node: dict) -> dict:
        variants = [{"id": v.get("id"), "title": v.get("title"), "price": v.get("price"),
                     "compare_at_price": v.get("compareAtPrice"), "sku": v.get("sku") or "",
                     "inventory": v.get("inventoryQuantity")}
                    for v in ((node.get("variants") or {}).get("nodes") or [])]
        first = variants[0] if variants else {}
        return {
            "id": node.get("id"), "title": node.get("title"), "status": (node.get("status") or "").lower(),
            "vendor": node.get("vendor") or "", "product_type": node.get("productType") or "",
            "tags": node.get("tags") or [], "inventory": node.get("totalInventory"),
            "price": first.get("price"), "sku": first.get("sku", ""), "variants": variants,
            "description": node.get("descriptionHtml"),
        }

    def products(self, search: str | None = None, limit: int = 20) -> list[dict]:
        query = f"""query($n: Int!, $q: String) {{
  products(first: $n, query: $q, sortKey: UPDATED_AT, reverse: true) {{ nodes {{ {_PRODUCT_FIELDS} }} }}
}}"""
        data = self.gql(query, {"n": max(1, min(limit, PAGE)), "q": search or None})
        return [self._product(n) for n in ((data.get("products") or {}).get("nodes") or [])]

    def product(self, product_id: str) -> dict:
        data = self.gql(f"query($id: ID!) {{ product(id: $id) {{ {_PRODUCT_FIELDS} descriptionHtml }} }}",
                        {"id": product_id})
        node = data.get("product")
        if not node:
            raise StoreError(f"لم يُعثر على المنتج {product_id}")
        return self._product(node)

    def orders(self, days: int = 30, limit: int = 100) -> list[dict]:
        since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        query = """query($n: Int!, $q: String) {
  orders(first: $n, query: $q, sortKey: CREATED_AT, reverse: true) { nodes {
    name createdAt displayFinancialStatus displayFulfillmentStatus
    totalPriceSet { shopMoney { amount currencyCode } }
    lineItems(first: 20) { nodes { title quantity } }
  } }
}"""
        data = self.gql(query, {"n": max(1, min(limit, 250)), "q": f"created_at:>={since}"})
        out = []
        for o in (data.get("orders") or {}).get("nodes") or []:
            total, currency = _money(o.get("totalPriceSet"))
            out.append({
                "number": o.get("name"), "created": o.get("createdAt"), "total": total, "currency": currency,
                "paid": (o.get("displayFinancialStatus") or "").lower(),
                "fulfillment": (o.get("displayFulfillmentStatus") or "").lower(),
                "items": [{"title": li.get("title"), "quantity": li.get("quantity") or 0}
                          for li in ((o.get("lineItems") or {}).get("nodes") or [])],
            })
        return out

    # ---- writing (only from approved actions) ---------------------------------------
    def _set_variant(self, product_id: str, variant_id: str, fields: dict) -> None:
        variant: dict = {"id": variant_id}
        if fields.get("price") is not None:
            variant["price"] = str(fields["price"])
        if fields.get("compare_at_price") is not None:
            variant["compareAtPrice"] = str(fields["compare_at_price"]) if fields["compare_at_price"] != "" else None
        if fields.get("sku") is not None:
            variant["inventoryItem"] = {"sku": fields["sku"]}
        if len(variant) == 1:
            return
        data = self.gql("""mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price } userErrors { field message } } }""", {"productId": product_id, "variants": [variant]})
        self._user_errors(data.get("productVariantsBulkUpdate"))

    @staticmethod
    def _product_input(fields: dict) -> dict:
        out = {}
        for ours, theirs in (("title", "title"), ("description", "descriptionHtml"), ("vendor", "vendor"),
                             ("product_type", "productType"), ("tags", "tags")):
            if fields.get(ours) is not None:
                out[theirs] = fields[ours]
        if fields.get("status"):
            out["status"] = "ACTIVE" if fields["status"] == "active" else "DRAFT"
        return out

    def create_product(self, fields: dict) -> dict:
        product = self._product_input(fields)
        product.setdefault("status", "DRAFT")
        data = self.gql("""mutation($product: ProductCreateInput!) {
  productCreate(product: $product) {
    product { id title status variants(first: 1) { nodes { id } } } userErrors { field message } } }""",
                        {"product": product})
        payload = data.get("productCreate") or {}
        self._user_errors(payload)
        created = payload.get("product") or {}
        variants = ((created.get("variants") or {}).get("nodes") or [])
        if variants:
            self._set_variant(created["id"], variants[0]["id"], fields)
        return {"id": created.get("id"), "url": self.admin_url(created.get("id", ""))}

    def update_product(self, product_id: str, fields: dict, variant_id: str | None = None) -> dict:
        product = self._product_input(fields)
        if product:
            product["id"] = product_id
            data = self.gql("""mutation($product: ProductUpdateInput!) {
  productUpdate(product: $product) { product { id } userErrors { field message } } }""", {"product": product})
            self._user_errors(data.get("productUpdate"))
        if any(fields.get(k) is not None for k in ("price", "compare_at_price", "sku")):
            if not variant_id:
                variant_id = (self.product(product_id).get("variants") or [{}])[0].get("id")
            if not variant_id:
                raise StoreError("لا يوجد متغير (variant) لتحديث السعر")
            self._set_variant(product_id, variant_id, fields)
        return {"id": product_id, "url": self.admin_url(product_id)}

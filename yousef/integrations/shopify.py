"""Shopify Admin GraphQL API (custom app access token).

Needed scopes: read_products, write_products, read_orders; for offers also
read_discounts and write_discounts.
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
featuredMedia { id preview { image { url } } } seo { title description }
variants(first: 10) { nodes { id title price compareAtPrice sku inventoryQuantity } }"""
_DISCOUNT_FIELDS = "title status startsAt endsAt summary"
_DISCOUNT_TYPES = ("DiscountCodeBasic", "DiscountAutomaticBasic", "DiscountCodeBxgy", "DiscountAutomaticBxgy",
                   "DiscountCodeFreeShipping", "DiscountAutomaticFreeShipping")
BUNDLE_POLL_SECONDS = 30


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

    def admin_url(self, product_id: str, section: str = "products") -> str:
        handle = self.domain.split(".")[0]
        return f"https://admin.shopify.com/store/{handle}/{section}/{_gid_number(product_id)}"

    # ---- reading ---------------------------------------------------------------
    def info(self) -> dict:
        data = self.gql("{ shop { name currencyCode myshopifyDomain primaryDomain { url } } productsCount { count } }")
        shop = data.get("shop") or {}
        return {"name": shop.get("name", ""), "currency": shop.get("currencyCode", ""),
                "domain": shop.get("myshopifyDomain", self.domain),
                "site": (shop.get("primaryDomain") or {}).get("url") or f"https://{self.domain}",
                "products": ((data.get("productsCount") or {}).get("count"))}

    @staticmethod
    def _product(node: dict) -> dict:
        variants = [{"id": v.get("id"), "title": v.get("title"), "price": v.get("price"),
                     "compare_at_price": v.get("compareAtPrice"), "sku": v.get("sku") or "",
                     "inventory": v.get("inventoryQuantity")}
                    for v in ((node.get("variants") or {}).get("nodes") or [])]
        first = variants[0] if variants else {}
        seo = node.get("seo") or {}
        featured = node.get("featuredMedia") or {}
        urls = [((featured.get("preview") or {}).get("image") or {}).get("url")]
        urls += [((m.get("preview") or {}).get("image") or {}).get("url") for m in (node.get("media") or {}).get("nodes") or []]
        return {
            "id": node.get("id"), "title": node.get("title"), "status": (node.get("status") or "").lower(),
            "vendor": node.get("vendor") or "", "product_type": node.get("productType") or "",
            "tags": node.get("tags") or [], "inventory": node.get("totalInventory"),
            "price": first.get("price"), "sku": first.get("sku", ""), "variants": variants,
            "description": node.get("descriptionHtml"), "images": 1 if node.get("featuredMedia") else 0,
            "seo_title": seo.get("title") or "", "seo_description": seo.get("description") or "",
            "image_urls": list(dict.fromkeys(u for u in urls if u)),
        }

    def all_products(self, limit: int = 250) -> list[dict]:
        """Up to `limit` products, newest first, page by page (for audits)."""
        out: list[dict] = []
        cursor = None
        while len(out) < limit:
            query = f"""query($n: Int!, $after: String) {{
  products(first: $n, after: $after, sortKey: UPDATED_AT, reverse: true) {{
    nodes {{ {_PRODUCT_FIELDS} }} pageInfo {{ hasNextPage endCursor }} }}
}}"""
            data = self.gql(query, {"n": min(PAGE, limit - len(out)), "after": cursor})
            conn = data.get("products") or {}
            out.extend(self._product(n) for n in conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage") or not info.get("endCursor"):
                break
            cursor = info["endCursor"]
        return out

    def products(self, search: str | None = None, limit: int = 20) -> list[dict]:
        query = f"""query($n: Int!, $q: String) {{
  products(first: $n, query: $q, sortKey: UPDATED_AT, reverse: true) {{ nodes {{ {_PRODUCT_FIELDS} }} }}
}}"""
        data = self.gql(query, {"n": max(1, min(limit, PAGE)), "q": search or None})
        return [self._product(n) for n in ((data.get("products") or {}).get("nodes") or [])]

    def product(self, product_id: str) -> dict:
        data = self.gql(f"query($id: ID!) {{ product(id: $id) {{ {_PRODUCT_FIELDS} descriptionHtml "
                        f"media(first: 6) {{ nodes {{ preview {{ image {{ url }} }} }} }} }} }}", {"id": product_id})
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
    def _bulk_variants(self, product_id: str, variants: list[dict]) -> None:
        if not variants:
            return
        data = self.gql("""mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price } userErrors { field message } } }""", {"productId": product_id, "variants": variants})
        self._user_errors(data.get("productVariantsBulkUpdate"))

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
        if fields.get("seo_title") is not None or fields.get("seo_description") is not None:
            out["seo"] = {k: fields[f"seo_{k}"] for k in ("title", "description") if fields.get(f"seo_{k}") is not None}
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
        if (fields.get("seo_title") is None) != (fields.get("seo_description") is None):
            # send both SEO values so the one not being changed is kept as it is
            current = self.product(product_id)
            fields = dict(fields)
            for key in ("seo_title", "seo_description"):
                if fields.get(key) is None:
                    fields[key] = current.get(key) or ""
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

    # ---- offers ---------------------------------------------------------------------
    def discounts(self, limit: int = 25) -> list[dict]:
        fragments = " ".join(f"... on {t} {{ {_DISCOUNT_FIELDS} }}" for t in _DISCOUNT_TYPES)
        query = f"""query($n: Int!) {{ discountNodes(first: $n, reverse: true) {{ nodes {{ id discount {{
  __typename {fragments} ... on DiscountCodeBasic {{ codes(first: 1) {{ nodes {{ code }} }} }} }} }} }} }}"""
        data = self.gql(query, {"n": max(1, min(limit, 50))})
        out = []
        for node in (data.get("discountNodes") or {}).get("nodes") or []:
            d = node.get("discount") or {}
            codes = ((d.get("codes") or {}).get("nodes") or [])
            out.append({
                "id": node.get("id"), "title": d.get("title") or d.get("__typename", ""),
                "status": (d.get("status") or "").lower(), "starts": d.get("startsAt"), "ends": d.get("endsAt"),
                "summary": d.get("summary") or "", "code": codes[0].get("code") if codes else None,
                "method": "code" if "Code" in (d.get("__typename") or "") else "automatic",
            })
        return out

    def create_discount(self, spec: dict) -> dict:
        if spec["kind"] == "percentage":
            value = {"percentage": float(spec["value"]) / 100}
        else:
            value = {"discountAmount": {"amount": str(spec["value"]), "appliesOnEachItem": bool(spec["product_ids"])}}
        items = {"products": {"productsToAdd": spec["product_ids"]}} if spec["product_ids"] else {"all": True}
        discount = {"title": spec["title"], "startsAt": spec["starts"], "endsAt": spec.get("ends"),
                    "context": {"all": "ALL"}, "customerGets": {"value": value, "items": items}}
        if spec.get("minimum"):
            discount["minimumRequirement"] = {"subtotal": {"greaterThanOrEqualToSubtotal": str(spec["minimum"])}}
        if spec.get("code"):
            discount.update({"code": spec["code"], "usageLimit": spec.get("usage_limit"),
                             "appliesOncePerCustomer": bool(spec.get("once_per_customer"))})
            data = self.gql("""mutation($d: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $d) { codeDiscountNode { id } userErrors { field message } } }""",
                            {"d": discount})
            payload = data.get("discountCodeBasicCreate") or {}
            node = payload.get("codeDiscountNode") or {}
        else:
            data = self.gql("""mutation($d: DiscountAutomaticBasicInput!) {
  discountAutomaticBasicCreate(automaticBasicDiscount: $d) { automaticDiscountNode { id } userErrors { field message } } }""",
                            {"d": discount})
            payload = data.get("discountAutomaticBasicCreate") or {}
            node = payload.get("automaticDiscountNode") or {}
        self._user_errors(payload)
        return {"id": node.get("id"), "url": self.admin_url(node.get("id", ""), "discounts")}

    def end_discount(self, discount_id: str) -> dict:
        if "DiscountCodeNode" in discount_id:
            name, node = "discountCodeDeactivate", "codeDiscountNode"
        else:
            name, node = "discountAutomaticDeactivate", "automaticDiscountNode"
        data = self.gql(f"""mutation($id: ID!) {{ {name}(id: $id) {{ {node} {{ id }} userErrors {{ field message }} }} }}""",
                        {"id": discount_id})
        self._user_errors(data.get(name))
        return {"id": discount_id, "url": self.admin_url(discount_id, "discounts")}

    # ---- bundles --------------------------------------------------------------------
    def product_options(self, product_id: str) -> dict:
        data = self.gql("""query($id: ID!) { product(id: $id) { id title options { id name optionValues { name } } } }""",
                        {"id": product_id})
        node = data.get("product")
        if not node:
            raise StoreError(f"لم يُعثر على المنتج {product_id}")
        options = [{"id": o.get("id"), "name": o.get("name"), "values": [v.get("name") for v in o.get("optionValues") or []]}
                   for o in node.get("options") or []]
        return {"title": node.get("title"), "options": options}

    @staticmethod
    def _choices(options: list[dict]) -> list[dict]:
        """Options the buyer actually chooses (a lone "Default Title" is not a choice)."""
        return [o for o in options if len(o["values"]) > 1]

    def bundle_check(self, items: list[dict]) -> dict:
        choices = []
        for item in items:
            info = self.product_options(item["product_id"])
            choices += [f"{o['name']} ({info['title']})" for o in self._choices(info["options"])]
        if len(choices) > 3:
            raise StoreError(f"باقات Shopify تسمح بثلاثة خيارات على الأكثر، وهذه المنتجات فيها {len(choices)}: "
                             + "، ".join(choices))
        notes = ["باقة Shopify أصلية: مخزونها يُحسب من مخزون منتجاتها"]
        if choices:
            notes.append("يختار المشتري: " + "، ".join(choices))
        return {"native": True, "notes": notes}

    def create_bundle(self, spec: dict, sleep=time.sleep) -> dict:
        components, names = [], set()
        for item in spec["items"]:
            info = self.product_options(item["product_id"])
            selections = []
            for o in info["options"]:
                name = o["name"]
                if name.lower() in names:  # option names must be unique across the bundle
                    name = f"{o['name']} - {info['title']}"
                names.add(name.lower())
                selections.append({"componentOptionId": o["id"], "name": name, "values": o["values"]})
            components.append({"productId": item["product_id"], "quantity": int(item["quantity"]),
                               "optionSelections": selections})
        data = self.gql("""mutation($input: ProductBundleCreateInput!) { productBundleCreate(input: $input) {
  productBundleOperation { id status } userErrors { field message } } }""",
                        {"input": {"title": spec["title"], "components": components}})
        payload = data.get("productBundleCreate") or {}
        self._user_errors(payload)
        op_id = (payload.get("productBundleOperation") or {}).get("id")
        if not op_id:
            raise StoreError("Shopify لم يُرجع عملية إنشاء للباقة")
        product_id = None
        for _ in range(BUNDLE_POLL_SECONDS):
            op = (self.gql("""query($id: ID!) { productOperation(id: $id) { ... on ProductBundleOperation {
  id status product { id } userErrors { field message } } } }""", {"id": op_id}).get("productOperation") or {})
            if op.get("userErrors"):
                self._user_errors(op)
            if op.get("status") == "COMPLETE":
                product_id = (op.get("product") or {}).get("id")
                break
            sleep(1)
        if not product_id:
            raise StoreError("لم ينتهِ Shopify من إنشاء الباقة بعد؛ ستظهر في المنتجات خلال دقائق، ثم حدّد سعرها")
        variants = self.product(product_id).get("variants") or []
        self._bulk_variants(product_id, [{"id": v["id"], "price": str(spec["price"])} for v in variants])
        details = {"id": product_id, "status": "ACTIVE" if spec.get("status") == "active" else "DRAFT"}
        if spec.get("description"):
            details["descriptionHtml"] = spec["description"]
        data = self.gql("""mutation($product: ProductUpdateInput!) {
  productUpdate(product: $product) { product { id } userErrors { field message } } }""", {"product": details})
        self._user_errors(data.get("productUpdate"))
        return {"id": product_id, "url": self.admin_url(product_id)}

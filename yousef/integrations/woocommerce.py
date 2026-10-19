"""WooCommerce REST API v3 (consumer key + secret with Read/Write permission).

The store must use HTTPS and pretty permalinks. Orders are read without the
billing/shipping blocks, so customer names, emails, phones and addresses are
never fetched into YOUSEF AI.
"""

from __future__ import annotations

import base64
import time
import urllib.parse

from ..models.base import ProviderError, http_json
from .shopify import StoreError, auth_hint

ORDER_FIELDS = "id,number,status,total,currency,date_created,line_items"


def site_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if not text:
        raise StoreError("اكتب رابط الموقع، مثل https://my-shop.com")
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urllib.parse.urlparse(text)
    local = parsed.hostname in ("127.0.0.1", "localhost")
    if parsed.scheme != "https" and not local:
        raise StoreError("يجب أن يعمل المتجر بـ https حتى تُرسل مفاتيح WooCommerce بأمان")
    path = parsed.path.split("/wp-json")[0].split("/wp-admin")[0].rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class WooCommerce:
    platform = "woocommerce"

    def __init__(self, url: str, secret: str):
        self.base = site_url(url)
        if ":" not in (secret or ""):
            raise StoreError("مفاتيح WooCommerce يجب أن تكون consumer key وconsumer secret")
        key, sec = secret.split(":", 1)
        self._auth = "Basic " + base64.b64encode(f"{key}:{sec}".encode()).decode()

    def _call(self, method: str, path: str, params: dict | None = None, body=None):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        try:
            return http_json(method, f"{self.base}/wp-json/wc/v3{path}{query}",
                             headers={"Authorization": self._auth, "User-Agent": "YOUSEF-AI"}, body=body, timeout=30)
        except ProviderError as e:
            raise StoreError(f"WooCommerce: {auth_hint(str(e), 'WooCommerce')}") from None

    def admin_url(self, product_id) -> str:
        return f"{self.base}/wp-admin/post.php?post={product_id}&action=edit"

    # ---- reading ---------------------------------------------------------------
    def info(self) -> dict:
        currency = self._call("GET", "/data/currencies/current") or {}
        self._call("GET", "/products", {"per_page": 1})  # proves the keys can read products
        return {"name": urllib.parse.urlparse(self.base).netloc, "currency": currency.get("code", ""),
                "domain": self.base, "products": None}

    @staticmethod
    def _product(p: dict) -> dict:
        return {
            "id": p.get("id"), "title": p.get("name"),
            "status": "active" if p.get("status") == "publish" else (p.get("status") or ""),
            "price": p.get("regular_price") or p.get("price"), "sale_price": p.get("sale_price") or "",
            "sku": p.get("sku") or "", "inventory": p.get("stock_quantity"), "stock_status": p.get("stock_status"),
            "categories": [c.get("name") for c in p.get("categories") or []],
            "tags": [t.get("name") for t in p.get("tags") or []],
            "description": p.get("description"),
        }

    def products(self, search: str | None = None, limit: int = 20) -> list[dict]:
        params = {"per_page": max(1, min(limit, 100)), "orderby": "date", "order": "desc"}
        if search:
            params["search"] = search
        return [self._product(p) for p in self._call("GET", "/products", params) or []]

    def product(self, product_id) -> dict:
        return self._product(self._call("GET", f"/products/{int(product_id)}") or {})

    def orders(self, days: int = 30, limit: int = 100) -> list[dict]:
        after = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days * 86400))
        rows = self._call("GET", "/orders", {"after": after, "per_page": max(1, min(limit, 100)),
                                             "_fields": ORDER_FIELDS}) or []
        out = []
        for o in rows:
            try:
                total = float(o.get("total") or 0)
            except ValueError:
                total = 0.0
            out.append({
                "number": str(o.get("number") or o.get("id")), "created": o.get("date_created"), "total": total,
                "currency": o.get("currency", ""), "paid": o.get("status", ""), "fulfillment": o.get("status", ""),
                "items": [{"title": li.get("name"), "quantity": li.get("quantity") or 0} for li in o.get("line_items") or []],
            })
        return out

    # ---- writing (only from approved actions) ---------------------------------------
    @staticmethod
    def _body(fields: dict) -> dict:
        body = {}
        if fields.get("title") is not None:
            body["name"] = fields["title"]
        if fields.get("description") is not None:
            body["description"] = fields["description"]
        if fields.get("price") is not None:
            body["regular_price"] = str(fields["price"])
        if fields.get("sale_price") is not None:
            body["sale_price"] = str(fields["sale_price"])
        if fields.get("sku") is not None:
            body["sku"] = fields["sku"]
        if fields.get("status"):
            body["status"] = "publish" if fields["status"] == "active" else "draft"
        if fields.get("tags") is not None:
            body["tags"] = [{"name": t} for t in fields["tags"]]
        return body

    def create_product(self, fields: dict) -> dict:
        body = {"type": "simple", "status": "draft", **self._body(fields)}
        created = self._call("POST", "/products", body=body) or {}
        return {"id": created.get("id"), "url": self.admin_url(created.get("id"))}

    def update_product(self, product_id, fields: dict, variant_id=None) -> dict:
        self._call("PUT", f"/products/{int(product_id)}", body=self._body(fields))
        return {"id": product_id, "url": self.admin_url(product_id)}

"""Fake Shopify (GraphQL) and WooCommerce (REST) servers for tests."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _Server:
    def __init__(self, handler):
        self.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.owner = self
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _send(h, status, payload):
    data = json.dumps(payload).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


class FakeShopify(_Server):
    TOKEN = "shpat_test_token_1234"

    def __init__(self):
        self.products = {
            "gid://shopify/Product/1": {"id": "gid://shopify/Product/1", "title": "بخور العود الملكي", "handle": "oud",
                                        "status": "ACTIVE", "vendor": "بيت البخور", "productType": "بخور",
                                        "tags": ["عود"], "totalInventory": 12, "descriptionHtml": "<p>بخور فاخر</p>",
                                        "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/11", "title": "Default",
                                                                "price": "8.500", "compareAtPrice": None, "sku": "OUD-1",
                                                                "inventoryQuantity": 12}]}},
        }
        self.next_id = 2
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                q, v = body["query"], body.get("variables") or {}
                outer.requests.append({"path": self.path, "query": q, "variables": v,
                                       "token": self.headers.get("X-Shopify-Access-Token")})
                if self.headers.get("X-Shopify-Access-Token") != outer.TOKEN:
                    return _send(self, 401, {"errors": "[API] Invalid API key or access token"})
                if "/admin/api/" not in self.path:
                    return _send(self, 404, {"errors": "not found"})
                _send(self, 200, {"data": outer.answer(q, v)})

        super().__init__(H)

    def answer(self, q, v):
        if "productVariantsBulkUpdate" in q:
            var = v["variants"][0]
            for p in self.products.values():
                for node in p["variants"]["nodes"]:
                    if node["id"] == var["id"]:
                        node["price"] = var.get("price", node["price"])
                        node["sku"] = (var.get("inventoryItem") or {}).get("sku", node["sku"])
            return {"productVariantsBulkUpdate": {"productVariants": [{"id": var["id"], "price": var.get("price")}],
                                                  "userErrors": []}}
        if "productCreate" in q:
            pid = f"gid://shopify/Product/{self.next_id}"
            vid = f"gid://shopify/ProductVariant/{self.next_id}1"
            self.next_id += 1
            data = v["product"]
            self.products[pid] = {"id": pid, "title": data["title"], "handle": "new", "status": data.get("status", "ACTIVE"),
                                  "vendor": data.get("vendor", ""), "productType": data.get("productType", ""),
                                  "tags": data.get("tags", []), "totalInventory": 0,
                                  "descriptionHtml": data.get("descriptionHtml", ""),
                                  "variants": {"nodes": [{"id": vid, "title": "Default", "price": "0.00",
                                                          "compareAtPrice": None, "sku": "", "inventoryQuantity": 0}]}}
            return {"productCreate": {"product": {"id": pid, "title": data["title"], "status": data.get("status"),
                                                  "variants": {"nodes": [{"id": vid}]}}, "userErrors": []}}
        if "productUpdate" in q:
            data = v["product"]
            if data.get("title") == "BAD":
                return {"productUpdate": {"product": None, "userErrors": [{"field": ["title"], "message": "is invalid"}]}}
            p = self.products[data["id"]]
            for ours, theirs in (("title", "title"), ("descriptionHtml", "descriptionHtml"), ("status", "status"),
                                 ("tags", "tags")):
                if ours in data:
                    p[theirs] = data[ours]
            return {"productUpdate": {"product": {"id": data["id"]}, "userErrors": []}}
        if "shop {" in q:
            return {"shop": {"name": "متجر العطور", "currencyCode": "OMR", "myshopifyDomain": "oman-oud.myshopify.com"},
                    "productsCount": {"count": len(self.products)}}
        if "product(id" in q:
            return {"product": self.products.get(v["id"])}
        if "products(" in q:
            return {"products": {"nodes": list(self.products.values())}}
        if "orders(" in q:
            return {"orders": {"nodes": [
                {"name": "#1001", "createdAt": "2026-10-10T10:00:00Z", "displayFinancialStatus": "PAID",
                 "displayFulfillmentStatus": "FULFILLED", "totalPriceSet": {"shopMoney": {"amount": "17.000", "currencyCode": "OMR"}},
                 "lineItems": {"nodes": [{"title": "بخور العود الملكي", "quantity": 2}]}},
                {"name": "#1002", "createdAt": "2026-10-12T10:00:00Z", "displayFinancialStatus": "PENDING",
                 "displayFulfillmentStatus": "UNFULFILLED", "totalPriceSet": {"shopMoney": {"amount": "8.500", "currencyCode": "OMR"}},
                 "lineItems": {"nodes": [{"title": "بخور العود الملكي", "quantity": 1}, {"title": "مبخرة", "quantity": 1}]}},
            ]}}
        return {}


class FakeWoo(_Server):
    KEY, SECRET = "ck_test123456", "cs_test654321"

    def __init__(self):
        self.products = {7: {"id": 7, "name": "لبان حوجري", "status": "publish", "regular_price": "5.000", "price": "5.000",
                             "sale_price": "", "sku": "LUBAN", "stock_quantity": 30, "stock_status": "instock",
                             "categories": [{"name": "لبان"}], "tags": [], "description": "لبان عماني"}}
        self.next_id = 8
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                parsed = urlparse(self.path)
                outer.requests.append({"method": self.command, "path": parsed.path, "query": parse_qs(parsed.query),
                                       "body": body, "auth": self.headers.get("Authorization")})
                expected = "Basic " + base64.b64encode(f"{outer.KEY}:{outer.SECRET}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    return _send(self, 401, {"code": "woocommerce_rest_cannot_view", "message": "Sorry, you cannot list resources."})
                status, payload = outer.answer(self.command, parsed.path, body)
                _send(self, status, payload)

            do_GET = do_POST = do_PUT = _go

        super().__init__(H)

    def answer(self, method, path, body):
        base = "/wp-json/wc/v3"
        if path == base + "/data/currencies/current":
            return 200, {"code": "OMR", "name": "Omani rial", "symbol": "ر.ع."}
        if path == base + "/products" and method == "GET":
            return 200, list(self.products.values())
        if path == base + "/products" and method == "POST":
            pid = self.next_id
            self.next_id += 1
            self.products[pid] = {"id": pid, "name": body["name"], "status": body.get("status"),
                                  "regular_price": body.get("regular_price", ""), "sku": body.get("sku", ""),
                                  "categories": [], "tags": body.get("tags", []), "description": body.get("description", "")}
            return 201, self.products[pid]
        if path.startswith(base + "/products/"):
            pid = int(path.rsplit("/", 1)[1])
            if pid not in self.products:
                return 404, {"code": "woocommerce_rest_product_invalid_id", "message": "Invalid ID."}
            if method == "PUT":
                p = self.products[pid]
                for k, val in body.items():
                    p[k] = val
                return 200, p
            return 200, self.products[pid]
        if path == base + "/orders":
            return 200, [{"id": 55, "number": "55", "status": "completed", "total": "10.000", "currency": "OMR",
                          "date_created": "2026-10-11T09:00:00",
                          "line_items": [{"name": "لبان حوجري", "quantity": 2}]}]
        return 404, {"code": "rest_no_route", "message": "No route was found."}

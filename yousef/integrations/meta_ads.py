"""Meta ad accounts through the official Marketing API (same Graph API, v26.0).

Reading needs the ads_read permission; pausing, resuming and changing budgets
need ads_management. Nothing here creates campaigns or ads: changes are
limited to the switches and budgets of what already exists, and the
orchestrator applies them only after the user approves each one.

Budgets travel in the account currency's smallest unit ("offset"): 100 for
most currencies (USD 10.00 -> 1000), 1 for currencies without decimals.
"""

from __future__ import annotations

from .meta import GRAPH, VERSION, Graph, MetaError

ADS_SCOPES = ("ads_read", "ads_management")
NO_DECIMALS = {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}
ACCOUNT_STATUS = {1: "active", 2: "disabled", 3: "unsettled", 7: "pending_review", 8: "pending_settlement",
                  9: "in_grace_period", 100: "pending_closure", 101: "closed", 201: "any_active", 202: "any_closed"}
INSIGHT_FIELDS = "campaign_id,campaign_name,adset_id,adset_name,spend,impressions,reach,clicks,ctr,cpc,cpm,actions," \
                 "action_values,purchase_roas"
DATE_PRESETS = {1: "yesterday", 7: "last_7d", 14: "last_14d", 30: "last_30d", 90: "last_90d"}
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase", "onsite_web_purchase")  # Ads Manager shows omni


def offset(currency: str) -> int:
    return 1 if (currency or "").upper() in NO_DECIMALS else 100


def to_minor(amount: float, currency: str) -> int:
    return int(round(float(amount) * offset(currency)))


def from_minor(value, currency: str) -> float | None:
    if value in (None, ""):
        return None
    return int(value) / offset(currency)


def preset(days: int) -> str:
    return DATE_PRESETS.get(days) or DATE_PRESETS[min(DATE_PRESETS, key=lambda d: abs(d - days))]


def _purchases(row: dict) -> tuple[float, float]:
    """(purchase count, purchase value) from an insights row; Meta reports several overlapping types, take one."""
    def pick(items):
        by_type = {a.get("action_type"): float(a.get("value") or 0) for a in items or []}
        return next((by_type[t] for t in PURCHASE_TYPES if t in by_type), 0.0)
    return pick(row.get("actions")), pick(row.get("action_values"))


def summarize(row: dict) -> dict:
    count, value = _purchases(row)
    spend = float(row.get("spend") or 0)
    return {
        "spend": spend, "impressions": int(float(row.get("impressions") or 0)), "reach": int(float(row.get("reach") or 0)),
        "clicks": int(float(row.get("clicks") or 0)), "ctr": float(row.get("ctr") or 0), "cpc": float(row.get("cpc") or 0),
        "cpm": float(row.get("cpm") or 0), "purchases": count, "revenue": value,
        "roas": round(value / spend, 2) if spend else None, "cpa": round(spend / count, 2) if count else None,
    }


def connect_ads(token: str, api: str = GRAPH, version: str = VERSION) -> list[dict]:
    """Ad accounts this token can see: [{"id": "act_..", "name", "currency", "status"}]."""
    g = Graph(token, api, version)
    data = g.get("me/adaccounts", fields="id,account_id,name,currency,account_status,timezone_name", limit=50)
    out = []
    for a in data.get("data") or []:
        out.append({"id": str(a["id"]), "name": a.get("name") or str(a["id"]), "currency": (a.get("currency") or "USD").upper(),
                    "status": ACCOUNT_STATUS.get(a.get("account_status"), str(a.get("account_status") or ""))})
    if not out:
        raise MetaError("لم أجد حسابًا إعلانيًا بهذا الرمز. أضف صلاحيتي ads_read وads_management وتأكد أنك مدير الحساب الإعلاني.")
    return out


class AdAccount:
    def __init__(self, entry: dict, token: str, api: str = GRAPH, version: str = VERSION):
        self.entry = entry
        self.id = entry["id"]
        self.currency = entry.get("currency") or "USD"
        self.g = Graph(token, api, version)

    def campaigns(self, days: int = 7) -> list[dict]:
        rows = self.g.get(f"{self.id}/campaigns", limit=100,
                          fields="id,name,status,effective_status,objective,daily_budget,lifetime_budget,"
                                 "budget_remaining,start_time,stop_time").get("data") or []
        insights = self.g.get(f"{self.id}/insights", level="campaign", fields=INSIGHT_FIELDS, limit=200,
                              date_preset=preset(days)).get("data") or []
        perf = {i.get("campaign_id"): summarize(i) for i in insights}
        out = []
        for c in rows:
            out.append({"id": str(c["id"]), "name": c.get("name") or "", "status": c.get("status") or "",
                        "effective_status": c.get("effective_status") or "", "objective": c.get("objective") or "",
                        "daily_budget": from_minor(c.get("daily_budget"), self.currency),
                        "lifetime_budget": from_minor(c.get("lifetime_budget"), self.currency),
                        "performance": perf.get(str(c["id"])) or summarize({})})
        return out

    def adsets(self, campaign_id: str, days: int = 7) -> list[dict]:
        rows = self.g.get(f"{campaign_id}/adsets", limit=100,
                          fields="id,name,status,effective_status,daily_budget,lifetime_budget,campaign_id").get("data") or []
        insights = self.g.get(f"{campaign_id}/insights", level="adset", fields=INSIGHT_FIELDS, limit=200,
                              date_preset=preset(days)).get("data") or []
        perf = {i.get("adset_id"): summarize(i) for i in insights}
        return [{"id": str(a["id"]), "name": a.get("name") or "", "status": a.get("status") or "",
                 "effective_status": a.get("effective_status") or "",
                 "daily_budget": from_minor(a.get("daily_budget"), self.currency),
                 "lifetime_budget": from_minor(a.get("lifetime_budget"), self.currency),
                 "campaign_id": str(a.get("campaign_id") or campaign_id),
                 "performance": perf.get(str(a["id"])) or summarize({})} for a in rows]

    def daily(self, object_id: str, days: int = 14) -> list[dict]:
        rows = self.g.get(f"{object_id}/insights", fields="spend,impressions,clicks,ctr,cpc,actions,action_values",
                          time_increment=1, date_preset=preset(days), limit=100).get("data") or []
        return [dict(summarize(r), date=r.get("date_start")) for r in rows]

    def current(self, object_id: str) -> dict:
        """Status and budgets now, to check nothing changed since a proposal was made."""
        data = self.g.get(object_id, fields="id,name,status,daily_budget,lifetime_budget")
        return {"name": data.get("name") or "", "status": data.get("status") or "",
                "daily_budget": from_minor(data.get("daily_budget"), self.currency),
                "lifetime_budget": from_minor(data.get("lifetime_budget"), self.currency)}

    def set_status(self, object_id: str, status: str) -> None:
        if status not in ("ACTIVE", "PAUSED"):
            raise MetaError("الحالة يجب أن تكون ACTIVE أو PAUSED")
        self.g.post(object_id, {"status": status})

    def set_daily_budget(self, object_id: str, amount: float) -> None:
        self.g.post(object_id, {"daily_budget": to_minor(amount, self.currency)})


ADS_SECRET = "meta_ads"


class AdAccounts:
    """settings["ads"] = [{"id": "act_..", "name", "currency", "status"}]; one user token in keys.dat as "meta_ads"."""

    def __init__(self, settings, keystore, api: str = GRAPH, version: str = VERSION):
        self.settings = settings
        self.keystore = keystore
        self.api = api
        self.version = version

    def entries(self) -> list[dict]:
        return self.settings.get()["ads"]

    def public(self) -> list[dict]:
        token = self.keystore.has(ADS_SECRET)
        return [dict(e, connected=token) for e in self.entries()]

    def find(self, ref: str | None) -> dict | None:
        accounts = self.entries()
        if not ref:
            return accounts[0] if len(accounts) == 1 else None
        r = str(ref).strip().lower()
        for e in accounts:
            if r in (e["id"].lower(), e["id"].lower().removeprefix("act_"), e["name"].lower()):
                return e
        return None

    def account(self, entry: dict) -> AdAccount:
        token = self.keystore.get(ADS_SECRET)
        if not token:
            raise MetaError("رمز الحسابات الإعلانية غير محفوظ على هذا الجهاز؛ أعد الربط من حسابات السوشيال")
        return AdAccount(entry, token, self.api, self.version)

    def connect(self, token: str) -> list[dict]:
        found = connect_ads(token, self.api, self.version)[:20]
        self.keystore.set(ADS_SECRET, token)
        self.settings.update({"ads": found})
        return found

    def remove(self, account_id: str) -> bool:
        current = self.entries()
        kept = [e for e in current if e["id"] != account_id]
        if len(kept) == len(current):
            return False
        self.settings.update({"ads": kept})
        if not kept:
            self.keystore.delete(ADS_SECRET)
        return True

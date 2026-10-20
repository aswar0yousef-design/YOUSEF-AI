"""Tools the ads manager calls: read campaigns and results, propose pausing, resuming or rebudgeting.

Every change is a proposal the user approves. Nothing creates campaigns, ads
or audiences, and nothing spends money except a budget the user approves.
"""

from __future__ import annotations

from ..models.base import ProviderError
from .meta import MetaError

OBSERVATION_LIMIT = 8000
MAX_PROPOSALS = 15
STATUS_AR = {"ACTIVE": "تعمل", "PAUSED": "متوقفة", "DELETED": "محذوفة", "ARCHIVED": "مؤرشفة"}

TOOL_HELP = """{"tool": "list_ad_accounts"}                                         connected Meta ad accounts and their currency
{"tool": "campaigns", "account": "name", "days": 7}                    campaigns with budget and results (spend, clicks, CTR, CPC, purchases, revenue, ROAS) for 1, 7, 14, 30 or 90 days
{"tool": "adsets", "account": "name", "campaign_id": "...", "days": 7}  the ad sets of one campaign with their budgets and results
{"tool": "daily", "account": "name", "id": "...", "days": 14}          day-by-day results of a campaign or ad set
{"tool": "propose_pause", "account": "name", "id": "...", "reason": "..."}     propose pausing a campaign or ad set
{"tool": "propose_resume", "account": "name", "id": "...", "reason": "..."}    propose turning a paused one back on
{"tool": "propose_budget", "account": "name", "id": "...", "daily_budget": 25, "reason": "..."}   propose a new daily budget in the account currency (where the budget is set: campaign or ad set)
{"done": "your answer for the user"}                                    finish"""


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... (cut at {limit} characters)"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


class AdsToolbox:
    def __init__(self, ads, default_account: str | None = None):
        self.ads = ads  # meta_ads.AdAccounts
        self.default_account = default_account
        self.trace: list[dict] = []
        self.proposals: list[dict] = []
        self._accounts: dict[str, object] = {}
        self._seen: dict[str, dict] = {}  # object id -> the row last shown to the model

    def help(self) -> str:
        return TOOL_HELP

    def describe_accounts(self) -> str:
        return "\n".join(f'- "{e["name"]}" ({e["id"]}, {e["currency"]}, {e["status"]})' for e in self.ads.entries()) \
            or "(none)"

    def _account(self, call: dict):
        ref = str(call.get("account") or "").strip() or self.default_account
        entry = self.ads.find(ref)
        if entry is None:
            names = ", ".join(f'"{e["name"]}"' for e in self.ads.entries()) or "none"
            raise MetaError(f'ad account "{ref}" not found. Connected: {names}' if ref
                            else f'several ad accounts are connected; say which one with "account". Connected: {names}')
        if entry["id"] not in self._accounts:
            self._accounts[entry["id"]] = self.ads.account(entry)
        return entry, self._accounts[entry["id"]]

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("id") or call.get("campaign_id") or call.get("account") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise MetaError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            out = handler(call)
            self.trace.append({"tool": tool, "target": target[:200], "ok": True})
            return _clip(out)
        except (ProviderError, ValueError, TypeError) as e:
            self.trace.append({"tool": tool, "target": target[:200], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    @staticmethod
    def _days(call: dict) -> int:
        try:
            return max(1, min(int(call.get("days") or 7), 90))
        except (TypeError, ValueError):
            return 7

    def _rows(self, items: list[dict], currency: str, level: str) -> str:
        lines = [f"id | name | status | daily budget | lifetime budget | spend {currency} | impressions | clicks | CTR% | "
                 f"CPC | purchases | revenue | ROAS | cost per purchase"]
        for c in items:
            p = c["performance"]
            self._seen[c["id"]] = dict(c, level=level)
            lines.append(" | ".join([c["id"], c["name"], c.get("effective_status") or c["status"], _num(c["daily_budget"]),
                                     _num(c["lifetime_budget"]), _num(p["spend"]), str(p["impressions"]), str(p["clicks"]),
                                     _num(p["ctr"]), _num(p["cpc"]), _num(p["purchases"], 0), _num(p["revenue"]),
                                     _num(p["roas"]), _num(p["cpa"])]))
        return "\n".join(lines)

    # ---- reading ---------------------------------------------------------------------
    def tool_list_ad_accounts(self, call: dict) -> str:
        return self.describe_accounts()

    def tool_campaigns(self, call: dict) -> str:
        entry, account = self._account(call)
        days = self._days(call)
        items = account.campaigns(days)
        if not items:
            return "This ad account has no campaigns."
        total = sum(c["performance"]["spend"] for c in items)
        revenue = sum(c["performance"]["revenue"] for c in items)
        head = f'Campaigns of "{entry["name"]}", last {days} days. Total spend {_num(total)} {entry["currency"]}, ' \
               f'revenue tracked by Meta {_num(revenue)}. A budget of "-" means it is set on the ad sets.'
        return head + "\n" + self._rows(items, entry["currency"], "campaign")

    def tool_adsets(self, call: dict) -> str:
        entry, account = self._account(call)
        cid = str(call.get("campaign_id") or call.get("id") or "").strip()
        if not cid:
            raise MetaError("adsets needs campaign_id")
        items = account.adsets(cid, self._days(call))
        return self._rows(items, entry["currency"], "adset") if items else "No ad sets in this campaign."

    def tool_daily(self, call: dict) -> str:
        entry, account = self._account(call)
        oid = str(call.get("id") or "").strip()
        if not oid:
            raise MetaError("daily needs the id of a campaign or ad set")
        rows = account.daily(oid, self._days(call) if call.get("days") else 14)
        lines = ["date | spend | clicks | CTR% | purchases | revenue | ROAS"]
        lines += [f"{r['date']} | {_num(r['spend'])} | {r['clicks']} | {_num(r['ctr'])} | {_num(r['purchases'], 0)} | "
                  f"{_num(r['revenue'])} | {_num(r['roas'])}" for r in rows]
        return "\n".join(lines) if rows else "No results in this period."

    # ---- proposals ---------------------------------------------------------------------
    def _target(self, call: dict):
        if len(self.proposals) >= MAX_PROPOSALS:
            raise MetaError("too many proposals in one task")
        entry, account = self._account(call)
        oid = str(call.get("id") or "").strip()
        if not oid:
            raise MetaError("the proposal needs the id of a campaign or ad set (from campaigns or adsets)")
        now = account.current(oid)
        seen = self._seen.get(oid, {})
        level = seen.get("level") or "campaign"
        return entry, account, oid, now, level, seen

    def _add(self, kind: str, entry: dict, oid: str, now: dict, level: str, title: str, rows: list, spec: dict,
             warnings: list) -> None:
        self.proposals.append({"kind": kind, "payload": {
            "account_id": entry["id"], "account": entry["name"], "currency": entry["currency"], "object_id": oid,
            "level": level, "title": title, "rows": rows, "warnings": warnings, "before": now, "spec": spec}})

    def _base_rows(self, entry, now, level, seen) -> list:
        p = seen.get("performance") or {}
        rows = [{"label": "الحساب", "value": entry["name"]},
                {"label": "المستوى", "value": "حملة" if level == "campaign" else "مجموعة إعلانية"},
                {"label": "الحالة الآن", "value": STATUS_AR.get(now["status"], now["status"])}]
        if p:
            rows.append({"label": "النتائج", "value": f"إنفاق {_num(p.get('spend'))} {entry['currency']}، "
                                                      f"مشتريات {_num(p.get('purchases'), 0)}، ROAS {_num(p.get('roas'))}"})
        return rows

    def tool_propose_pause(self, call: dict) -> str:
        entry, account, oid, now, level, seen = self._target(call)
        if now["status"] == "PAUSED":
            return f'No change: "{now["name"]}" is already paused.'
        rows = self._base_rows(entry, now, level, seen) + [{"label": "بعد الموافقة", "value": "تتوقف ولا يُصرف عليها"}]
        if call.get("reason"):
            rows.append({"label": "السبب", "value": str(call["reason"])[:300]})
        self._add("ads_pause", entry, oid, now, level, now["name"], rows, {"status": "PAUSED"}, [])
        return f'Proposed pausing "{now["name"]}". It changes only if the user approves.'

    def tool_propose_resume(self, call: dict) -> str:
        entry, account, oid, now, level, seen = self._target(call)
        if now["status"] == "ACTIVE":
            return f'No change: "{now["name"]}" is already active.'
        if now["status"] in ("DELETED", "ARCHIVED"):
            raise MetaError("deleted or archived campaigns cannot be turned back on")
        budget = now["daily_budget"] or now["lifetime_budget"]
        after = "تعمل ويبدأ الصرف" + (f" (الميزانية {_num(budget)} {entry['currency']})" if budget else "")
        rows = self._base_rows(entry, now, level, seen) + [{"label": "بعد الموافقة", "value": after}]
        if call.get("reason"):
            rows.append({"label": "السبب", "value": str(call["reason"])[:300]})
        self._add("ads_resume", entry, oid, now, level, now["name"], rows, {"status": "ACTIVE"},
                  ["تشغيلها يعيد الصرف من الميزانية"])
        return f'Proposed resuming "{now["name"]}". It changes only if the user approves.'

    def tool_propose_budget(self, call: dict) -> str:
        entry, account, oid, now, level, seen = self._target(call)
        try:
            new = round(float(str(call.get("daily_budget")).replace(",", "")), 2)
        except (TypeError, ValueError):
            raise MetaError("daily_budget must be a number in the account currency") from None
        if new <= 0:
            raise MetaError("daily_budget must be above zero (to stop spending, propose_pause)")
        old = now["daily_budget"]
        if old is None:
            if now["lifetime_budget"] is not None:
                raise MetaError("this one has a lifetime budget, not a daily one; change it in Ads Manager")
            raise MetaError("no daily budget here: it is set at the other level (campaign or ad sets); use that id")
        if abs(new - old) < 0.01:
            return f"No change: the daily budget is already {_num(old)}."
        warnings = []
        change = (new - old) / old * 100 if old else 100
        if change > 20:
            warnings.append(f"زيادة {change:.0f}%: الزيادات الكبيرة قد تعيد الحملة إلى مرحلة التعلّم؛ الأفضل رفعها تدريجيًا (20% كل بضعة أيام)")
        if change < -50:
            warnings.append(f"خفض {abs(change):.0f}%: قد يقل الوصول كثيرًا")
        if new < 1:
            warnings.append("ميزانية أقل من 1 في اليوم قد يرفضها Meta")
        rows = self._base_rows(entry, now, level, seen) + [
            {"label": "الميزانية اليومية", "value": f"من {_num(old)} إلى {_num(new)} {entry['currency']}"},
            {"label": "الفرق في الشهر", "value": f"{'+' if new > old else ''}{_num((new - old) * 30)} {entry['currency']} تقريبًا"}]
        if call.get("reason"):
            rows.append({"label": "السبب", "value": str(call["reason"])[:300]})
        self._add("ads_budget", entry, oid, now, level, now["name"], rows, {"daily_budget": new}, warnings)
        return f'Proposed daily budget {_num(new)} {entry["currency"]} for "{now["name"]}" (now {_num(old)}). ' \
               "It changes only if the user approves."

    def action_list(self) -> list[dict]:
        return list(self.proposals)

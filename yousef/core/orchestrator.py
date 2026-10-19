"""The orchestrator: plan -> run each step with the right agent -> final answer.

Flow for every request:
1. Planner splits the request into 1-5 steps and picks an agent for each.
2. Steps run in order; each agent sees the request, earlier results, recent
   conversation and the user's lessons. The router picks the model.
3. Steps that act on the outside world wait for the user's approval.
   With a project folder attached, the coder works through file tools and its
   edits wait for approval as diffs before anything is written. With stores
   connected, the store manager reads them and its product proposals wait for
   approval with before/after values. The technical manager checks the stores'
   sites and the project code, and its fixes wait for approval the same way.
4. Results are combined into one final answer and remembered.
Everything is written to memory as it happens, so the dashboard can follow
along, and a task interrupted by closing the app is marked as such on restart.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field

from ..memory import Memory
from ..models import NoModelError, Router
from ..integrations import GitHub, Vercel
from ..integrations.github import API as GITHUB_API
from ..integrations.store_tools import comparable, current_values
from ..integrations.stores import PLATFORM_LABEL
from ..integrations.vercel import API as VERCEL_API
from ..models.base import ProviderError
from ..workspace import Services, Workspace, WorkspaceError, git, registry
from .agents import AGENTS, available_agents, get_agent
from .coder import run_coder
from .store_manager import run_store_manager
from .tech_manager import build_toolbox, run_tech_manager
from .jsonutil import extract_json
from .permissions import requires_approval

log = logging.getLogger("yousef.orchestrator")

MAX_STEPS = 5
MAX_REQUEST = 8000
STEP_RESULT_LIMIT = 6000
SHORT_REQUEST = 25  # greetings and tiny questions skip the planner

PLANNER_PROMPT = """You are the planner of YOUSEF AI. Split the user's request into the fewest steps that get it done, and assign each step to one agent.

Agents:
{agents}

Rules:
- A question, a chat message or a single piece of work is ONE step. Use several steps only when the work has real stages (e.g. analyse, then write, then review). Maximum {max_steps} steps.
- Each instruction must be self-contained: the agent sees the original request and earlier results, but write what this step must produce. Write instructions in the user's language.
- Set "needs_confirmation": true for any step that would act outside this conversation: publish, post, send, pay, buy, delete, trade, deploy or change files, stores, accounts or settings. Writing a draft is not an action.
- Prefer the most specific agent; use "general" when none fits.
{lessons}
Answer with JSON only, in this exact shape:
{{"steps": [{{"agent": "general", "instruction": "...", "needs_confirmation": false}}]}}"""

VOICE_HINT = """The user is talking to you by voice and will hear your answer read aloud.
Open with a short spoken answer: 2 to 4 plain sentences, no markdown, no lists, no symbols.
If the request needs long content (code, tables, posts, plans), put it after that opening; it will be shown on screen."""

STORES_NOTE = """Connected online stores: {stores}. The "store_manager" agent can read their products, orders and sales and propose new products or changes (the user approves each one). Give any step that needs store data or changes to "store_manager", in ONE step when possible; other agents cannot see the stores."""

WORKSPACE_NOTE = """The user attached the project folder "{name}". The "coder" agent can list, read and search its files, propose edits (the user approves each change as a diff), use git (commit, push, pull requests after approval), run the project's allowed commands and check Vercel deployments. Give any step that needs the project to "coder", in ONE step when possible."""

TECH_NOTE = """The "tech_manager" agent checks {targets}: security (HTTPS, headers, exposed files, leaked keys, vulnerable code and packages), errors, speed and technical SEO, and proposes fixes for approval. Give checks, security reviews, bug hunting and technical fixes to "tech_manager"{others}."""

# short requests that sound like a technical check go through the planner, which can pick the technical manager
TECH_HINT = re.compile(r"ثغر|أمان|امان|حماي|افحص|فحص|اخطاء|أخطاء|خطأ|سرعة|بطيء|security|vulnerab|audit|scan|bug|hack|اختراق", re.I)

COMPOSE_PROMPT = """Several agents worked on the user's request in steps. Write the final answer for the user from their results.
- Keep everything the user needs complete: code, posts, drafts, tables. Do not shorten them.
- Remove repetition between steps and fix contradictions.
- Do not describe the internal steps or agents; just deliver the result, in the user's language."""


def parse_plan(text: str, connected: frozenset | set = frozenset()) -> list[dict]:
    data = extract_json(text)
    raw_steps = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(raw_steps, list):
        raise ValueError("plan has no steps list")
    steps = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        instruction = str(raw.get("instruction") or raw.get("task") or "").strip()
        if not instruction:
            continue
        agent = get_agent(str(raw.get("agent") or "general"), connected).key
        flag = raw.get("needs_confirmation") is True or str(raw.get("needs_confirmation")).lower() == "true"
        steps.append({
            "agent": agent,
            "instruction": instruction[:2000],
            "needs_confirmation": requires_approval(instruction, flag),
        })
        if len(steps) == MAX_STEPS:
            break
    if not steps:
        raise ValueError("plan is empty")
    return steps


@dataclass
class Job:
    request: str
    voice: bool = False
    workspace: str | None = None
    context: dict = field(default_factory=dict)
    store: str | None = None  # store id picked by the user

    def full_request(self, selection_limit: int = 12000) -> str:
        """The request plus what the user is looking at in their editor."""
        c = self.context or {}
        parts = [self.request]
        if c.get("file"):
            lang = f" ({c['language']})" if c.get("language") else ""
            parts.append(f"The user has this file open: {c['file']}{lang}")
        if c.get("selection"):
            lines = ""
            if c.get("start_line") and c.get("end_line"):
                lines = f" (lines {c['start_line']}-{c['end_line']})"
            sel = c["selection"][:selection_limit]
            parts.append(f"Selected code{lines}:\n```{c.get('language') or ''}\n{sel}\n```")
        return "\n\n".join(parts)


def _lessons_text(lessons: list[dict]) -> tuple[list[str], str]:
    rules = [l["text"] for l in lessons if l["kind"] == "rule"]
    outcomes = [l for l in lessons if l["kind"] != "rule"]
    lines = []
    if outcomes:
        lines.append("Past outcomes the user rated (learn from them):")
        for l in outcomes:
            label = "WORKED" if l["kind"] == "success" else "DID NOT WORK"
            lines.append(f"- [{label}] {l['text']}")
    return rules, "\n".join(lines)


class Orchestrator:
    def __init__(self, memory: Memory, router: Router, settings, keystore=None, stores=None):
        self.memory = memory
        self.router = router
        self.settings = settings
        self.keystore = keystore
        self.stores = stores
        self.github_api = GITHUB_API
        self.vercel_api = VERCEL_API
        self.site_auditor = None  # audit.SiteAuditor; None = live checks over the network
        self.osv = None           # (post, get) for the vulnerability database; None = api.osv.dev
        self.wp_latest = None     # callable returning the latest WordPress version; None = api.wordpress.org
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}
        self._decisions: dict[int, tuple[bool, str]] = {}
        self._scopes: dict[int, str] = {}  # step_id -> "all" | "files" after approval
        self._excludes: dict[int, set[int]] = {}  # step_id -> action ids the user unticked
        self._cancelled: set[int] = set()
        self._threads: dict[int, threading.Thread] = {}
        self._shutdown = threading.Event()

    # ---- public API ----------------------------------------------------------
    def submit(self, request: str, source: str = "text", workspace: str | None = None,
               context: dict | None = None, store: str | None = None) -> int:
        request = (request or "").strip()
        if not request:
            raise ValueError("الطلب فارغ")
        if len(request) > MAX_REQUEST:
            raise ValueError(f"الطلب أطول من {MAX_REQUEST} حرف")
        if workspace is not None and registry.find(self.settings, workspace) is None:
            raise WorkspaceError(f"المشروع «{workspace}» غير مسموح به")
        store_name = None
        if store is not None:
            entry = self.stores.find(store) if self.stores else None
            if entry is None:
                raise ValueError("المتجر غير موجود")
            store, store_name = entry["id"], entry["name"]
        task_id = self.memory.create_task(request, source, workspace, context or None, store_name)
        self.memory.add_message("user", request, task_id)
        job = Job(request=request, voice=source == "voice", workspace=workspace, context=context or {}, store=store)
        thread = threading.Thread(target=self._run_safe, args=(task_id, job), name=f"task-{task_id}", daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return task_id

    def decide(self, step_id: int, approve: bool, scope: str = "all", exclude: list[int] | None = None) -> bool:
        """scope "files": write the proposed files but skip commit/push/pull request.
        exclude: ids of proposed actions (e.g. one product change) the user does not want."""
        step = self.memory.get_step(step_id)
        if not step or step["status"] != "awaiting_approval":
            return False
        with self._lock:
            event = self._events.get(step_id)
            if event is None:
                return False
            self._decisions[step_id] = (bool(approve), "files" if scope == "files" else "all")
            self._excludes[step_id] = {int(i) for i in (exclude or [])}
        event.set()
        return True

    def cancel(self, task_id: int) -> bool:
        task = self.memory.get_task(task_id)
        if not task or task["status"] not in ("planning", "running", "waiting"):
            return False
        with self._lock:
            self._cancelled.add(task_id)
            waiting = [s["id"] for s in task["steps"] if s["status"] == "awaiting_approval"]
            for step_id in waiting:
                event = self._events.get(step_id)
                if event:
                    event.set()
        return True

    def feedback(self, task_id: int, good: bool, note: str = "") -> bool:
        task = self.memory.get_task(task_id)
        if not task or task["status"] != "done":
            return False
        note = (note or "").strip()[:1000]
        self.memory.update_task(task_id, feedback=1 if good else -1)
        request = task["request"][:300]
        text = f"الطلب: «{request}»"
        if note:
            text += f" — ملاحظة المستخدم: {note}"
        self.memory.add_lesson("success" if good else "failure", text, task_id)
        return True

    def wait(self, task_id: int, timeout: float = 30) -> None:
        with self._lock:
            thread = self._threads.get(task_id)
        if thread:
            thread.join(timeout)

    def shutdown(self, wait: float = 3.0) -> None:
        """Stop waiting tasks and give running ones a moment to record their state."""
        self._shutdown.set()
        with self._lock:
            for event in self._events.values():
                event.set()
            threads = list(self._threads.values())
        deadline = time.time() + wait
        for thread in threads:
            thread.join(max(0.0, deadline - time.time()))

    # ---- internals -----------------------------------------------------------
    def _is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancelled

    def _run_safe(self, task_id: int, job: Job) -> None:
        try:
            self._run(task_id, job)
        except Exception as e:  # never leave a task stuck in "running"
            log.exception("task %s crashed", task_id)
            self.memory.update_task(task_id, status="failed", error=f"خطأ داخلي: {e}")
        finally:
            with self._lock:
                self._threads.pop(task_id, None)
                self._cancelled.discard(task_id)

    def _run(self, task_id: int, job: Job) -> None:
        s = self.settings.get()
        voice = job.voice
        request = job.full_request()
        history = self.memory.recent_turns(s["history_turns"], exclude_task=task_id)
        lessons = self.memory.relevant_lessons(job.request)
        ws = None
        if job.workspace:
            try:
                ws = registry.open_workspace(self.settings, job.workspace)
            except WorkspaceError as e:
                self.memory.update_task(task_id, status="failed", error=str(e))
                return

        connected = self._connected(ws)
        try:
            plan = self._plan(request, history, lessons, ws, connected, job.store)
        except NoModelError as e:
            self.memory.update_task(task_id, status="failed", error=str(e))
            return
        step_ids = self.memory.add_steps(task_id, plan)
        self.memory.update_task(task_id, status="running")

        results: list[tuple[dict, str]] = []
        for step_id, step in zip(step_ids, plan):
            if self._is_cancelled(task_id) or self._shutdown.is_set():
                self._stop(task_id, step_ids)
                return
            agent = get_agent(step["agent"], connected)
            coder_in_project = ws is not None and agent.key == "coder"
            store_step = agent.key == "store_manager"
            tech_step = agent.key == "tech_manager"
            # tool agents propose their actions; the approval comes after they work, on the real proposals
            if step["needs_confirmation"] and not (coder_in_project or store_step or tech_step):
                approved = self._wait_for_approval(task_id, step_id)
                self._take_decision(step_id)
                if approved is None:
                    self._stop(task_id, step_ids)
                    return
                if not approved:
                    self.memory.update_step(step_id, status="skipped", output="رفض المستخدم هذه الخطوة.",
                                            finished_at=time.time())
                    continue
            self.memory.update_step(step_id, status="running", started_at=time.time())
            speak = voice and len(plan) == 1  # this step's output is the final answer
            if coder_in_project:
                outcome = self._run_coder_step(task_id, step_id, agent, step, request, history, results,
                                               lessons, speak, ws)
                if outcome is None:  # cancelled, failed or shut down; already recorded
                    if self._is_cancelled(task_id) or self._shutdown.is_set():
                        self._stop(task_id, step_ids)
                    else:
                        self._skip_rest(step_ids, step_id)
                    return
                results.append((step, outcome))
                continue
            if store_step or tech_step:
                if tech_step:
                    outcome = self._run_tech_step(task_id, step_id, agent, step, request, history, results,
                                                  lessons, speak, ws, job.store)
                else:
                    outcome = self._run_store_step(task_id, step_id, agent, step, request, history, results,
                                                   lessons, speak, job.store)
                if outcome is None:
                    if self._is_cancelled(task_id) or self._shutdown.is_set():
                        self._stop(task_id, step_ids)
                    else:
                        self._skip_rest(step_ids, step_id)
                    return
                results.append((step, outcome))
                continue
            try:
                completion = self.router.complete(
                    agent.task_type, self._agent_messages(agent, step, request, history, results, lessons, speak)
                )
            except NoModelError as e:
                self.memory.update_step(step_id, status="failed", error=str(e), finished_at=time.time())
                self.memory.update_task(task_id, status="failed", error=str(e))
                self._skip_rest(step_ids, step_id)
                return
            self.memory.update_step(step_id, status="done", output=completion.text, provider=completion.provider,
                                    model=completion.model, finished_at=time.time())
            results.append((step, completion.text))

        if self._is_cancelled(task_id):
            self._stop(task_id, step_ids)
            return
        final = self._compose(request, results, lessons, voice)
        self.memory.update_task(task_id, status="done", final=final)
        self.memory.add_message("assistant", final, task_id)

    def _connected(self, ws: Workspace | None = None) -> frozenset:
        found = set()
        if self.stores and self.stores.entries():
            found.add("stores")
        if ws is not None:
            found.add("workspace")
        return frozenset(found)

    def _plan(self, request: str, history: list[dict], lessons: list[dict],
              ws: Workspace | None = None, connected: frozenset = frozenset(), store: str | None = None) -> list[dict]:
        default = "coder" if ws else ("store_manager" if store and "stores" in connected else "general")
        # a short request skips the planner, unless stores are connected and none was picked:
        # "كم طلب اليوم؟" must reach the store manager, which only the planner can decide
        obvious = ws is not None or store is not None or "stores" not in connected
        if obvious and connected and TECH_HINT.search(request):
            obvious = False  # "افحص متجري" belongs to the technical manager, which the planner picks
        if len(request) <= SHORT_REQUEST and "\n" not in request and obvious:
            return [{"agent": default, "instruction": request,
                     "needs_confirmation": requires_approval(request, False)}]
        agents = "\n".join(f'- "{a.key}": {a.role}' for a in available_agents(connected))
        rules, outcomes = _lessons_text(lessons)
        lesson_block = ""
        if rules:
            lesson_block += "\nUser's saved rules (keep them in the instructions):\n" + "\n".join(f"- {r}" for r in rules)
        if outcomes:
            lesson_block += "\n" + outcomes
        system = PLANNER_PROMPT.format(agents=agents, max_steps=MAX_STEPS, lessons=lesson_block + "\n")
        if ws:
            system += "\n\n" + WORKSPACE_NOTE.format(name=ws.name)
        if "stores" in connected:
            names = ", ".join(f'"{e["name"]}" ({PLATFORM_LABEL[e["platform"]]})' for e in self.stores.entries())
            system += "\n\n" + STORES_NOTE.format(stores=names)
            if store:
                picked = self.stores.find(store)
                if picked:
                    system += f'\nThe user picked the store "{picked["name"]}" for this request.'
        if connected:
            targets = " and ".join(t for t in (("the connected stores and their live sites" if "stores" in connected else ""),
                                               (f'the project folder "{ws.name}"' if ws else "")) if t)
            others = []
            if ws:
                others.append('new features and ordinary code changes stay with "coder"')
            if "stores" in connected:
                others.append('products, prices, offers and sales stay with "store_manager"')
            system += "\n\n" + TECH_NOTE.format(targets=targets, others="; " + "; ".join(others) if others else "")
        messages = [{"role": "system", "content": system}]
        if history:
            context = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
            messages.append({"role": "user", "content": f"Recent conversation for context:\n{context}"})
            messages.append({"role": "assistant", "content": "Understood."})
        messages.append({"role": "user", "content": f"Request:\n{request}\n\nReturn the JSON plan."})
        try:
            completion = self.router.complete("plan", messages, json_mode=True)
            return parse_plan(completion.text, connected)
        except NoModelError:
            raise
        except ValueError as e:
            log.warning("planner output unusable (%s); running as one step", e)
            return [{"agent": default, "instruction": request,
                     "needs_confirmation": requires_approval(request, False)}]

    def _step_parts(self, step: dict, request: str, history: list[dict], results: list[tuple[dict, str]]) -> str:
        parts = [f"Request:\n{request}"]
        if history:
            recent = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-4:])
            parts.append(f"Recent conversation:\n{recent}")
        for prev_step, text in results:
            parts.append(f"Result of an earlier step ({AGENTS[prev_step['agent']].name}):\n{text[:STEP_RESULT_LIMIT]}")
        if step["instruction"].strip() != request.strip():
            parts.append(f"Your step:\n{step['instruction']}")
        return "\n\n".join(parts)

    def _take_decision(self, step_id: int) -> tuple[str, set[int]]:
        with self._lock:
            return self._scopes.pop(step_id, "all"), self._excludes.pop(step_id, set())

    def _system(self, agent, lessons: list[dict], speak: bool, **kwargs) -> str:
        rules, outcomes = _lessons_text(lessons)
        system = agent.system_prompt(rules, **kwargs)
        if outcomes:
            system += "\n\n" + outcomes
        if speak:
            system += "\n\n" + VOICE_HINT
        return system

    def _fail_step(self, task_id: int, step_id: int, error: Exception) -> None:
        self.memory.update_step(step_id, status="failed", error=str(error), finished_at=time.time())
        self.memory.update_task(task_id, status="failed", error=str(error))

    def _run_store_step(self, task_id: int, step_id: int, agent, step: dict, request: str,
                        history: list[dict], results: list[tuple[dict, str]], lessons: list[dict],
                        speak: bool, store: str | None) -> str | None:
        """Tool loop over the stores, then approval of the proposed product changes."""
        system = self._system(agent, lessons, speak, stores=True)
        try:
            res = run_store_manager(self.router, self.stores, system, self._step_parts(step, request, history, results),
                                    default_store=store)
        except NoModelError as e:
            self._fail_step(task_id, step_id, e)
            return None
        return self._finish_tool_step(task_id, step_id, res, [], res.toolbox.action_list())

    def _run_tech_step(self, task_id: int, step_id: int, agent, step: dict, request: str,
                       history: list[dict], results: list[tuple[dict, str]], lessons: list[dict],
                       speak: bool, ws: Workspace | None, store: str | None) -> str | None:
        """Checks of the stores' sites and the project, then approval of the proposed fixes."""
        system = self._system(agent, lessons, speak, tech=True)
        services = self._services(ws) if ws else None
        toolbox = build_toolbox(self.stores, store, ws, services, auditor=self.site_auditor, osv=self.osv,
                                wp_latest=self.wp_latest)
        try:
            res = run_tech_manager(self.router, toolbox, system, self._step_parts(step, request, history, results))
        except NoModelError as e:
            self._fail_step(task_id, step_id, e)
            return None
        return self._finish_tool_step(task_id, step_id, res, toolbox.change_list(), toolbox.action_list(), ws, services)

    def _run_coder_step(self, task_id: int, step_id: int, agent, step: dict, request: str,
                        history: list[dict], results: list[tuple[dict, str]], lessons: list[dict],
                        speak: bool, ws: Workspace) -> str | None:
        """Tool loop in the project, then approval of the proposed diffs. Returns the step's output."""
        system = self._system(agent, lessons, speak, project=ws.name)
        services = self._services(ws)
        try:
            res = run_coder(self.router, ws, system, self._step_parts(step, request, history, results),
                            services=services)
        except NoModelError as e:
            self._fail_step(task_id, step_id, e)
            return None
        return self._finish_tool_step(task_id, step_id, res, res.toolbox.change_list(), res.toolbox.action_list(),
                                      ws, services)

    def _finish_tool_step(self, task_id: int, step_id: int, res, changes: list[dict], actions: list[dict],
                          ws: Workspace | None = None, services: Services | None = None) -> str | None:
        self.memory.update_step(step_id, output=res.text, provider=res.provider, model=res.model,
                                trace=json.dumps(res.toolbox.trace, ensure_ascii=False))
        output = self._settle(task_id, step_id, res.text, changes, actions, ws, services)
        if output is not None:
            self.memory.update_step(step_id, status="done", output=output, finished_at=time.time())
        return output

    def _settle(self, task_id: int, step_id: int, output: str, changes: list[dict], actions: list[dict],
                ws: Workspace | None, services: Services | None) -> str | None:
        """Wait for the user's decision on an agent's proposals, then carry out what was approved:
        files first, then git (commit, push, pull request), then store changes.
        Returns the step's output, or None when the task was cancelled."""
        if not (changes or actions):
            return output
        self.memory.add_changes(task_id, step_id, changes)
        self.memory.add_actions(task_id, step_id, actions)
        approved = self._wait_for_approval(task_id, step_id)
        scope, excluded = self._take_decision(step_id)
        pending = self.memory.get_changes(step_id, "pending")
        pending_actions = self.memory.get_actions(step_id, "pending")
        had_store = any(a["kind"].startswith("store_") for a in pending_actions)
        if approved is None:
            for c in pending:
                self.memory.update_change(c["id"], "rejected", "أُلغيت المهمة")
            for a in pending_actions:
                self.memory.update_action(a["id"], "rejected", "أُلغيت المهمة")
            return None
        if not approved:
            for c in pending:
                self.memory.update_change(c["id"], "rejected")
            for a in pending_actions:
                self.memory.update_action(a["id"], "rejected")
            only_store = had_store and not pending and all(a["kind"].startswith("store_") for a in pending_actions)
            return output + "\n\n" + ("**تغييرات المتجر:** رفضها المستخدم، ولم يتغيّر شيء." if only_store
                                      else "**التعديلات:** رفضها المستخدم، ولم يُنفَّذ شيء.")
        chosen = []
        for a in pending_actions:
            if a["id"] in excluded:
                self.memory.update_action(a["id"], "rejected", "استبعدتها من الموافقة")
            else:
                chosen.append(a)
        git_actions = [a for a in chosen if not a["kind"].startswith("store_")]
        store_actions = [a for a in chosen if a["kind"].startswith("store_")]
        applied, summary = self._apply_changes(ws, pending) if pending and ws else ([], "")
        if summary:
            output += "\n\n" + summary
        if scope == "files" and (git_actions or store_actions):
            for a in git_actions + store_actions:
                self.memory.update_action(a["id"], "rejected", "اخترت تطبيق الملفات فقط")
            if git_actions:
                output += "\n\n**git:** لم يُنفَّذ (اخترت تطبيق الملفات فقط)."
            if store_actions:
                output += "\n\n**المتجر:** لم يتغيّر (اخترت تطبيق الملفات فقط)."
            return output
        if git_actions:
            if pending and not applied:
                for a in git_actions:
                    self.memory.update_action(a["id"], "skipped", "لم يُكتب أي ملف")
                output += "\n\n**git:** لم يُنفَّذ لأن الملفات لم تُكتب."
            else:
                output += "\n\n" + self._run_actions(ws, services, git_actions, applied)
        if store_actions:
            output += "\n\n" + self._run_store_actions(store_actions)
        elif had_store and not pending:
            output += "\n\n**المتجر:** لم تختر أي تغيير."
        return output

    def _run_store_actions(self, actions: list[dict]) -> str:
        """Each approved product change runs on its own; one failure does not stop the others."""
        lines = []
        for action in actions:
            kind, p = action["kind"], action["payload"]
            label = p.get("title") or p.get("after", {}).get("title") or "?"
            try:
                entry = self.stores.find(p["store_id"])
                if entry is None:
                    raise ProviderError("المتجر لم يعد متصلًا")
                client = self.stores.client(entry)
                if kind == "store_create_product":
                    out = client.create_product(p["after"])
                    status = "draft" if p["after"].get("status", "draft") == "draft" else "active"
                    note = "مسودة" if status == "draft" else "منشور"
                    self.memory.update_action(action["id"], "done", out.get("url") or str(out.get("id")))
                    lines.append(f"- أُنشئ «{label}» في {entry['name']} ({note}): {out.get('url') or out.get('id')}")
                elif kind == "store_update_product":
                    current = current_values(client.product(p["product_id"]), p["before"].keys(), p.get("variant_id"))
                    moved = [f for f, old in p["before"].items() if comparable(f, current.get(f)) != comparable(f, old)]
                    if moved:
                        note = "تغيّر في المتجر بعد الاقتراح: " + "، ".join(moved)
                        self.memory.update_action(action["id"], "conflict", note)
                        lines.append(f"- لم يُعدَّل «{label}»: {note}")
                        continue
                    out = client.update_product(p["product_id"], p["after"], p.get("variant_id"))
                    self.memory.update_action(action["id"], "done", out.get("url") or str(out.get("id")))
                    changed = "، ".join(f"{k}: {v}" for k, v in p["after"].items() if k != "description")
                    if "description" in p["after"]:
                        changed = (changed + "، " if changed else "") + "الوصف"
                    lines.append(f"- عُدّل «{label}» في {entry['name']} ({changed})")
                elif kind == "store_create_discount":
                    out = client.create_discount(p["spec"])
                    self.memory.update_action(action["id"], "done", out.get("url") or str(out.get("id")))
                    lines.append(f"- أُنشئ العرض «{label}» في {entry['name']}: {out.get('url') or out.get('id')}")
                elif kind == "store_end_discount":
                    out = client.end_discount(p["spec"]["id"])
                    self.memory.update_action(action["id"], "done", out.get("url") or str(out.get("id")))
                    lines.append(f"- أُوقف العرض «{label}» في {entry['name']}")
                elif kind == "store_create_bundle":
                    out = client.create_bundle(p["spec"])
                    note = "منشورة" if p["spec"].get("status") == "active" else "مسودة"
                    self.memory.update_action(action["id"], "done", out.get("url") or str(out.get("id")))
                    lines.append(f"- أُنشئت الباقة «{label}» في {entry['name']} ({note}): {out.get('url') or out.get('id')}")
                else:
                    raise ProviderError(f"إجراء غير معروف: {kind}")
            except (ProviderError, ValueError, KeyError) as e:
                self.memory.update_action(action["id"], "failed", str(e)[:500])
                lines.append(f"- تعذّر تنفيذ «{label}»: {e}")
        return "**تغييرات المتجر:**\n" + "\n".join(lines)

    def _services(self, ws: Workspace) -> Services:
        entry = registry.find(self.settings, ws.name) or {}
        is_repo = git.is_repo(ws.root)
        remote = git.remote_url(ws.root) if is_repo else None
        token = self.keystore.get("github") if self.keystore else None
        vtoken = self.keystore.get("vercel") if self.keystore else None
        return Services(
            commands=list(entry.get("commands") or []),
            git=is_repo,
            has_remote=bool(remote),
            github=GitHub(token, self.github_api) if token else None,
            github_repo=git.parse_github(remote),
            vercel=Vercel(vtoken, self.vercel_api, team=entry.get("vercel_team") or None) if vtoken else None,
            vercel_project=entry.get("vercel_project") or None,
        )

    def _run_actions(self, ws: Workspace, services: Services, actions: list[dict], applied: list[str]) -> str:
        """Commit, then push, then open the pull request; stop at the first failure."""
        lines = []
        failed = False
        for action in actions:
            if failed:
                self.memory.update_action(action["id"], "skipped", "توقّف بعد خطوة فاشلة")
                continue
            kind, payload = action["kind"], action["payload"]
            try:
                if kind == "git_commit":
                    result = git.commit(ws.root, payload["message"], None if payload.get("all") else applied,
                                        payload.get("branch"))
                    lines.append(f"**commit:** `{result}`")
                elif kind == "git_push":
                    result = git.push(ws.root)
                    lines.append(f"**push:** {result}")
                elif kind == "open_pr":
                    if not (services.github and services.github_repo):
                        raise WorkspaceError("GitHub غير متصل لهذا المستودع")
                    owner, repo = services.github_repo
                    pr = services.github.open_pull_request(owner, repo, git.current_branch(ws.root), payload["title"],
                                                           payload.get("body") or "", payload.get("base"))
                    result = pr["url"] or f"#{pr['number']}"
                    lines.append(f"**Pull Request:** {result}")
                else:
                    raise WorkspaceError(f"إجراء غير معروف: {kind}")
                self.memory.update_action(action["id"], "done", result)
            except (WorkspaceError, ProviderError) as e:
                failed = True
                self.memory.update_action(action["id"], "failed", str(e)[:500])
                lines.append(f"**{kind}:** تعذّر — {e}")
        return "\n".join(lines)

    def _apply_changes(self, ws: Workspace, pending: list[dict]) -> tuple[list[str], str]:
        by_path = {c["path"]: c for c in pending}
        applied, problems = [], []
        for res in ws.apply(pending):
            change = by_path[res.path]
            self.memory.update_change(change["id"], res.status, res.note)
            if res.status == "applied":
                applied.append(res.path)
            else:
                problems.append(f"{res.path} ({res.note})")
        lines = []
        if applied:
            lines.append("**طُبّقت التعديلات على:** " + "، ".join(f"`{p}`" for p in applied))
        if problems:
            lines.append("**لم تُكتب:** " + "، ".join(problems))
        return applied, "\n".join(lines)

    def _agent_messages(self, agent, step: dict, request: str, history: list[dict],
                        results: list[tuple[dict, str]], lessons: list[dict], speak: bool = False) -> list[dict]:
        rules, outcomes = _lessons_text(lessons)
        system = agent.system_prompt(rules)
        if outcomes:
            system += "\n\n" + outcomes
        if speak:
            system += "\n\n" + VOICE_HINT
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        if not results and step["instruction"].strip() == request.strip():
            messages.append({"role": "user", "content": request})
            return messages
        parts = [f"Original request:\n{request}"]
        for prev_step, text in results:
            name = AGENTS[prev_step["agent"]].name
            parts.append(f"Result of an earlier step ({name}):\n{text[:STEP_RESULT_LIMIT]}")
        parts.append(f"Your step:\n{step['instruction']}")
        messages.append({"role": "user", "content": "\n\n".join(parts)})
        return messages

    def _compose(self, request: str, results: list[tuple[dict, str]], lessons: list[dict],
                 voice: bool = False) -> str:
        if not results:
            return "لم تُنفَّذ أي خطوة."
        if len(results) == 1:
            return results[0][1]
        fallback = "\n\n".join(f"### {AGENTS[s['agent']].name}\n{text}" for s, text in results)
        rules, _ = _lessons_text(lessons)
        system = AGENTS["general"].system_prompt(rules) + "\n\n" + COMPOSE_PROMPT
        if voice:
            system += "\n\n" + VOICE_HINT
        body = "\n\n".join(f"Step result ({AGENTS[s['agent']].name}):\n{t[:STEP_RESULT_LIMIT]}" for s, t in results)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Original request:\n{request}\n\n{body}\n\nWrite the final answer."},
        ]
        try:
            text = self.router.complete("writing", messages).text
            return text or fallback
        except NoModelError:
            return fallback

    def _wait_for_approval(self, task_id: int, step_id: int) -> bool | None:
        event = threading.Event()
        with self._lock:
            self._events[step_id] = event
        self.memory.update_step(step_id, status="awaiting_approval")
        self.memory.update_task(task_id, status="waiting")
        try:
            while not event.wait(0.5):
                if self._shutdown.is_set() or self._is_cancelled(task_id):
                    return None
            if self._shutdown.is_set() or self._is_cancelled(task_id):
                return None
            with self._lock:
                decision = self._decisions.pop(step_id, None)
            if decision is None:
                return None
            self.memory.update_task(task_id, status="running")
            with self._lock:
                self._scopes[step_id] = decision[1]
            return decision[0]
        finally:
            with self._lock:
                self._events.pop(step_id, None)

    def _stop(self, task_id: int, step_ids: list[int]) -> None:
        for sid in step_ids:
            step = self.memory.get_step(sid)
            if step and step["status"] in ("pending", "awaiting_approval", "running"):
                self.memory.update_step(sid, status="skipped")
        self.memory.update_task(task_id, status="cancelled", error="أُلغيت المهمة.")

    def _skip_rest(self, step_ids: list[int], failed_id: int) -> None:
        after = False
        for sid in step_ids:
            if after:
                self.memory.update_step(sid, status="skipped")
            if sid == failed_id:
                after = True

"""The orchestrator: plan -> run each step with the right agent -> final answer.

Flow for every request:
1. Planner splits the request into 1-5 steps and picks an agent for each.
2. Steps run in order; each agent sees the request, earlier results, recent
   conversation and the user's lessons. The router picks the model.
3. Steps that act on the outside world wait for the user's approval.
   With a project folder attached, the coder works through file tools and its
   edits wait for approval as diffs before anything is written.
4. Results are combined into one final answer and remembered.
Everything is written to memory as it happens, so the dashboard can follow
along, and a task interrupted by closing the app is marked as such on restart.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from ..memory import Memory
from ..models import NoModelError, Router
from ..workspace import Workspace, WorkspaceError, registry
from .agents import AGENTS, available_agents, get_agent
from .coder import run_coder
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

WORKSPACE_NOTE = """The user attached the project folder "{name}". The "coder" agent can list, read and search its files and propose edits (the user approves each change as a diff). Give any step that needs the project's code to "coder"."""

COMPOSE_PROMPT = """Several agents worked on the user's request in steps. Write the final answer for the user from their results.
- Keep everything the user needs complete: code, posts, drafts, tables. Do not shorten them.
- Remove repetition between steps and fix contradictions.
- Do not describe the internal steps or agents; just deliver the result, in the user's language."""


def parse_plan(text: str) -> list[dict]:
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
        agent = get_agent(str(raw.get("agent") or "general")).key
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
    def __init__(self, memory: Memory, router: Router, settings):
        self.memory = memory
        self.router = router
        self.settings = settings
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}
        self._decisions: dict[int, bool] = {}
        self._cancelled: set[int] = set()
        self._threads: dict[int, threading.Thread] = {}
        self._shutdown = threading.Event()

    # ---- public API ----------------------------------------------------------
    def submit(self, request: str, source: str = "text", workspace: str | None = None,
               context: dict | None = None) -> int:
        request = (request or "").strip()
        if not request:
            raise ValueError("الطلب فارغ")
        if len(request) > MAX_REQUEST:
            raise ValueError(f"الطلب أطول من {MAX_REQUEST} حرف")
        if workspace is not None and registry.find(self.settings, workspace) is None:
            raise WorkspaceError(f"المشروع «{workspace}» غير مسموح به")
        task_id = self.memory.create_task(request, source, workspace, context or None)
        self.memory.add_message("user", request, task_id)
        job = Job(request=request, voice=source == "voice", workspace=workspace, context=context or {})
        thread = threading.Thread(target=self._run_safe, args=(task_id, job), name=f"task-{task_id}", daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return task_id

    def decide(self, step_id: int, approve: bool) -> bool:
        step = self.memory.get_step(step_id)
        if not step or step["status"] != "awaiting_approval":
            return False
        with self._lock:
            event = self._events.get(step_id)
            if event is None:
                return False
            self._decisions[step_id] = bool(approve)
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

        try:
            plan = self._plan(request, history, lessons, ws)
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
            agent = get_agent(step["agent"])
            coder_in_project = ws is not None and agent.key == "coder"
            # in a project, the coder's edits are approved as diffs after it works
            if step["needs_confirmation"] and not coder_in_project:
                approved = self._wait_for_approval(task_id, step_id)
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

    def _plan(self, request: str, history: list[dict], lessons: list[dict],
              ws: Workspace | None = None) -> list[dict]:
        if len(request) <= SHORT_REQUEST and "\n" not in request:
            return [{"agent": "coder" if ws else "general", "instruction": request,
                     "needs_confirmation": requires_approval(request, False)}]
        agents = "\n".join(f'- "{a.key}": {a.role}' for a in available_agents())
        rules, outcomes = _lessons_text(lessons)
        lesson_block = ""
        if rules:
            lesson_block += "\nUser's saved rules (keep them in the instructions):\n" + "\n".join(f"- {r}" for r in rules)
        if outcomes:
            lesson_block += "\n" + outcomes
        system = PLANNER_PROMPT.format(agents=agents, max_steps=MAX_STEPS, lessons=lesson_block + "\n")
        if ws:
            system += "\n\n" + WORKSPACE_NOTE.format(name=ws.name)
        messages = [{"role": "system", "content": system}]
        if history:
            context = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
            messages.append({"role": "user", "content": f"Recent conversation for context:\n{context}"})
            messages.append({"role": "assistant", "content": "Understood."})
        messages.append({"role": "user", "content": f"Request:\n{request}\n\nReturn the JSON plan."})
        try:
            completion = self.router.complete("plan", messages, json_mode=True)
            return parse_plan(completion.text)
        except NoModelError:
            raise
        except ValueError as e:
            log.warning("planner output unusable (%s); running as one step", e)
            return [{"agent": "coder" if ws else "general", "instruction": request,
                     "needs_confirmation": requires_approval(request, False)}]

    def _run_coder_step(self, task_id: int, step_id: int, agent, step: dict, request: str,
                        history: list[dict], results: list[tuple[dict, str]], lessons: list[dict],
                        speak: bool, ws: Workspace) -> str | None:
        """Tool loop in the project, then approval of the proposed diffs. Returns the step's output."""
        rules, outcomes = _lessons_text(lessons)
        system = agent.system_prompt(rules, project=ws.name)
        if outcomes:
            system += "\n\n" + outcomes
        if speak:
            system += "\n\n" + VOICE_HINT
        parts = [f"Request:\n{request}"]
        if history:
            recent = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-4:])
            parts.append(f"Recent conversation:\n{recent}")
        for prev_step, text in results:
            parts.append(f"Result of an earlier step ({AGENTS[prev_step['agent']].name}):\n{text[:STEP_RESULT_LIMIT]}")
        if step["instruction"].strip() != request.strip():
            parts.append(f"Your step:\n{step['instruction']}")
        try:
            res = run_coder(self.router, ws, system, "\n\n".join(parts))
        except NoModelError as e:
            self.memory.update_step(step_id, status="failed", error=str(e), finished_at=time.time())
            self.memory.update_task(task_id, status="failed", error=str(e))
            return None
        trace = json.dumps(res.toolbox.trace, ensure_ascii=False)
        output = res.text
        changes = res.toolbox.change_list()
        self.memory.update_step(step_id, output=output, provider=res.provider, model=res.model, trace=trace)
        if changes:
            self.memory.add_changes(task_id, step_id, changes)
            approved = self._wait_for_approval(task_id, step_id)
            pending = self.memory.get_changes(step_id, "pending")
            if approved is None:
                for c in pending:
                    self.memory.update_change(c["id"], "rejected", "أُلغيت المهمة")
                return None
            if approved:
                output += "\n\n" + self._apply_changes(ws, pending)
            else:
                for c in pending:
                    self.memory.update_change(c["id"], "rejected")
                output += "\n\n**التعديلات:** رفضها المستخدم، ولم يُكتب أي ملف."
        self.memory.update_step(step_id, status="done", output=output, finished_at=time.time())
        return output

    def _apply_changes(self, ws: Workspace, pending: list[dict]) -> str:
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
        return "\n".join(lines)

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
            return decision
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

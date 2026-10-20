"""Tools the device agent calls: read this PC's state, and propose safe actions the user approves."""

from __future__ import annotations

from ..device.control import FOLDER_NAMES, MEDIA, SPECIAL_FOLDERS, VOLUME, DeviceError, check_url

MAX_PROPOSALS = 6
ACCOUNT = "هذا الجهاز"

TOOL_HELP = """{"tool": "system_info"}                                    this PC: Windows version, memory, disks, battery, uptime, time
{"tool": "running_apps"}                                   programs running now, by memory use
{"tool": "list_apps"}                                      programs Yousef may open or close
{"tool": "propose_open_app", "app": "chrome"}              open a program from the allowed list
{"tool": "propose_open_url", "url": "https://..."}         open a web page in the default browser
{"tool": "propose_open_folder", "path": "downloads"}       open a folder (desktop, documents, downloads, pictures, music, videos, home, or a full path in the user's home or projects)
{"tool": "propose_open_file", "path": "C:\\\\Users\\\\...\\\\report.pdf"}   open a document, picture or media file in the user's home or projects
{"tool": "propose_volume", "action": "up", "steps": 5}     volume up or down (each step about 2%), or "mute"
{"tool": "propose_media", "action": "play_pause"}          media keys: play_pause, next, previous, stop
{"tool": "propose_close_app", "app": "chrome"}             ask an allowed program to close (it may ask to save)
{"tool": "propose_lock"}                                   lock the screen
{"done": "your answer for the user"}                       finish"""


class DeviceToolbox:
    def __init__(self, device):
        self.device = device  # device.Device
        self.trace: list[dict] = []
        self.proposals: list[dict] = []

    def help(self) -> str:
        return TOOL_HELP

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("app") or call.get("url") or call.get("path") or call.get("action") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise DeviceError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            out = handler(call)
            self.trace.append({"tool": tool, "target": target[:160], "ok": True})
            return out
        except (ValueError, OSError, KeyError, TypeError) as e:
            self.trace.append({"tool": tool, "target": target[:160], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    # ---- reading ------------------------------------------------------------------------
    def tool_system_info(self, call: dict) -> str:
        i = self.device.info()
        gb = lambda x: "-" if x is None else f"{x:.1f} GB"
        lines = [f"OS: {i['os']} | computer: {i['machine']} | time: {i['time']}",
                 f"CPU: {i['cpu']} ({i['cores']} threads)",
                 f"Memory: {gb(i['memory_gb'])} total, {gb(i['memory_free_gb'])} free"
                 + (f" ({i['memory_load']}% in use)" if i.get("memory_load") is not None else "")]
        for d in i.get("drives") or []:
            lines.append(f"Disk {d['drive']}: {d['free_gb']:.0f} GB free of {d['total_gb']:.0f} GB")
        if i.get("battery"):
            lines.append(f"Battery: {i['battery']['percent']}%{' (charging)' if i['battery']['charging'] else ''}")
        if i.get("uptime_hours") is not None:
            lines.append(f"On for {i['uptime_hours']:.1f} hours")
        return "\n".join(lines)

    def tool_running_apps(self, call: dict) -> str:
        procs = self.device.running()[:30]
        if not procs:
            return "The list of running programs is available on Windows only."
        return "program | windows/processes | memory\n" + "\n".join(
            f"{p['name']} | {p['count']} | {p['memory_mb']:.0f} MB" for p in procs)

    def tool_list_apps(self, call: dict) -> str:
        return "\n".join(f'{a["name"]} — {a["label"]}' for a in self.device.apps())

    # ---- proposals ----------------------------------------------------------------------
    def _add(self, kind: str, title: str, rows: list, payload: dict) -> str:
        if len(self.proposals) >= MAX_PROPOSALS:
            raise DeviceError(f"at most {MAX_PROPOSALS} actions per task")
        if any(p["kind"] == kind and {k: v for k, v in p["payload"].items() if k in payload} == payload
               for p in self.proposals):
            return "Already proposed."
        self.proposals.append({"kind": kind, "payload": dict(payload, title=title, account=ACCOUNT, rows=rows,
                                                             warnings=[])})
        return f"Proposed: {title}. It happens only after the user approves."

    def tool_propose_open_app(self, call: dict) -> str:
        app = self.device.app(call.get("app"))
        return self._add("device_open_app", f"فتح {app['label']}", [{"label": "البرنامج", "value": app["label"]}],
                         {"app": app["name"]})

    def tool_propose_open_url(self, call: dict) -> str:
        url = check_url(call.get("url"))
        return self._add("device_open_url", "فتح رابط", [{"label": "الرابط", "value": url},
                                                          {"label": "في", "value": "المتصفح الافتراضي"}], {"url": url})

    def tool_propose_open_folder(self, call: dict) -> str:
        ref = str(call.get("path") or "")
        path = self.device.folder(ref)
        key = FOLDER_NAMES.get(ref.strip(), ref.strip().lower())
        stored = key if key in SPECIAL_FOLDERS else str(path)
        return self._add("device_open_folder", f"فتح المجلد {path.name or path}", [{"label": "المجلد", "value": str(path)}],
                         {"path": stored})

    def tool_propose_open_file(self, call: dict) -> str:
        path = self.device.document(call.get("path"))
        return self._add("device_open_file", f"فتح {path.name}", [{"label": "الملف", "value": str(path)},
                                                                  {"label": "يفتح بـ", "value": "البرنامج المرتبط به في ويندوز"}],
                         {"path": str(path)})

    def tool_propose_volume(self, call: dict) -> str:
        action = str(call.get("action") or "")
        if action not in VOLUME:
            raise DeviceError('action must be "up", "down" or "mute"')
        steps = max(1, min(int(call.get("steps") or 5), 25))
        what = {"up": f"رفع الصوت نحو {steps * 2}%", "down": f"خفض الصوت نحو {steps * 2}%", "mute": "كتم الصوت أو إعادته"}[action]
        return self._add("device_volume", what, [{"label": "الصوت", "value": what}], {"action": action, "steps": steps})

    def tool_propose_media(self, call: dict) -> str:
        action = str(call.get("action") or "")
        if action not in MEDIA:
            raise DeviceError("action must be play_pause, next, previous or stop")
        what = {"play_pause": "تشغيل أو إيقاف مؤقت", "next": "المقطع التالي", "previous": "المقطع السابق",
                "stop": "إيقاف التشغيل"}[action]
        return self._add("device_media", what, [{"label": "الوسائط", "value": what}], {"action": action})

    def tool_propose_close_app(self, call: dict) -> str:
        app = self.device.app(call.get("app"))
        if not app["target"].lower().endswith(".exe") or app["target"].lower() == "explorer.exe":
            raise DeviceError(f"{app['label']} cannot be closed by Yousef")
        return self._add("device_close_app", f"إغلاق {app['label']}",
                         [{"label": "البرنامج", "value": app["label"]},
                          {"label": "كيف", "value": "كأنك ضغطت ×: قد يسألك عن حفظ عملك، ولا يُغلق بالقوة"}],
                         {"app": app["name"]})

    def tool_propose_lock(self, call: dict) -> str:
        return self._add("device_lock", "قفل الشاشة", [{"label": "الإجراء", "value": "قفل ويندوز (تحتاج كلمة المرور للعودة)"}], {})

    def action_list(self) -> list[dict]:
        return list(self.proposals)

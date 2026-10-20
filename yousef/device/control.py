"""Controlling this PC, safely: what the device agent may do, and how each action runs.

Allowed: read system information and the running programs; open a program
from the allowed list, a web link, a folder, or a document under the user's
home; change the volume; play/pause media; close an allowed program
(politely, like clicking X, so it can ask to save); lock the screen.

Never: run commands, open scripts or programs outside the list, delete or
move files, shut down, or type into other programs. Every action except
reading is proposed first and runs only after the user approves it.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_APPS = [
    {"name": "notepad", "label": "المفكرة", "target": "notepad.exe"},
    {"name": "calculator", "label": "الآلة الحاسبة", "target": "calc.exe"},
    {"name": "explorer", "label": "مستكشف الملفات", "target": "explorer.exe"},
    {"name": "paint", "label": "الرسام", "target": "mspaint.exe"},
    {"name": "chrome", "label": "Google Chrome", "target": "chrome.exe"},
    {"name": "edge", "label": "Microsoft Edge", "target": "msedge.exe"},
    {"name": "vscode", "label": "VS Code", "target": "code.exe"},
    {"name": "word", "label": "Word", "target": "winword.exe"},
    {"name": "excel", "label": "Excel", "target": "excel.exe"},
    {"name": "settings", "label": "إعدادات ويندوز", "target": "ms-settings:"},
    {"name": "taskmgr", "label": "مدير المهام", "target": "taskmgr.exe"},
]
SPECIAL_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
                   "music": "Music", "videos": "Videos", "home": ""}
FOLDER_NAMES = {"سطح المكتب": "desktop", "المستندات": "documents", "مستنداتي": "documents", "التنزيلات": "downloads",
                "التحميلات": "downloads", "الصور": "pictures", "الموسيقى": "music", "الفيديو": "videos",
                "الفيديوهات": "videos", "المجلد الشخصي": "home"}
SAFE_DOCUMENTS = {".pdf", ".txt", ".md", ".csv", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods",
                  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".wav", ".mkv", ".mov"}
APP_NAME = re.compile(r"[a-z0-9][a-z0-9_\-]{0,39}")
APP_TARGET = re.compile(r"(?i)(?:[a-z0-9_.\-]{1,60}\.exe|ms-settings:|[a-z]:\\[^<>\"|?*\r\n]{1,250}\.(?:exe|lnk))")
MEDIA = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
VOLUME = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}
SECRET_DIR_NAMES = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "appdata"}


class DeviceError(ValueError):
    pass


# ---- validation (shared by the tools and the executor) ---------------------------------------
def check_url(url: str) -> str:
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c in url for c in "\r\n\"<> "):
        raise DeviceError("الرابط يجب أن يبدأ بـ https:// أو http://")
    return url


def check_app(entry: dict) -> dict:
    name = str(entry.get("name") or "").strip().lower()
    target = str(entry.get("target") or "").strip()
    label = str(entry.get("label") or name).strip()[:60]
    if not APP_NAME.fullmatch(name):
        raise DeviceError("اسم البرنامج: حروف إنجليزية صغيرة وأرقام فقط، مثل photoshop")
    if not APP_TARGET.fullmatch(target):
        raise DeviceError("المسار يجب أن يكون ملف .exe أو اختصار .lnk (مثل C:\\Program Files\\App\\app.exe)")
    return {"name": name, "label": label or name, "target": target}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Device:
    """What the agent and the executor use; the backend does the OS work."""

    def __init__(self, settings, backend=None, home: Path | None = None, workspaces=None):
        self.settings = settings
        self.backend = backend or default_backend()
        self.home = Path(home) if home else Path.home()
        self.workspaces = workspaces  # callable -> list of allowed project folders

    # ---- apps -----------------------------------------------------------------------
    def apps(self) -> list[dict]:
        custom = self.settings.get().get("device", {}).get("apps", [])
        names = {a["name"] for a in custom}
        return [a for a in DEFAULT_APPS if a["name"] not in names] + list(custom)

    def app(self, ref: str) -> dict:
        ref = str(ref or "").strip().lower()
        for a in self.apps():
            if ref in (a["name"], a["label"].lower(), a["target"].lower()):
                return a
        names = ", ".join(a["name"] for a in self.apps())
        raise DeviceError(f'"{ref}" ليس في قائمة البرامج المسموحة ({names}). يضيفه المستخدم من الإعدادات ← التحكم بالجهاز.')

    # ---- places ---------------------------------------------------------------------
    def roots(self) -> list[Path]:
        roots = [self.home]
        if self.workspaces:
            roots += [Path(p) for p in self.workspaces()]
        return [r.resolve() for r in roots]

    def folder(self, ref: str) -> Path:
        ref = str(ref or "").strip()
        key = FOLDER_NAMES.get(ref, ref.lower())
        if key in SPECIAL_FOLDERS:
            path = self.home / SPECIAL_FOLDERS[key] if SPECIAL_FOLDERS[key] else self.home
        else:
            path = Path(os.path.expandvars(os.path.expanduser(ref)))
        path = path.resolve()
        if not path.is_dir():
            raise DeviceError(f"المجلد غير موجود: {path}")
        self._check_place(path)
        return path

    def document(self, ref: str) -> Path:
        path = Path(os.path.expandvars(os.path.expanduser(str(ref or "").strip()))).resolve()
        if not path.is_file():
            raise DeviceError(f"الملف غير موجود: {path}")
        if path.suffix.lower() not in SAFE_DOCUMENTS:
            raise DeviceError(f"لا يفتح يوسف ملفات {path.suffix or 'بلا امتداد'}؛ المستندات والصور والوسائط فقط "
                              "(لا برامج ولا سكربتات)")
        self._check_place(path)
        return path

    def _check_place(self, path: Path) -> None:
        if not any(_inside(path, r) for r in self.roots()):
            raise DeviceError("يفتح يوسف ما في مجلدك الشخصي أو مجلدات مشاريعك فقط")
        parts = {p.lower() for p in path.parts}
        if parts & SECRET_DIR_NAMES:
            raise DeviceError("هذا المجلد فيه إعدادات أو مفاتيح خاصة؛ لا يفتحه يوسف")

    # ---- reading --------------------------------------------------------------------
    def info(self) -> dict:
        return self.backend.system_info()

    def running(self) -> list[dict]:
        return self.backend.running()

    # ---- actions (after approval) ------------------------------------------------------
    def execute(self, kind: str, p: dict) -> str:
        """Run one approved action -> what happened, in Arabic. Re-checks everything first."""
        b = self.backend
        if kind == "device_open_app":
            app = self.app(p["app"])
            b.open(app["target"])
            return f"فُتح {app['label']}"
        if kind == "device_open_url":
            b.open(check_url(p["url"]))
            return f"فُتح الرابط {p['url']}"
        if kind == "device_open_folder":
            path = self.folder(p["path"])
            b.open(str(path))
            return f"فُتح المجلد {path}"
        if kind == "device_open_file":
            path = self.document(p["path"])
            b.open(str(path))
            return f"فُتح الملف {path.name}"
        if kind == "device_volume":
            action, steps = p["action"], max(1, min(int(p.get("steps") or 1), 25))
            if action not in VOLUME:
                raise DeviceError("action: up, down أو mute")
            b.key(VOLUME[action], 1 if action == "mute" else steps)
            return {"up": f"رُفع الصوت {steps * 2}% تقريبًا", "down": f"خُفض الصوت {steps * 2}% تقريبًا",
                    "mute": "كُتم الصوت (أو أُعيد)"}[action]
        if kind == "device_media":
            if p["action"] not in MEDIA:
                raise DeviceError("action: play_pause, next, previous أو stop")
            b.key(MEDIA[p["action"]], 1)
            return {"play_pause": "تشغيل/إيقاف مؤقت", "next": "المقطع التالي", "previous": "المقطع السابق",
                    "stop": "أُوقف التشغيل"}[p["action"]]
        if kind == "device_close_app":
            app = self.app(p["app"])
            if not app["target"].lower().endswith(".exe"):
                raise DeviceError("يُغلق يوسف البرامج التي مسارها .exe فقط")
            if app["target"].lower() == "explorer.exe":
                raise DeviceError("لا يُغلق يوسف مستكشف الملفات (يحمل سطح المكتب وشريط المهام)")
            closed = b.close(Path(app["target"]).name)
            return f"طُلب من {app['label']} الإغلاق؛ قد يسألك عن حفظ عملك" if closed else f"{app['label']} ليس مفتوحًا"
        if kind == "device_lock":
            b.lock()
            return "قُفلت الشاشة"
        raise DeviceError(f"إجراء غير معروف: {kind}")


# ---- backends ----------------------------------------------------------------------------------
class WindowsBackend:
    name = "windows"

    def open(self, target: str) -> None:
        try:
            os.startfile(target)  # ShellExecute: no shell, no arguments
        except OSError as e:
            raise DeviceError(f"تعذّر الفتح: {e.strerror or e}") from None

    def key(self, vk: int, times: int) -> None:
        user32 = ctypes.windll.user32
        for _ in range(times):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
            time.sleep(0.02)

    def lock(self) -> None:
        if not ctypes.windll.user32.LockWorkStation():
            raise DeviceError("تعذّر قفل الشاشة")

    def close(self, image: str) -> bool:
        # without /F Windows asks the program to close, as if the user clicked X
        out = subprocess.run(["taskkill", "/IM", image], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0

    def running(self) -> list[dict]:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        procs: dict[str, dict] = {}
        for line in out.stdout.splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) < 5:
                continue
            name, mem = cols[0], re.sub(r"[^\d]", "", cols[4])
            p = procs.setdefault(name.lower(), {"name": name, "count": 0, "memory_mb": 0.0})
            p["count"] += 1
            p["memory_mb"] += int(mem or 0) / 1024
        return sorted(procs.values(), key=lambda p: p["memory_mb"], reverse=True)

    def system_info(self) -> dict:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        k32 = ctypes.windll.kernel32
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        k32.GlobalMemoryStatusEx(ctypes.byref(mem))
        power = SYSTEM_POWER_STATUS()
        battery = None
        if k32.GetSystemPowerStatus(ctypes.byref(power)) and power.BatteryFlag != 128 and power.BatteryLifePercent != 255:
            battery = {"percent": power.BatteryLifePercent, "charging": power.ACLineStatus == 1}
        drives = []
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if k32.GetDriveTypeW(root) == 3:  # fixed disks only
                    try:
                        u = shutil.disk_usage(root)
                        drives.append({"drive": root, "total_gb": u.total / 1e9, "free_gb": u.free / 1e9})
                    except OSError:
                        pass
        return {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "machine": platform.node(),
                "cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(),
                "memory_gb": mem.ullTotalPhys / 1e9, "memory_free_gb": mem.ullAvailPhys / 1e9,
                "memory_load": mem.dwMemoryLoad, "drives": drives, "battery": battery,
                "uptime_hours": k32.GetTickCount64() / 3_600_000, "time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")}


class GenericBackend:
    """Linux and macOS, for development: opening works, keys and locking are Windows-only."""
    name = "generic"

    def open(self, target: str) -> None:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if not shutil.which(opener):
            raise DeviceError("الفتح يعمل على ويندوز")
        subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def key(self, vk: int, times: int) -> None:
        raise DeviceError("التحكم بالصوت والوسائط متاح على ويندوز فقط")

    def lock(self) -> None:
        raise DeviceError("قفل الشاشة متاح على ويندوز فقط")

    def close(self, image: str) -> bool:
        raise DeviceError("إغلاق البرامج متاح على ويندوز فقط")

    def running(self) -> list[dict]:
        return []

    def system_info(self) -> dict:
        total = free = None
        try:
            meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
            total = int(meminfo["MemTotal"].split()[0]) * 1024 / 1e9
            free = int(meminfo["MemAvailable"].split()[0]) * 1024 / 1e9
        except (OSError, KeyError, ValueError):
            pass
        u = shutil.disk_usage("/")
        return {"os": f"{platform.system()} {platform.release()}", "machine": platform.node(),
                "cpu": platform.processor() or platform.machine(), "cores": os.cpu_count(), "memory_gb": total,
                "memory_free_gb": free, "memory_load": None,
                "drives": [{"drive": "/", "total_gb": u.total / 1e9, "free_gb": u.free / 1e9}], "battery": None,
                "uptime_hours": None, "time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")}


def default_backend():
    return WindowsBackend() if os.name == "nt" else GenericBackend()

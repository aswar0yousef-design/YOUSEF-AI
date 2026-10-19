"""Wires the pieces together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings, data_dir as default_data_dir
from .core import Orchestrator
from .keystore import KeyStore
from .memory import Memory
from .models import Router
from .models.router import default_factory


@dataclass
class App:
    data_dir: Path
    settings: Settings
    keystore: KeyStore
    memory: Memory
    router: Router
    orchestrator: Orchestrator

    def close(self) -> None:
        self.orchestrator.shutdown()
        self.memory.close()


def build_app(data_dir: Path | None = None, factory=None, cipher=None) -> App:
    root = Path(data_dir) if data_dir else default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(root / "settings.json")
    keystore = KeyStore(root / "keys.dat", cipher=cipher)
    memory = Memory(root / "memory.db")
    memory.mark_interrupted()
    router = Router(settings, keystore, factory or default_factory)
    orchestrator = Orchestrator(memory, router, settings)
    return App(root, settings, keystore, memory, router, orchestrator)

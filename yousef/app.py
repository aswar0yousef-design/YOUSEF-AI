"""Wires the pieces together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings, data_dir as default_data_dir
from .core import Orchestrator
from .integrations import Stores
from .integrations.meta_ads import AdAccounts
from .integrations.publisher import SocialPublisher
from .integrations.social import SocialAccounts
from .integrations.stores import make_client
from .keystore import KeyStore
from .media import MediaLibrary
from .memory import Memory
from .models import Router
from .models.router import default_factory
from .voice import VoiceEngine


@dataclass
class App:
    data_dir: Path
    settings: Settings
    keystore: KeyStore
    memory: Memory
    router: Router
    orchestrator: Orchestrator
    voice: VoiceEngine
    stores: Stores
    social: SocialAccounts
    media: MediaLibrary
    publisher: SocialPublisher
    ads: AdAccounts

    def close(self) -> None:
        self.publisher.stop()
        self.orchestrator.shutdown()
        self.memory.close()


def build_app(data_dir: Path | None = None, factory=None, cipher=None, voice_backend=None,
              store_factory=None, meta_api: str | None = None, meta_sleep=None) -> App:
    root = Path(data_dir) if data_dir else default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(root / "settings.json")
    keystore = KeyStore(root / "keys.dat", cipher=cipher)
    memory = Memory(root / "memory.db")
    memory.mark_interrupted()
    router = Router(settings, keystore, factory or default_factory)
    stores = Stores(settings, keystore, store_factory or make_client)
    social = SocialAccounts(settings, keystore, **({"api": meta_api} if meta_api else {}), sleep=meta_sleep)
    media = MediaLibrary(root / "media")
    ads = AdAccounts(settings, keystore, **({"api": meta_api} if meta_api else {}))
    publisher = SocialPublisher(memory, social, media)
    orchestrator = Orchestrator(memory, router, settings, keystore, stores, social=social, media=media,
                                publisher=publisher, ads=ads)
    voice = VoiceEngine(settings, root, backend_factory=voice_backend)
    router.free_gpu = voice.release_gpu  # when Ollama's runner dies, speech gives the graphics card back first
    return App(root, settings, keystore, memory, router, orchestrator, voice, stores, social, media, publisher, ads)

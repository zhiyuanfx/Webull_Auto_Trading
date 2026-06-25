from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, Field

from strategy_desk.domain import AssetClass


class LocalizedText(BaseModel):
    en: str
    zh_cn: str = ""


class StrategyManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    version: str
    entrypoint: str = "strategy:Strategy"
    name: LocalizedText
    description: LocalizedText
    assets: set[AssetClass]
    parameters: dict[str, Any] = Field(default_factory=dict)
    required_events: set[str] = Field(default_factory=lambda: {"quote"})


class StrategyPlugin(BaseModel):
    manifest: StrategyManifest
    directory: Path
    source_hash: str

    model_config = {"arbitrary_types_allowed": True}


class PluginRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self) -> list[StrategyPlugin]:
        if not self.root.exists():
            return []
        plugins: list[StrategyPlugin] = []
        for manifest_path in sorted(self.root.glob("*/strategy.yaml")):
            manifest = StrategyManifest.model_validate(yaml.safe_load(manifest_path.read_text()))
            directory = manifest_path.parent
            hasher = hashlib.sha256()
            for source in sorted([*directory.glob("*.py"), *directory.glob("requirements*.lock")]):
                hasher.update(source.name.encode())
                hasher.update(source.read_bytes())
            hasher.update(manifest_path.read_bytes())
            plugins.append(
                StrategyPlugin(
                    manifest=manifest, directory=directory, source_hash=hasher.hexdigest()[:12]
                )
            )
        return plugins

    def load_class(self, plugin: StrategyPlugin) -> type:
        module_name, class_name = plugin.manifest.entrypoint.split(":", 1)
        module = self._load_module(plugin.directory / f"{module_name}.py", plugin)
        strategy_class = getattr(module, class_name, None)
        if not isinstance(strategy_class, type):
            raise TypeError(f"Entrypoint {plugin.manifest.entrypoint} is not a class")
        return strategy_class

    @staticmethod
    def _load_module(path: Path, plugin: StrategyPlugin) -> ModuleType:
        if not path.is_file():
            raise FileNotFoundError(path)
        name = f"strategy_plugin_{plugin.manifest.id}_{plugin.source_hash}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def validate_all(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for plugin in self.discover():
            try:
                self.load_class(plugin)
                results.append(
                    {"id": plugin.manifest.id, "version": plugin.manifest.version, "status": "ok"}
                )
            except Exception as exc:
                results.append(
                    {
                        "id": plugin.manifest.id,
                        "version": plugin.manifest.version,
                        "status": "error",
                        "message": str(exc),
                    }
                )
        return results

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from app.plugin_runtime.base import PluginMetadata
from app.plugin_runtime.manifest import PluginManifest


class PluginLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredPlugin:
    manifest: PluginManifest
    plugin_class: type[Any]
    directory: Path
    install_type: str


class PluginRegistry:
    def __init__(self, roots: dict[str, Path] | None = None) -> None:
        self._roots = roots or {
            "builtin": Path("plugins/builtin"),
            "private": Path("plugins/private"),
        }
        self._plugins: dict[str, RegisteredPlugin] = {}
        self.errors: dict[str, str] = {}

    def discover(self) -> list[RegisteredPlugin]:
        discovered: dict[str, RegisteredPlugin] = {}
        errors: dict[str, str] = {}
        for install_type, root in self._roots.items():
            if not root.exists():
                continue
            for manifest_path in sorted(root.glob("*/manifest.json")):
                try:
                    plugin = self._load(manifest_path, install_type)
                    if plugin.manifest.id in discovered:
                        raise PluginLoadError(f"duplicate plugin id {plugin.manifest.id}")
                    discovered[plugin.manifest.id] = plugin
                except Exception as exc:
                    errors[str(manifest_path)] = f"{type(exc).__name__}: {exc}"
        self._plugins = discovered
        self.errors = errors
        return list(discovered.values())

    def _load(self, manifest_path: Path, install_type: str) -> RegisteredPlugin:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.model_validate(raw)
        except (OSError, ValueError) as exc:
            raise PluginLoadError(f"invalid manifest: {exc}") from exc
        directory = manifest_path.parent.resolve()
        if directory.name != manifest.id:
            raise PluginLoadError("manifest id must match its directory name")
        module_name, class_name = manifest.entrypoint.split(":", maxsplit=1)
        module_path = directory.joinpath(*module_name.split(".")).with_suffix(".py").resolve()
        if directory not in module_path.parents or not module_path.is_file():
            raise PluginLoadError("entrypoint module is outside the plugin directory")
        package_name = f"_notify_hub_plugins_{manifest.id}"
        package = ModuleType(package_name)
        package.__path__ = [str(directory)]
        sys.modules[package_name] = package
        qualified_name = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(qualified_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginLoadError("could not create module spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(qualified_name, None)
            raise PluginLoadError(f"entrypoint import failed: {type(exc).__name__}: {exc}") from exc
        plugin_class = getattr(module, class_name, None)
        if not isinstance(plugin_class, type):
            raise PluginLoadError("entrypoint must be a plugin class")
        plugin_type: Any = plugin_class
        for member in ("metadata", "config_schema", "run"):
            if not callable(getattr(plugin_type, member, None)):
                raise PluginLoadError(f"entrypoint is missing callable {member}")
        metadata = PluginMetadata.model_validate(plugin_type.metadata())
        if (metadata.id, metadata.name, metadata.version) != (
            manifest.id,
            manifest.name,
            manifest.version,
        ):
            raise PluginLoadError("entrypoint metadata does not match manifest")
        return RegisteredPlugin(manifest, plugin_type, directory, install_type)

    def get(self, plugin_id: str) -> RegisteredPlugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise PluginLoadError(f"plugin {plugin_id!r} is not registered") from exc

    def list(self) -> list[RegisteredPlugin]:
        return list(self._plugins.values())

"""App adapter loading and the safe generic fallback.

The runner owns the Excel lifecycle, evidence, journal, and Agent action
contract.  An adapter only owns behaviour that cannot be inferred safely from
those generic pieces, such as which package to launch or how an app returns to
its module root.

An app does not need an adapter file.  In that case :class:`GenericAdapter` is
used.  The generic adapter deliberately makes no assumptions about visible
labels, coordinates, or module names.  If an app has not declared a root-page
contract, it falls back to a cold launch at a module-group boundary instead of
pretending that an arbitrary page is a valid root.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

import yaml


class AppAdapterError(ValueError):
    """Raised when an app configuration or adapter is invalid."""


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _tuple_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in (_text(item) for item in value) if item)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise AppAdapterError(f"App 配置不可读取: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise AppAdapterError(f"App 配置必须是 YAML 对象: {path}")
    return document


def _resolve_optional_path(value: Any, *, base: Path) -> Path | None:
    text = _text(value)
    if not text:
        return None
    path = Path(text).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def infer_app_slug_from_profile(profile_path: str | Path | None) -> str | None:
    """Read an optional profile slug so ``--profile`` can select the App.

    This is only an inference convenience.  An explicit ``--app`` always has
    precedence, and malformed/missing profiles simply leave the caller's
    default selection unchanged.
    """

    if not profile_path:
        return None
    path = Path(profile_path).expanduser().resolve()
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(document, Mapping):
        return None
    slug = _text(document.get("slug")).casefold()
    return slug if _SLUG_RE.fullmatch(slug) else None


@dataclass(frozen=True)
class AppConfig:
    """Resolved configuration shared by the runner and an adapter."""

    project_root: Path
    slug: str
    app_dir: Path
    app_file: Path | None
    profile_path: Path | None
    packages: tuple[str, ...]
    adapter_file: Path | None
    document: Mapping[str, Any]

    @property
    def aliases(self) -> tuple[str, ...]:
        return _tuple_text(self.document.get("aliases"))

    @property
    def default_source(self) -> Path | None:
        return _resolve_optional_path(
            self.document.get("default_source") or self.document.get("source"),
            base=self.app_dir,
        )

    @property
    def default_output(self) -> Path | None:
        return _resolve_optional_path(
            self.document.get("default_output") or self.document.get("output"),
            base=self.project_root,
        )

    def manifest_context(self, *, adapter_name: str) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "aliases": list(self.aliases),
            "app_file": str(self.app_file) if self.app_file else "",
            "profile_file": str(self.profile_path) if self.profile_path else "",
            "packages": list(self.packages),
            "adapter_file": str(self.adapter_file) if self.adapter_file else "",
            "adapter": adapter_name,
        }


def load_app_config(
    project_root: str | Path,
    app_slug: str = "generic",
    *,
    profile_path: str | Path | None = None,
) -> AppConfig:
    """Load ``apps/<slug>/app.yaml`` without requiring a custom adapter.

    A missing ``app.yaml`` is allowed for the reserved ``generic`` profile so
    callers can use the generic adapter in a small standalone harness.  A
    named app must have its declared app directory/configuration; silently
    falling back to another app would make device execution unsafe.
    """

    root = Path(project_root).expanduser().resolve()
    slug = _text(app_slug).casefold()
    if not _SLUG_RE.fullmatch(slug):
        raise AppAdapterError(f"非法 App slug: {app_slug!r}")

    app_dir = root / "apps" / slug
    app_file = app_dir / "app.yaml"
    if app_file.is_file():
        document = _read_yaml(app_file)
        declared_slug = _text(document.get("slug")).casefold()
        if declared_slug and declared_slug != slug:
            raise AppAdapterError(
                f"App 配置 slug 与目录不一致: 目录={slug!r}, 配置={declared_slug!r}"
            )
    elif slug != "generic":
        raise AppAdapterError(f"未找到 App 配置: {app_file}")
    else:
        document = {"slug": "generic", "packages": []}
        app_file = None

    packages = _tuple_text(document.get("packages") or document.get("package"))
    resolved_profile = (
        Path(profile_path).expanduser().resolve()
        if profile_path
        else _resolve_optional_path(document.get("profile"), base=app_dir)
    )
    if resolved_profile is None and (app_dir / "profile.yaml").is_file():
        resolved_profile = (app_dir / "profile.yaml").resolve()

    adapter_ref = document.get("adapter")
    adapter_file = _resolve_optional_path(adapter_ref, base=app_dir)
    if adapter_file is None:
        candidate = app_dir / "adapter.py"
        if candidate.is_file():
            adapter_file = candidate.resolve()

    return AppConfig(
        project_root=root,
        slug=slug,
        app_dir=app_dir,
        app_file=app_file.resolve() if app_file else None,
        profile_path=resolved_profile,
        packages=packages,
        adapter_file=adapter_file,
        document=document,
    )


class AppAdapter:
    """Minimal adapter contract.

    ``runtime`` is intentionally a small duck-typed facade supplied by the
    runner.  Keeping the facade outside this module prevents adapters from
    importing the monolithic runner and makes the same adapter usable by a
    different executor in the future.
    """

    name = "base"
    supports_legacy_deterministic = False

    def __init__(self, config: AppConfig, runtime: Any = None) -> None:
        self.config = config
        self.runtime = runtime

    @property
    def slug(self) -> str:
        return self.config.slug

    @property
    def probe_canaries(self) -> tuple[tuple[str, int], ...]:
        return ()

    def _runtime(self) -> Any:
        if self.runtime is None:
            raise AppAdapterError(f"{self.name} adapter 尚未绑定执行器 runtime")
        return self.runtime

    def launch(self, events: list[dict]) -> bool:
        """Launch the app package(s) and leave module entry to the Agent."""

        return self._runtime().launch_packages(events, self.config.packages)

    def is_module_root(self, sheet_name: str, elements: list[dict]) -> bool:
        """Return whether a declared module-root contract is satisfied."""

        del sheet_name, elements
        return False

    def soft_reset_to_module_root(self, events: list[dict], sheet_name: str) -> bool:
        """Try a bounded back sequence; fail closed without a root contract."""

        runtime = self._runtime()
        elements = runtime.screen_elements()
        if self.is_module_root(sheet_name, elements):
            return True
        for attempt in range(runtime.max_soft_back):
            if not runtime.key_back(events):
                return False
            elements = runtime.screen_elements()
            if self.is_module_root(sheet_name, elements):
                runtime.event(
                    events,
                    "reset",
                    f"{sheet_name}模块根页面",
                    "success",
                    f"通用 adapter 返回次数={attempt + 1}",
                )
                return True
        runtime.event(
            events,
            "reset",
            f"{sheet_name}模块根页面",
            "failed",
            "通用 adapter 未声明可验证的根页面契约",
        )
        return False

    def enter_module(self, events: list[dict], sheet_name: str) -> bool:
        """Optional module-entry hook; generic apps use Agent navigation."""

        runtime = self._runtime()
        runtime.event(
            events,
            "module_entry",
            sheet_name,
            "success",
            "通用 adapter 不假设固定入口，由 Agent action plan 负责导航",
        )
        return True

    def tap_market_tab(self, events: list[dict], sheet_name: str) -> bool:
        del events, sheet_name
        return False

    def tap_fund_tab(self, events: list[dict], tab_name: str) -> bool:
        del events, tab_name
        return False

    def enter_market_home(self, events: list[dict], sheet_name: str) -> bool:
        return self.enter_module(events, sheet_name)

    def legacy_module(self):
        """Optional module containing an App's legacy compatibility code."""

        return None


class GenericAdapter(AppAdapter):
    """Safe fallback for apps without a specialized adapter file."""

    name = "generic"

    @property
    def probe_canaries(self) -> tuple[tuple[str, int], ...]:
        raw = self.config.document.get("probe_canaries")
        if not isinstance(raw, (list, tuple)):
            return ()
        result: list[tuple[str, int]] = []
        for item in raw:
            if isinstance(item, Mapping) and item.get("sheet") is not None:
                sheet, row = item.get("sheet"), item.get("row")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                sheet, row = item
            else:
                continue
            try:
                result.append((_text(sheet), int(row)))
            except (TypeError, ValueError):
                continue
        return tuple((sheet, row) for sheet, row in result if sheet and row > 0)

    def _root_contract(self, sheet_name: str) -> Mapping[str, Any] | None:
        roots = self.config.document.get("module_roots")
        if not isinstance(roots, Mapping):
            return None
        contract = roots.get(sheet_name) or roots.get("*")
        return contract if isinstance(contract, Mapping) else None

    @staticmethod
    def _match_value(elements: Iterable[Mapping[str, Any]], value: Any, *, resource_id: bool) -> bool:
        expected = _text(value)
        if not expected:
            return False
        for element in elements:
            actual = _text(element.get("id" if resource_id else "text"))
            desc = _text(element.get("desc"))
            actual_short = actual.rsplit("/", 1)[-1]
            expected_short = expected.rsplit("/", 1)[-1]
            if expected in {actual, actual_short} or expected == desc or (
                resource_id and expected_short == actual_short
            ):
                return True
            if not resource_id and (expected in actual or expected in desc):
                return True
        return False

    def is_module_root(self, sheet_name: str, elements: list[dict]) -> bool:
        contract = self._root_contract(sheet_name)
        if not contract:
            return False
        if any(
            not self._match_value(elements, value, resource_id=False)
            for value in contract.get("all_text", [])
        ):
            return False
        if any(
            not self._match_value(elements, value, resource_id=True)
            for value in contract.get("all_ids", [])
        ):
            return False
        any_text = contract.get("any_text", [])
        if any_text and not any(self._match_value(elements, value, resource_id=False) for value in any_text):
            return False
        any_ids = contract.get("any_ids", [])
        if any_ids and not any(self._match_value(elements, value, resource_id=True) for value in any_ids):
            return False
        return True


def _load_adapter_module(path: Path, slug: str) -> ModuleType:
    module_name = f"sixgill_app_adapter_{slug}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AppAdapterError(f"无法加载 App adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - exact import error is app-specific
        sys.modules.pop(module_name, None)
        raise AppAdapterError(f"App adapter 加载失败: {path}: {exc}") from exc
    return module


def load_app_adapter(config: AppConfig, runtime: Any = None) -> AppAdapter:
    """Instantiate the declared adapter, or the generic fallback."""

    if config.adapter_file is None:
        return GenericAdapter(config, runtime)
    if not config.adapter_file.is_file():
        raise AppAdapterError(f"App adapter 文件不存在: {config.adapter_file}")

    module = _load_adapter_module(config.adapter_file, config.slug)
    factory = getattr(module, "create_adapter", None)
    if callable(factory):
        adapter = factory(config, runtime)
    else:
        adapter_class = getattr(module, "Adapter", None)
        if adapter_class is None:
            adapter_class = next(
                (
                    value
                    for value in vars(module).values()
                    if isinstance(value, type)
                    and issubclass(value, AppAdapter)
                    and value is not AppAdapter
                    and value.__module__ == module.__name__
                ),
                None,
            )
        if adapter_class is None:
            raise AppAdapterError(f"adapter 未提供 create_adapter 或 AppAdapter 子类: {config.adapter_file}")
        adapter = adapter_class(config, runtime)
    if not isinstance(adapter, AppAdapter):
        raise AppAdapterError(f"adapter 必须继承 AppAdapter: {config.adapter_file}")
    return adapter

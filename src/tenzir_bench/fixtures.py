"""Fixture runtime for benchmark execution."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import inspect
import json
import logging
import sys
import types
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .definitions import BenchmarkDefinition

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """Pair a fixture name with optional structured options."""

    name: str
    options: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.name, json.dumps(self.options, sort_keys=True)))

    def __str__(self) -> str:
        if not self.options:
            return self.name
        items = ", ".join(f"{key}={value!r}" for key, value in sorted(self.options.items()))
        return f"{self.name}({items})"


@dataclass(frozen=True)
class FixtureContext:
    """Describe the benchmark context available to fixture factories."""

    definition: BenchmarkDefinition
    dataset_path: Path
    output_root: Path
    env: Mapping[str, str]
    fixture_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FixtureHandle:
    """Container for fixture environment, teardown, and optional hooks."""

    env: dict[str, str] | None = None
    teardown: Callable[[], None] | None = None
    hooks: Mapping[str, Callable[..., Any]] | None = None


class FixtureUnavailable(Exception):
    """Raised when a fixture cannot be provided in the current environment."""


@dataclass(frozen=True, slots=True)
class FixtureSelection:
    """Read-only view of the currently active fixtures."""

    names: frozenset[str]

    def __iter__(self) -> Iterator[str]:
        return iter(self.names)

    def __bool__(self) -> bool:
        return bool(self.names)

    def __contains__(self, item: str) -> bool:
        return item in self.names

    def __getattr__(self, item: str) -> bool:
        if item in self.names:
            return True
        available = ", ".join(sorted(self.names)) or "<none>"
        raise AttributeError(f"fixture '{item}' was not requested; available fixtures: {available}")

    def require(self, *names: str) -> None:
        missing = [name for name in names if name not in self.names]
        if missing:
            available = ", ".join(sorted(self.names)) or "<none>"
            missing_list = ", ".join(sorted(missing))
            raise RuntimeError(
                f"Missing required fixture(s): {missing_list} (available: {available})",
            )


_CONTEXT: ContextVar[FixtureContext | None] = ContextVar("tenzir_bench_fixture_context", default=None)
_ACTIVE_SELECTION: ContextVar[frozenset[str]] = ContextVar(
    "tenzir_bench_active_fixtures",
    default=frozenset(),
)
_ACTIVE_CONTROLLERS: ContextVar[dict[str, "FixtureController"]] = ContextVar(
    "tenzir_bench_active_fixture_controllers",
    default={},
)
_OPTIONS_CLASSES: dict[str, type] = {}


def current_context() -> FixtureContext | None:
    """Return the active benchmark fixture context."""

    return _CONTEXT.get()


def push_context(context: FixtureContext) -> Token[FixtureContext | None]:
    """Install fixture context for the duration of a benchmark run."""

    return _CONTEXT.set(context)


def pop_context(token: Token[FixtureContext | None]) -> None:
    """Restore the previous fixture context."""

    _CONTEXT.reset(token)


def fixtures() -> FixtureSelection:
    """Return the names of the currently active fixtures."""

    return FixtureSelection(_ACTIVE_SELECTION.get())


def has(name: str) -> bool:
    """Check whether the named fixture is active."""

    return name in fixtures()


def require(*names: str) -> None:
    """Assert that all given fixtures are active."""

    fixtures().require(*names)


def _unwrap_optional(tp: Any) -> Any:
    origin = typing.get_origin(tp)
    if origin not in (typing.Union, types.UnionType):
        return tp
    non_none_args = [arg for arg in typing.get_args(tp) if arg is not type(None)]
    return non_none_args[0] if len(non_none_args) == 1 else tp


def _instantiate_options(cls: type, data: Mapping[str, Any]) -> Any:
    try:
        hints = typing.get_type_hints(cls)
    except NameError:
        hints = {}
    processed: dict[str, Any] = {}
    for key, value in data.items():
        raw_type = hints.get(key)
        field_type = _unwrap_optional(raw_type) if raw_type is not None else None
        if (
            field_type is not None
            and isinstance(field_type, type)
            and dataclasses.is_dataclass(field_type)
            and isinstance(value, Mapping)
        ):
            processed[key] = _instantiate_options(field_type, value)
        else:
            processed[key] = value
    return cls(**processed)


def get_options_class(name: str) -> type | None:
    """Return the registered options class for the named fixture, if any."""

    return _OPTIONS_CLASSES.get(name)


def current_options(name: str) -> Any:
    """Return typed or raw options for the named fixture from the active context."""

    ctx = _CONTEXT.get()
    if ctx is None:
        return {}
    if name in ctx.fixture_options:
        return ctx.fixture_options[name]
    options_cls = _OPTIONS_CLASSES.get(name)
    if options_cls is None:
        return {}
    return _instantiate_options(options_cls, {})


class _FactoryCallable(Protocol):
    def __call__(
        self,
    ) -> (
        AbstractContextManager[dict[str, str] | None]
        | FixtureHandle
        | dict[str, str]
        | tuple[dict[str, str] | None, Callable[[], None] | None]
        | None
    ): ...


FixtureFactory = Callable[[], AbstractContextManager[dict[str, str] | None]]
_FACTORIES: dict[str, FixtureFactory] = {}


def _attach_hooks(
    manager: AbstractContextManager[dict[str, str] | None],
    hooks: Mapping[str, Callable[..., Any]] | None = None,
) -> AbstractContextManager[dict[str, str] | None]:
    setattr(manager, "__tenzir_bench_fixture_hooks__", dict(hooks or {}))
    return manager


def _normalize_factory(factory: _FactoryCallable) -> FixtureFactory:
    def _as_context_manager() -> AbstractContextManager[dict[str, str] | None]:
        result = factory()
        if isinstance(result, AbstractContextManager):
            return _attach_hooks(result)
        if isinstance(result, FixtureHandle):
            env_dict = result.env or {}

            @contextmanager
            def _ctx() -> Iterator[dict[str, str] | None]:
                try:
                    yield env_dict
                finally:
                    if result.teardown:
                        result.teardown()

            return _attach_hooks(_ctx(), result.hooks)
        if isinstance(result, tuple) and len(result) == 2:
            raw_env, teardown = result
            env_dict = raw_env or {}

            @contextmanager
            def _ctx_tuple() -> Iterator[dict[str, str] | None]:
                try:
                    yield env_dict
                finally:
                    if callable(teardown):
                        teardown()

            return _attach_hooks(_ctx_tuple())
        if result is None:

            @contextmanager
            def _ctx_none() -> Iterator[dict[str, str] | None]:
                yield {}

            return _attach_hooks(_ctx_none())
        if isinstance(result, dict):

            @contextmanager
            def _ctx_dict() -> Iterator[dict[str, str] | None]:
                yield result

            return _attach_hooks(_ctx_dict())
        raise TypeError(
            "fixture factory must return a context manager, FixtureHandle, dict, "
            "tuple[env, teardown], or None",
        )

    return _as_context_manager


def register(
    name: str | None,
    factory: _FactoryCallable,
    *,
    replace: bool = False,
    options: type | None = None,
) -> None:
    resolved_name = _infer_name(factory, name)
    if resolved_name in _FACTORIES and not replace:
        raise ValueError(f"fixture '{resolved_name}' already registered")
    if options is not None:
        if not isinstance(options, type) or not dataclasses.is_dataclass(options):
            raise TypeError(
                f"'options' for fixture '{resolved_name}' must be a dataclass type, "
                f"got {type(options).__name__}",
            )
        _OPTIONS_CLASSES[resolved_name] = options
    _FACTORIES[resolved_name] = _normalize_factory(factory)


def fixture(
    func: _FactoryCallable | None = None,
    *,
    name: str | None = None,
    replace: bool = False,
    options: type | None = None,
) -> Callable[[_FactoryCallable], _FactoryCallable] | _FactoryCallable:
    """Decorator that registers a benchmark fixture factory."""

    def _decorator(inner: _FactoryCallable) -> _FactoryCallable:
        candidate: _FactoryCallable
        if inspect.isgeneratorfunction(inner):
            candidate = contextmanager(inner)
        else:
            candidate = inner
        register(name, candidate, replace=replace, options=options)
        return inner

    if func is not None:
        return _decorator(func)
    return _decorator


def _infer_name(func: Callable[..., object], explicit: str | None) -> str:
    if explicit:
        return explicit
    code_obj = getattr(func, "__code__", None)
    if code_obj is not None and hasattr(code_obj, "co_filename"):
        return Path(code_obj.co_filename).stem
    name_attr = getattr(func, "__name__", None)
    if isinstance(name_attr, str):
        return name_attr
    raise ValueError("Unable to infer fixture name; please provide one explicitly")


class FixtureController:
    """Imperative controller for an active fixture."""

    def __init__(self, name: str, factory: FixtureFactory) -> None:
        self.name = name
        self._factory = factory
        self._state: tuple[AbstractContextManager[dict[str, str] | None], dict[str, str]] | None = None
        self._hooks: dict[str, Callable[..., Any]] = {}

    def start(self) -> dict[str, str]:
        if self._state is not None:
            raise RuntimeError(f"fixture '{self.name}' is already running")
        context = self._factory()
        env = context.__enter__() or {}
        hooks = getattr(context, "__tenzir_bench_fixture_hooks__", {}) or {}
        self._hooks = {
            hook_name: self._wrap_hook(hook_name, hook)
            for hook_name, hook in hooks.items()
            if callable(hook)
        }
        self._state = (context, env)
        return env

    def stop(self) -> None:
        if self._state is None:
            return
        context, _env = self._state
        self._state = None
        try:
            context.__exit__(None, None, None)
        finally:
            self._hooks.clear()

    def _wrap_hook(self, hook_name: str, hook: Callable[..., Any]) -> Callable[..., Any]:
        def _inner(*args: Any, **kwargs: Any) -> Any:
            if self._state is None:
                raise RuntimeError(
                    f"cannot call '{hook_name}' on fixture '{self.name}' because it is not running",
                )
            return hook(*args, **kwargs)

        return _inner


def invoke_active_hook(
    hook_name: str,
    **kwargs: Any,
) -> None:
    """Invoke a named hook on every currently active fixture that exposes it."""

    controllers = _ACTIVE_CONTROLLERS.get()
    for fixture_name, controller in controllers.items():
        hook = controller._hooks.get(hook_name)
        if hook is None:
            continue
        hook(fixture=fixture_name, **kwargs)


def _build_fixture_options(specs: tuple[FixtureSpec, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for spec in specs:
        options_cls = _OPTIONS_CLASSES.get(spec.name)
        if options_cls is not None:
            try:
                result[spec.name] = _instantiate_options(options_cls, spec.options)
            except TypeError as exc:
                raise ValueError(f"invalid options for fixture '{spec.name}': {exc}") from exc
            continue
        if spec.options:
            result[spec.name] = spec.options
    return result


@contextmanager
def activate(names: Iterable[FixtureSpec | str]) -> Iterator[dict[str, str]]:
    """Activate the requested fixtures and yield their combined environment."""

    specs = _coerce_specs(names)
    selection_token = _ACTIVE_SELECTION.set(frozenset(spec.name for spec in specs))
    context_token = _push_fixture_options_context(specs)
    controller_token = _ACTIVE_CONTROLLERS.set({})
    stack = ExitStack()
    combined: dict[str, str] = {}
    try:
        controllers = _ACTIVE_CONTROLLERS.get()
        for spec in specs:
            factory = _FACTORIES.get(spec.name)
            if factory is None:
                available = ", ".join(sorted(_FACTORIES)) or "<none>"
                raise ValueError(
                    f"fixture '{spec.name}' is not registered (available: {available})",
                )
            controller = FixtureController(spec.name, factory)
            controllers[spec.name] = controller
            env = controller.start()
            stack.callback(controller.stop)
            if env:
                combined.update(env)
        yield combined
    finally:
        stack.close()
        _ACTIVE_CONTROLLERS.reset(controller_token)
        if context_token is not None:
            _CONTEXT.reset(context_token)
        _ACTIVE_SELECTION.reset(selection_token)


def _coerce_specs(items: Iterable[FixtureSpec | str]) -> tuple[FixtureSpec, ...]:
    result: list[FixtureSpec] = []
    for item in items:
        if isinstance(item, str):
            result.append(FixtureSpec(name=item))
        else:
            result.append(item)
    return tuple(result)


def _push_fixture_options_context(
    specs: tuple[FixtureSpec, ...],
) -> Token[FixtureContext | None] | None:
    ctx = _CONTEXT.get()
    if ctx is None:
        return None
    existing_options = ctx.fixture_options
    merged = dict(existing_options)
    merged.update(_build_fixture_options(specs))
    if merged == existing_options:
        return None
    return _CONTEXT.set(dataclasses.replace(ctx, fixture_options=merged))


_LOADED_FIXTURE_MODULES: set[Path] = set()


def load_fixture_modules(benchmark_path: Path, *, root: Path | None = None) -> None:
    """Load nearby ``fixtures.py`` modules for the given benchmark path."""

    resolved_root = (root or Path.cwd()).resolve()
    benchmark_dir = benchmark_path.resolve().parent
    try:
        relative = benchmark_dir.relative_to(resolved_root)
    except ValueError:
        search_dirs = [benchmark_dir]
    else:
        search_dirs = [resolved_root]
        current = resolved_root
        for part in relative.parts:
            current = current / part
            search_dirs.append(current)

    for directory in search_dirs:
        candidate = directory / "fixtures.py"
        if not candidate.exists():
            continue
        resolved_candidate = candidate.resolve()
        if resolved_candidate in _LOADED_FIXTURE_MODULES:
            continue
        module_name = (
            "_tenzir_bench_fixture_"
            + hashlib.sha256(str(resolved_candidate).encode("utf-8")).hexdigest()[:12]
        )
        spec = importlib.util.spec_from_file_location(module_name, resolved_candidate)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load fixture module from {resolved_candidate}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        _LOADED_FIXTURE_MODULES.add(resolved_candidate)


__all__ = [
    "FixtureContext",
    "FixtureHandle",
    "FixtureSelection",
    "FixtureSpec",
    "FixtureUnavailable",
    "activate",
    "current_context",
    "current_options",
    "fixture",
    "fixtures",
    "get_options_class",
    "has",
    "invoke_active_hook",
    "load_fixture_modules",
    "pop_context",
    "push_context",
    "register",
    "require",
]

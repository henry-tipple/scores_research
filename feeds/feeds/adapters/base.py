"""Adapter interface. A provider adapter only has to answer two questions:

* which fixtures exist in a time window (so the poller knows when to speed up), and
* which normalised events happened in a time window.

Adapters must be pure readers: no writes, no global state. The poller does the storing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, Protocol, runtime_checkable

from ..model import Fixture, NormalizedEvent


@runtime_checkable
class FeedAdapter(Protocol):
    name: str

    def fixtures(self, start: datetime, end: datetime) -> list[Fixture]: ...

    def events(self, since: datetime, until: datetime) -> Iterable[NormalizedEvent]: ...


_REGISTRY: dict[str, Callable[..., FeedAdapter]] = {}


def register(name: str):
    def deco(factory: Callable[..., FeedAdapter]):
        _REGISTRY[name] = factory
        return factory
    return deco


def get_adapter(name: str, **config) -> FeedAdapter:
    # import for side effect: adapters register themselves
    from . import fake, sportradar  # noqa: F401
    if name not in _REGISTRY:
        raise KeyError(f"unknown feed adapter {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**config)

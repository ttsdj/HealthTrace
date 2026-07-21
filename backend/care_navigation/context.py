"""Request-scoped location authorization.

Precise coordinates are deliberately kept out of chat history and long-term
memory. Agent tools can only read coordinates that the authenticated request
placed in this context.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class LocationContext:
    authorized: bool = False
    latitude: float | None = None
    longitude: float | None = None
    accuracy_meters: float | None = None

    @property
    def usable(self) -> bool:
        return (
            self.authorized
            and self.latitude is not None
            and self.longitude is not None
            and -90 <= self.latitude <= 90
            and -180 <= self.longitude <= 180
        )

    @classmethod
    def from_value(cls, value: dict | None) -> "LocationContext":
        if not value:
            return cls()
        return cls(
            authorized=bool(value.get("authorized")),
            latitude=_optional_float(value.get("latitude")),
            longitude=_optional_float(value.get("longitude")),
            accuracy_meters=_optional_float(value.get("accuracy_meters")),
        )


@dataclass
class CareNavigationCallState:
    calls: int = 0


def _optional_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_LOCATION_CONTEXT: ContextVar[LocationContext] = ContextVar(
    "healthtrace_location_context",
    default=LocationContext(),
)
_CARE_NAVIGATION_CALLS: ContextVar[CareNavigationCallState] = ContextVar(
    "healthtrace_care_navigation_calls",
    default=CareNavigationCallState(),
)


def get_location_context() -> LocationContext:
    return _LOCATION_CONTEXT.get()


def try_acquire_care_navigation_call() -> bool:
    state = _CARE_NAVIGATION_CALLS.get()
    if state.calls >= 1:
        return False
    state.calls += 1
    return True


@contextmanager
def use_location_context(value: dict | LocationContext | None) -> Iterator[LocationContext]:
    context = value if isinstance(value, LocationContext) else LocationContext.from_value(value)
    location_token = _LOCATION_CONTEXT.set(context)
    call_token = _CARE_NAVIGATION_CALLS.set(CareNavigationCallState())
    try:
        yield context
    finally:
        _CARE_NAVIGATION_CALLS.reset(call_token)
        _LOCATION_CONTEXT.reset(location_token)

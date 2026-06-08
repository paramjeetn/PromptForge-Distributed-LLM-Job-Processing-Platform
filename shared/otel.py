# OTel stub — wired up fully in Phase 10.
# All services import from here so Phase 10 only needs to touch this file.

from contextlib import contextmanager
from typing import Any


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs):
        yield _NoopSpan()


class _NoopMeter:
    def create_counter(self, name: str, **kwargs):
        class _Noop:
            def add(self, amount, attributes=None):
                pass
        return _Noop()

    def create_histogram(self, name: str, **kwargs):
        class _Noop:
            def record(self, amount, attributes=None):
                pass
        return _Noop()

    def create_gauge(self, name: str, **kwargs):
        class _Noop:
            def set(self, amount, attributes=None):
                pass
        return _Noop()


def get_tracer(name: str) -> _NoopTracer:
    return _NoopTracer()


def get_meter(name: str) -> _NoopMeter:
    return _NoopMeter()

"""Shared route-registration helpers for the aiohttp application."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


RouteDefinition = tuple[str, str, Callable[..., Any]]


def register_routes(app: Any, cors: Any, routes: Iterable[RouteDefinition]) -> None:
    """Register routes grouped by path so each resource gets one CORS wrapper."""
    route_groups: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
    for method, path, handler in routes:
        route_groups.setdefault(path, []).append((method, handler))

    for path, method_handlers in route_groups.items():
        resource = cors.add(app.router.add_resource(path))
        for method, handler in method_handlers:
            cors.add(resource.add_route(method, handler))

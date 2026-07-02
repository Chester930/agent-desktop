"""
routes/__init__.py

Exports all route handler registration functions.
Each sub-module registers its own routes onto the aiohttp app.
"""

from .agents import register_agent_routes, register_skill_routes
from .teams  import register_team_routes

__all__ = [
    "register_agent_routes",
    "register_skill_routes",
    "register_team_routes",
]

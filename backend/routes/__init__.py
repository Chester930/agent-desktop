"""
routes/__init__.py

Exports all route handler registration functions.
Each sub-module registers its own routes onto the aiohttp app.
"""

from .agents import register_agent_routes, register_skill_routes
from .teams  import register_team_routes
from .team_planning import register_team_planning_routes
from .mcp_servers import register_mcp_server_routes
from .engine_routes import register_engine_routes
from .resource_sync import register_resource_sync_routes

__all__ = [
    "register_agent_routes",
    "register_skill_routes",
    "register_team_routes",
    "register_team_planning_routes",
    "register_mcp_server_routes",
    "register_engine_routes",
    "register_resource_sync_routes",
]

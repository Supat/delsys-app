"""macOS/Linux/Windows client for the Delsys Trigno SDK server (TCP)."""

from .client import CommandClient, TrignoError
from .sensors import SystemInfo, query_system
from .session import TrignoSession

__all__ = ["CommandClient", "TrignoError", "SystemInfo", "query_system", "TrignoSession"]

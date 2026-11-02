"""ASEP-AI Agent package.

Provides CLI dispatcher and Hermes native skill interfaces.
"""

from typing import List, Optional


def dispatch_command(cmd_string: str, db_path: Optional[str] = None) -> str:
    """Zero-token command router for deterministic slash commands."""
    from src.agent.cli import dispatch_command as _dispatch

    return _dispatch(cmd_string, db_path=db_path)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    from src.agent.cli import main as _main

    return _main(argv)


__all__ = ["dispatch_command", "main"]

"""Raybot agent: a local, tool-using Python tutor built on Ollama."""

from .agent import MODEL, OllamaUnavailable, run_turn

__all__ = ["run_turn", "OllamaUnavailable", "MODEL"]

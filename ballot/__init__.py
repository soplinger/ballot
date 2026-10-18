"""Offline ranked-choice vote counting from plain-text ballot files."""

from .core import METHOD_NAMES, METHODS, Ballot, Election, compare, count, parse

__version__ = "1.0.0"
__all__ = ["Ballot", "Election", "METHODS", "METHOD_NAMES", "compare", "count", "parse"]

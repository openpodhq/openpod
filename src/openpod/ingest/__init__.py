"""Ingestion: turn a link/feed/file into a :class:`~openpod.models.SourceRef`
and a :class:`~openpod.models.Transcript`."""

from __future__ import annotations

from .resolve import resolve, detect_kind

__all__ = ["resolve", "detect_kind"]

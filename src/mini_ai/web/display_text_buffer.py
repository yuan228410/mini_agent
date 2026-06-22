"""Buffered text streaming helpers for WebDisplay."""
from __future__ import annotations

import asyncio
from collections.abc import Callable


class WebTextBuffer:
    """Coalesce high-frequency text chunks before sending Web events."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop, emit_text: Callable[[str], None], flush_ms: int = 40, max_chars: int = 512):
        self.loop = loop
        self.emit_text = emit_text
        self.flush_ms = self._normalize_flush_ms(flush_ms)
        self.max_chars = self._normalize_max_chars(max_chars)
        self.pending_text = ""
        self.stream_text = ""
        self.streaming = False
        self.flush_scheduled = False

    def configure(self, *, flush_ms: int | None = None, max_chars: int | None = None) -> None:
        if flush_ms is not None:
            self.flush_ms = self._normalize_flush_ms(flush_ms)
        if max_chars is not None:
            self.max_chars = self._normalize_max_chars(max_chars)

    @staticmethod
    def _normalize_flush_ms(value: int) -> int:
        return max(0, int(value))

    @staticmethod
    def _normalize_max_chars(value: int) -> int:
        return max(1, int(value))

    def add_chunk(self, text: str, *, suppress_text: bool = False) -> None:
        """Accumulate stream text and emit buffered visible text when needed."""

        self.stream_text += text
        self.streaming = True
        if suppress_text:
            return
        self.pending_text += text
        if len(self.pending_text) >= self.max_chars or self.flush_ms == 0:
            self.flush()
        else:
            self.schedule_flush()

    def end(self, full_text: str | None = None, *, suppress_text: bool = False) -> str:
        """Finish a text stream and return the accumulated streamed text."""

        self.flush()
        saved_text = self.stream_text
        should_emit = bool(full_text) and not suppress_text and (not saved_text or full_text != saved_text)
        if should_emit:
            self.stream_text = ""
            self.streaming = True
            self.emit_text(full_text or "")
        self.stream_text = ""
        self.streaming = False
        return saved_text

    def schedule_flush(self) -> None:
        if self.flush_scheduled:
            return
        self.flush_scheduled = True
        delay = self.flush_ms / 1000
        try:
            self.loop.call_soon_threadsafe(self.loop.call_later, delay, self.flush)
        except Exception:
            self.flush_scheduled = False
            self.flush()

    def flush(self) -> None:
        text = self.pending_text
        self.pending_text = ""
        self.flush_scheduled = False
        if text:
            self.emit_text(text)

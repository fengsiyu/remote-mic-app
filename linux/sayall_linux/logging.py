"""Sanitized, size-bounded runtime logging for the Linux client."""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional


class RuntimeLogger:
    MAX_BYTES = 1024 * 1024

    def __init__(self, path: Path, echo: bool = False) -> None:
        self.path = path
        self.echo = echo
        self._lock = threading.Lock()
        self._file = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._open()
        except OSError:
            self._file = None

    def _open(self) -> None:
        descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.chmod(str(self.path), 0o600)
        except OSError:
            pass
        self._file = os.fdopen(descriptor, "a", encoding="utf-8")

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < self.MAX_BYTES:
                return
            if self._file is not None:
                self._file.close()
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            os.replace(self.path, rotated)
            self._open()
        except OSError:
            pass

    @staticmethod
    def new_operation_id(prefix: str) -> str:
        return "%s-%s" % (prefix, secrets.token_hex(4))

    def write(self, event: str, fields: Optional[Dict[str, object]] = None) -> None:
        sanitized = []
        for key, value in (fields or {}).items():
            text = str(value)
            if any(character in text for character in ("\n", "\r", "\t")):
                text = "invalid"
            sanitized.append("%s=%s" % (key, text))
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        line = "%s %s%s\n" % (timestamp, event, (" " + " ".join(sanitized)) if sanitized else "")
        with self._lock:
            if self.echo:
                print(line, end="")
            if self._file is None:
                return
            self._rotate_if_needed()
            try:
                self._file.write(line)
                self._file.flush()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None

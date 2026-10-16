"""
Token meter. Counts what each CLI command prints, which is what lands in the
agent's context, and appends one line per command to the workspace token log.
Tokens are estimated as characters / 4; the host's own count is the exact one.
The agent's own reasoning and the documents it reads are not seen here: the
README adds the documents' size, and the agent can record its total with `log
--tokens`.
"""

import atexit
import json
import sys
from datetime import datetime
from typing import List

from olap_swifter.paths import estimate_tokens, workspace_path, write_workspace_readme

# CLI groups whose subcommand is part of the command name ("source match").
GROUPS = ("schema", "source")


class _Counting:
    """Passes writes through to the real stream and counts the characters."""

    def __init__(self, stream):
        self._stream, self.chars = stream, 0

    def write(self, s):
        self.chars += len(s)
        return self._stream.write(s)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def command_name(argv: List[str]) -> str:
    words = [a for a in argv[1:] if not a.startswith("-")]
    if not words:
        return ""
    return " ".join(words[:2]) if words[0] in GROUPS else words[0]


def start(argv: List[str] = None) -> None:
    """Meters this process's stdout; the total is logged when it exits."""
    argv = argv if argv is not None else sys.argv
    if isinstance(sys.stdout, _Counting) or "--help" in argv:
        return
    counter = sys.stdout = _Counting(sys.stdout)
    command = command_name(argv)

    def record():
        if not counter.chars or not command:
            return
        try:
            entry = {"time": datetime.now().isoformat(timespec="seconds"), "command": command,
                     "chars": counter.chars, "tokens": estimate_tokens(counter.chars)}
            with open(workspace_path("token_log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            write_workspace_readme()
        except Exception:
            pass  # metering never fails a command

    atexit.register(record)

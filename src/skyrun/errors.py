"""Errors a caller can handle without depending on a provider SDK."""


class SkyrunError(Exception):
    """Base class for expected configuration, transport, and sync errors."""


class ConfigError(SkyrunError, ValueError):
    """Invalid or ambiguous project configuration."""


class SyncConflict(SkyrunError):
    """Local edits prevented applying one or more remote files."""

    def __init__(self, paths):
        self.paths = tuple(paths)
        super().__init__("local files changed during execution: " + ", ".join(self.paths))

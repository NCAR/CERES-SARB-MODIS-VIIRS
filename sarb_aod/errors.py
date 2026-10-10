"""Exception types raised by sarb_aod."""


class SarbAodError(Exception):
    """Base class for sarb_aod errors."""


class ConfigurationError(SarbAodError):
    """A configuration file, profile, or template is invalid."""


class StageError(SarbAodError):
    """Acquisition of L2 granules failed."""

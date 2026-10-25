"""Exception types. Messages must never contain raw data values."""


class FieldLedgerError(Exception):
    """Base class for all FieldLedger errors."""


class ConfigError(FieldLedgerError):
    """A configuration file is missing, malformed or invalid."""


class PathAccessError(FieldLedgerError):
    """A path resolved outside its permitted base directory."""

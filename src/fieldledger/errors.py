"""Exception types. Messages must never contain raw data values."""


class FieldLedgerError(Exception):
    """Base class for all FieldLedger errors."""


class ConfigError(FieldLedgerError):
    """A configuration file is missing, malformed or invalid."""


class PathAccessError(FieldLedgerError):
    """A path resolved outside its permitted base directory."""


class IngestionError(FieldLedgerError):
    """Base class for input-ingestion failures (CSV and SQLite profiling).

    Messages never contain cell values or absolute paths.
    """


class UnsupportedFileTypeError(IngestionError):
    """The input file extension is not one the ingestion layer accepts."""


class InputAccessError(IngestionError):
    """The input is missing, not a regular file, unreadable, or otherwise inaccessible."""


class InputTooLargeError(IngestionError):
    """The input exceeds a configured size, row, column or table limit."""


class MalformedCsvError(IngestionError):
    """The CSV is empty, has invalid headers, inconsistent rows, or cannot be decoded."""


class InvalidDatabaseError(IngestionError):
    """The file is not a valid or readable SQLite database (invalid, corrupt, unusable names)."""


class DatabaseTimeoutError(IngestionError):
    """SQLite profiling exceeded its time budget or the database stayed locked."""

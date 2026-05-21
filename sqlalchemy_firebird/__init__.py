# firebird/__init__.py
# Copyright (C) 2005-2023 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is released under the MIT License: http://www.opensource.org/licenses/mit-license.php
from ._version import __version__

# Re-export the Firebird-specific column types so users can write
# ``from sqlalchemy_firebird import FBVARCHAR, FBBLOB, INT128, ...``. Only
# ``types`` is imported here (not ``firebird``/``base``) so importing the
# package never eagerly loads the native firebird-driver -- the dialect is
# resolved lazily through the ``sqlalchemy.dialects`` entry point.
from .types import (
    FBBIGINT,
    FBBINARY,
    FBBLOB,
    FBBOOLEAN,
    FBCHAR,
    FBDATE,
    FBDECFLOAT,
    FBDECIMAL,
    FBDOUBLE_PRECISION,
    FBFLOAT,
    FBINT128,
    FBINTEGER,
    FBNCHAR,
    FBNUMERIC,
    FBNVARCHAR,
    FBREAL,
    FBSMALLINT,
    FBTEXT,
    FBTIME,
    FBTIMESTAMP,
    FBUUID,
    FBVARBINARY,
    FBVARCHAR,
)

# Firebird-specific DML: insert(...).matching(...) -> UPDATE OR INSERT. Only
# pulls in sqlalchemy.sql (no firebird-driver), so the import stays driver-free.
from .dml import insert, Insert

# Convenient un-prefixed aliases for the Firebird-only SQL types that have no
# generic SQLAlchemy spelling.
INT128 = FBINT128
DECFLOAT = FBDECFLOAT

__all__ = (
    "__version__",
    "FBBIGINT",
    "FBBINARY",
    "FBBLOB",
    "FBBOOLEAN",
    "FBCHAR",
    "FBDATE",
    "FBDECFLOAT",
    "FBDECIMAL",
    "FBDOUBLE_PRECISION",
    "FBFLOAT",
    "FBINT128",
    "FBINTEGER",
    "FBNCHAR",
    "FBNUMERIC",
    "FBNVARCHAR",
    "FBREAL",
    "FBSMALLINT",
    "FBTEXT",
    "FBTIME",
    "FBTIMESTAMP",
    "FBUUID",
    "FBVARBINARY",
    "FBVARCHAR",
    "INT128",
    "DECFLOAT",
    "insert",
    "Insert",
)

# firebird/dml.py
# Firebird-specific DML constructs.
#
# Firebird's single-statement upsert is ``UPDATE OR INSERT INTO t (...) VALUES
# (...) [MATCHING (...)]``: it updates the row(s) matching the MATCHING columns
# (or the primary key when MATCHING is omitted) and inserts otherwise. This is
# the Firebird counterpart to PostgreSQL's ``INSERT ... ON CONFLICT`` and
# MySQL's ``INSERT ... ON DUPLICATE KEY UPDATE``; this module mirrors the
# structure of ``sqlalchemy.dialects.postgresql.dml``.
from __future__ import annotations

from sqlalchemy.sql import coercions
from sqlalchemy.sql import roles
from sqlalchemy.sql.base import _generative
from sqlalchemy.sql.dml import Insert as StandardInsert
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.util.typing import Self


__all__ = ("Insert", "insert")


def insert(table) -> Insert:
    """Construct a Firebird-specific :class:`_firebird.Insert` construct.

    The :class:`_firebird.Insert` construct extends the core
    :class:`_sql.Insert` with :meth:`_firebird.Insert.matching`, which renders
    Firebird's ``UPDATE OR INSERT`` upsert statement::

        from sqlalchemy_firebird import insert

        stmt = insert(my_table).values(id=1, data="x").matching(my_table.c.id)
        # UPDATE OR INSERT INTO my_table (id, data)
        #   VALUES (?, ?) MATCHING (id)

    ``MATCHING`` defaults to the table's primary key when no columns are given,
    so ``matching()`` alone produces a primary-key upsert. ``RETURNING`` is
    supported, e.g. ``insert(t).values(...).matching().returning(t.c.id)``.

    ``UPDATE OR INSERT`` only accepts a ``VALUES`` row (not ``INSERT .. SELECT``)
    and a single row per statement, matching Firebird's grammar.
    """
    return Insert(table)


class Insert(StandardInsert):
    """Firebird-specific implementation of INSERT.

    Adds the :meth:`matching` method for Firebird's ``UPDATE OR INSERT``
    upsert. Created with the :func:`sqlalchemy_firebird.insert` function.
    """

    stringify_dialect = "firebird"
    inherit_cache = False

    @_generative
    def matching(self, *columns) -> Self:
        """Render the statement as ``UPDATE OR INSERT ... MATCHING (...)``.

        :param \\*columns: the columns (or column names) that identify an
         existing row to update. When omitted, Firebird matches on the target
         table's primary key.
        """
        self._post_values_clause = UpdateOrInsertMatch(columns)
        return self


class UpdateOrInsertMatch(ClauseElement):
    """Marks an :class:`Insert` as ``UPDATE OR INSERT`` and carries the
    optional ``MATCHING`` columns. Installed as the statement's
    ``_post_values_clause`` so it renders just after ``VALUES (...)``."""

    __visit_name__ = "fb_update_or_insert_match"
    stringify_dialect = "firebird"
    inherit_cache = False

    def __init__(self, columns):
        self.matching_elements = [
            coercions.expect(roles.DMLColumnRole, c) for c in columns
        ]

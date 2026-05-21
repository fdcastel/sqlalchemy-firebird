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
    upsert and :meth:`overriding_system_value` / :meth:`overriding_user_value`
    for ``INSERT ... OVERRIDING``. Created with the
    :func:`sqlalchemy_firebird.insert` function.
    """

    stringify_dialect = "firebird"
    inherit_cache = False

    # None | "SYSTEM" | "USER" -- INSERT ... OVERRIDING {SYSTEM|USER} VALUE.
    _fb_overriding = None

    @_generative
    def matching(self, *columns, order_by=None, rows=None) -> Self:
        """Render the statement as ``UPDATE OR INSERT ... MATCHING (...)``.

        :param \\*columns: the columns (or column names) that identify an
         existing row to update. When omitted, Firebird matches on the target
         table's primary key.
        :param order_by: an ORDER BY expression (or list of them) constraining
         which matched rows are updated together with ``rows``. **Firebird
         5.0+.**
        :param rows: limit the number of matched rows updated -- an ``int``
         (``ROWS n``) or a ``(start, end)`` pair (``ROWS start TO end``).
         **Firebird 5.0+.**
        """
        self._post_values_clause = UpdateOrInsertMatch(
            columns, order_by=order_by, rows=rows
        )
        return self

    @_generative
    def overriding_system_value(self) -> Self:
        """Render ``INSERT ... OVERRIDING SYSTEM VALUE`` (Firebird 4.0+).

        Lets an explicit value be inserted into a ``GENERATED ALWAYS AS
        IDENTITY`` column (which a plain INSERT rejects).
        """
        self._fb_overriding = "SYSTEM"
        return self

    @_generative
    def overriding_user_value(self) -> Self:
        """Render ``INSERT ... OVERRIDING USER VALUE`` (Firebird 4.0+).

        Tells Firebird to ignore the user-supplied value for an identity
        column and use the generated one instead.
        """
        self._fb_overriding = "USER"
        return self


class UpdateOrInsertMatch(ClauseElement):
    """Marks an :class:`Insert` as ``UPDATE OR INSERT`` and carries the
    optional ``MATCHING`` columns plus the Firebird 5.0+ ``ORDER BY`` / ``ROWS``
    clauses. Installed as the statement's ``_post_values_clause`` so it renders
    just after ``VALUES (...)`` (and before ``RETURNING``)."""

    __visit_name__ = "fb_update_or_insert_match"
    stringify_dialect = "firebird"
    inherit_cache = False

    def __init__(self, columns, order_by=None, rows=None):
        self.matching_elements = [
            coercions.expect(roles.DMLColumnRole, c) for c in columns
        ]

        if order_by is None:
            order_by = ()
        elif not isinstance(order_by, (list, tuple)):
            order_by = (order_by,)
        self.order_by_elements = [
            coercions.expect(roles.OrderByRole, e) for e in order_by
        ]

        if rows is not None and not (
            isinstance(rows, int)
            or (
                isinstance(rows, (list, tuple))
                and len(rows) == 2
                and all(isinstance(x, int) for x in rows)
            )
        ):
            raise ValueError(
                "rows must be an int (ROWS n) or a (start, end) pair "
                "(ROWS start TO end)"
            )
        self.rows = rows

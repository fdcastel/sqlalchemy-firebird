import datetime
import pytest

from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import exc
from sqlalchemy import extract
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import Identity
from sqlalchemy import Integer
from sqlalchemy import literal
from sqlalchemy import MetaData
from sqlalchemy import select
from sqlalchemy import Sequence
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy import text
from sqlalchemy import Time
from sqlalchemy import true
from sqlalchemy import union
from sqlalchemy.testing import assert_raises
from sqlalchemy.testing import AssertsExecutionResults
from sqlalchemy.testing import engines
from sqlalchemy.testing import eq_
from sqlalchemy.testing import expect_warnings
from sqlalchemy.testing import fixtures
from sqlalchemy.testing import requires

from sqlalchemy_firebird import insert as fb_insert
from sqlalchemy_firebird.types import _FBInterval


class QueryTest(fixtures.TestBase):
    @testing.provide_metadata
    def test_strlen(self, connection):
        metadata = self.metadata

        t = Table(
            "t1",
            metadata,
            Column("id", Integer, Sequence("t1idseq"), primary_key=True),
            Column("name", String(10)),
        )
        metadata.create_all(testing.db)
        connection.execute(t.insert().values(dict(name="dante")))
        connection.execute(t.insert().values(dict(name="alighieri")))
        eq_(
            connection.execute(
                select(func.count(t.c.id)).where(func.length(t.c.name) == 5)
            ).scalar(),
            1,
        )

    def test_render_casts_untyped_param_in_select_list(self, connection):
        # Firebird rejects a bare "?" wherever it can't infer the datatype at
        # PREPARE time (a SELECT list, COALESCE arguments, ...). The dialect's
        # bind_typing=RENDER_CASTS rescues these by emitting CAST(? AS <type>).
        # This locks that behaviour in: without RENDER_CASTS the statements
        # below fail with "Dynamic SQL Error / Datatype unknown".
        eq_(connection.execute(select(literal(5))).scalar(), 5)
        eq_(connection.execute(select(literal("abc"))).scalar(), "abc")
        eq_(
            connection.execute(
                select(func.coalesce(literal(1), literal(2)))
            ).scalar(),
            1,
        )

    @testing.provide_metadata
    def test_like_pattern_longer_than_column(self, connection):
        # A LIKE/ILIKE/NOT LIKE pattern longer than the matched column must
        # not raise a string-truncation error: the pattern is cast to
        # unbounded text, not the column's VARCHAR(2).
        t = Table("likt", self.metadata, Column("x", String(2)))
        t.create(testing.db)
        connection.execute(t.insert(), [{"x": "AB"}, {"x": "BC"}, {"x": "AC"}])

        eq_(
            connection.scalars(select(t.c.x).where(t.c.x.like("A%C%Z"))).all(),
            [],
        )
        eq_(
            connection.scalars(select(t.c.x).where(t.c.x.ilike("a%c"))).all(),
            ["AC"],
        )
        eq_(
            sorted(
                connection.scalars(
                    select(t.c.x).where(t.c.x.notlike("A%C%Z"))
                ).all()
            ),
            ["AB", "AC", "BC"],
        )

    def test_percents_in_text(self, connection):
        for expr, result in (
            (text("select '%' from rdb$database"), "%"),
            (text("select '%%' from rdb$database"), "%%"),
            (text("select '%%%' from rdb$database"), "%%%"),
            (
                text("select 'hello % world' from rdb$database"),
                "hello % world",
            ),
        ):
            eq_(connection.scalar(expr), result)


class CompoundSelectOrderByTest(fixtures.TablesTest):
    """Real-DB coverage for the positional ORDER BY rewrite in compound
    selects (the modifier-carrying cases the compliance suite doesn't
    exercise)."""

    __backend__ = True
    run_inserts = "once"
    run_deletes = None

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "cs_data",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("x", Integer),
            Column("y", Integer),
        )

    @classmethod
    def insert_data(cls, connection):
        connection.execute(
            cls.tables.cs_data.insert(),
            [
                {"id": 1, "x": 1, "y": 9},
                {"id": 2, "x": 2, "y": 9},
                {"id": 3, "x": 3, "y": 7},
                {"id": 4, "x": 4, "y": 7},
            ],
        )

    def test_union_order_by_desc(self, connection):
        t = self.tables.cs_data
        u = union(
            select(t).where(t.c.id.in_([1, 3])),
            select(t).where(t.c.id == 4),
        )
        rows = connection.execute(
            u.order_by(u.selected_columns.id.desc())
        ).fetchall()
        eq_(rows, [(4, 4, 7), (3, 3, 7), (1, 1, 9)])

    def test_union_order_by_multi_column(self, connection):
        # ORDER BY y, id DESC  ->  "ORDER BY 3, 1 DESC" on Firebird.
        t = self.tables.cs_data
        u = union(
            select(t).where(t.c.id.in_([1, 3])),
            select(t).where(t.c.id.in_([2, 4])),
        )
        rows = connection.execute(
            u.order_by(u.selected_columns.y, u.selected_columns.id.desc())
        ).fetchall()
        eq_(rows, [(4, 4, 7), (3, 3, 7), (2, 2, 9), (1, 1, 9)])


class UpdateOrInsertTest(fixtures.TablesTest):
    """Real-DB coverage for the Firebird UPDATE OR INSERT upsert (F1)."""

    __backend__ = True
    run_deletes = "each"

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "uoi",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("data", String(50)),
        )

    def _all(self, connection):
        t = self.tables.uoi
        return connection.execute(select(t).order_by(t.c.id)).fetchall()

    def test_insert_then_update(self, connection):
        t = self.tables.uoi
        connection.execute(
            fb_insert(t).values(id=1, data="a").matching(t.c.id)
        )
        # Same MATCHING key -> updates the existing row rather than inserting.
        connection.execute(
            fb_insert(t).values(id=1, data="b").matching(t.c.id)
        )
        eq_(self._all(connection), [(1, "b")])

    def test_matching_defaults_to_primary_key(self, connection):
        t = self.tables.uoi
        connection.execute(fb_insert(t).values(id=1, data="a").matching())
        connection.execute(fb_insert(t).values(id=1, data="b").matching())
        eq_(self._all(connection), [(1, "b")])

    def test_returning(self, connection):
        t = self.tables.uoi
        r = connection.execute(
            fb_insert(t)
            .values(id=5, data="x")
            .matching(t.c.id)
            .returning(t.c.id, t.c.data)
        )
        eq_(r.fetchall(), [(5, "x")])

    def test_executemany(self, connection):
        t = self.tables.uoi
        connection.execute(
            fb_insert(t).values(id=1, data="a").matching(t.c.id)
        )
        # Each parameter set is its own UPDATE OR INSERT: id=1 updates, id=2
        # inserts.
        connection.execute(
            fb_insert(t).matching(t.c.id),
            [{"id": 1, "data": "A"}, {"id": 2, "data": "B"}],
        )
        eq_(self._all(connection), [(1, "A"), (2, "B")])


class IntervalArithmeticTest(fixtures.TablesTest):
    """Temporal arithmetic round-trips: Firebird uses days for DATE/TIMESTAMP
    but seconds for TIME; _FBInterval reconciles both (J11)."""

    __backend__ = True
    run_inserts = "once"
    run_deletes = None

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "dta",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("d", Date),
            Column("ts", DateTime),
            Column("tm", Time),
            Column("tm2", Time),
        )

    @classmethod
    def insert_data(cls, connection):
        connection.execute(
            cls.tables.dta.insert(),
            {
                "id": 1,
                "d": datetime.date(2024, 1, 1),
                "ts": datetime.datetime(2024, 1, 1, 12, 0, 0),
                "tm": datetime.time(12, 0, 0),
                "tm2": datetime.time(13, 30, 0),
            },
        )

    def test_date_plus_interval(self, connection):
        t = self.tables.dta
        eq_(
            connection.scalar(select(t.c.d + datetime.timedelta(days=2))),
            datetime.date(2024, 1, 3),
        )

    def test_timestamp_plus_interval(self, connection):
        t = self.tables.dta
        eq_(
            connection.scalar(
                select(t.c.ts + datetime.timedelta(hours=1, minutes=30))
            ),
            datetime.datetime(2024, 1, 1, 13, 30),
        )

    def test_time_plus_minus_interval(self, connection):
        t = self.tables.dta
        d = datetime.timedelta(hours=1, minutes=30)
        eq_(connection.scalar(select(t.c.tm + d)), datetime.time(13, 30))
        eq_(connection.scalar(select(t.c.tm - d)), datetime.time(10, 30))

    def test_time_minus_time(self, connection):
        t = self.tables.dta
        eq_(
            connection.scalar(select(t.c.tm2 - t.c.tm)),
            datetime.timedelta(hours=1, minutes=30),
        )


class AnalyticsTest(fixtures.TablesTest):
    """FB4+ analytics work through SQLAlchemy core: aggregate FILTER, window
    ranking functions and LATERAL joins (J10)."""

    __backend__ = True
    run_inserts = "once"
    run_deletes = None

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "an",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("grp", Integer),
            Column("val", Integer),
        )

    @classmethod
    def insert_data(cls, connection):
        connection.execute(
            cls.tables.an.insert(),
            [{"id": i, "grp": i % 2, "val": i * 10} for i in range(1, 6)],
        )

    @testing.requires.firebird_4_or_higher
    def test_aggregate_filter(self, connection):
        t = self.tables.an
        row = connection.execute(
            select(
                func.count().filter(t.c.val > 20), func.count()
            ).select_from(t)
        ).one()
        eq_(row, (3, 5))

    @testing.requires.firebird_4_or_higher
    def test_window_ranking_functions(self, connection):
        # row_number()/ntile() -- no-arg and literal-arg window functions.
        t = self.tables.an
        rows = connection.execute(
            select(
                t.c.id,
                func.row_number().over(order_by=t.c.val),
                func.ntile(2).over(order_by=t.c.val),
            ).order_by(t.c.id)
        ).all()
        eq_(rows, [(1, 1, 1), (2, 2, 1), (3, 3, 1), (4, 4, 2), (5, 5, 2)])

    @testing.requires.firebird_4_or_higher
    def test_cume_dist_percent_rank(self, connection):
        t = self.tables.an
        rows = [
            (r[0], round(r[1], 4), round(r[2], 4))
            for r in connection.execute(
                select(
                    t.c.id,
                    func.cume_dist().over(order_by=t.c.val),
                    func.percent_rank().over(order_by=t.c.val),
                ).order_by(t.c.id)
            )
        ]
        eq_(
            rows,
            [
                (1, 0.2, 0.0),
                (2, 0.4, 0.25),
                (3, 0.6, 0.5),
                (4, 0.8, 0.75),
                (5, 1.0, 1.0),
            ],
        )

    @testing.requires.firebird_4_or_higher
    def test_lateral_join(self, connection):
        # Correlated LATERAL: per-row count of same-group rows.
        t = self.tables.an
        a = t.alias("a")
        lat = (
            select(func.count().label("cnt"))
            .where(t.c.grp == a.c.grp)
            .lateral("lat")
        )
        rows = connection.execute(
            select(a.c.id, lat.c.cnt)
            .select_from(a.join(lat, true()))
            .order_by(a.c.id)
        ).all()
        eq_(rows, [(1, 3), (2, 2), (3, 3), (4, 2), (5, 3)])


class UpdateOrInsertOrderByTest(fixtures.TablesTest):
    """UPDATE OR INSERT ORDER BY / ROWS row-limiting (J8, FB5+)."""

    __backend__ = True
    run_deletes = "each"

    @classmethod
    def define_tables(cls, metadata):
        # No primary key -> MATCHING is required, and a non-unique key lets
        # ROWS actually limit how many matched rows are updated.
        Table(
            "uoi_ord",
            metadata,
            Column("grp", Integer),
            Column("data", String(50)),
        )

    @testing.requires.firebird_5_or_higher
    def test_order_by_rows_limits_updated_rows(self, connection):
        t = self.tables.uoi_ord
        connection.execute(
            t.insert(), [{"grp": 1, "data": "a"}, {"grp": 1, "data": "b"}]
        )
        # MATCHING (grp) matches both rows; ROWS 1 ORDER BY data updates only
        # the first ('a' -> 'Z'), leaving 'b' untouched.
        connection.execute(
            fb_insert(t)
            .values(grp=1, data="Z")
            .matching(t.c.grp, order_by=t.c.data, rows=1)
        )
        eq_(
            sorted(tuple(r) for r in connection.execute(select(t))),
            [(1, "Z"), (1, "b")],
        )


class InsertOverridingTest(fixtures.TablesTest):
    """Real-DB coverage for INSERT ... OVERRIDING (J7, FB4+)."""

    __backend__ = True
    run_deletes = "each"

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "ovr",
            metadata,
            Column("id", Integer, Identity(always=True), primary_key=True),
            Column("data", String(50)),
        )

    @testing.requires.firebird_4_or_higher
    def test_overriding_system_value(self, connection):
        # An explicit value can be forced into a GENERATED ALWAYS column.
        t = self.tables.ovr
        connection.execute(
            fb_insert(t).values(id=100, data="x").overriding_system_value()
        )
        eq_(connection.execute(select(t)).fetchall(), [(100, "x")])

    @testing.requires.firebird_4_or_higher
    def test_overriding_system_value_returning(self, connection):
        t = self.tables.ovr
        r = connection.execute(
            fb_insert(t)
            .values(id=200, data="y")
            .overriding_system_value()
            .returning(t.c.id)
        )
        eq_(r.fetchall(), [(200,)])


#
# Tests from postgresql/test_query.py
#


class FunctionTypingTest(fixtures.TestBase, AssertsExecutionResults):
    __backend__ = True

    def test_count_star(self, connection):
        eq_(connection.scalar(func.count("*")), 1)

    def test_count_int(self, connection):
        eq_(connection.scalar(func.count(1)), 1)


class InsertTest(fixtures.TestBase, AssertsExecutionResults):
    __backend__ = True

    def test_foreignkey_missing_insert(self, metadata, connection):
        Table(
            "t1",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        t2 = Table(
            "t2",
            metadata,
            Column("id", Integer, ForeignKey("t1.id"), primary_key=True),
        )

        metadata.create_all(connection)

        # want to ensure that "null value in column "id" violates not-
        # null constraint" is raised (IntegrityError on psycoopg2, but
        # ProgrammingError on pg8000), and not "ProgrammingError:
        # (ProgrammingError) relationship "t2_id_seq" does not exist".
        # the latter corresponds to autoincrement behavior, which is not
        # the case here due to the foreign key.

        with expect_warnings(".*has no Python-side or server-side default.*"):
            assert_raises(
                (exc.DatabaseError),
                connection.execute,
                t2.insert(),
            )

    def test_sequence_insert(self, metadata, connection):
        table = Table(
            "testtable",
            metadata,
            Column("id", Integer, Sequence("my_seq"), primary_key=True),
            Column("data", String(30)),
        )
        metadata.create_all(connection)
        self._assert_data_with_sequence_returning(connection, table, "my_seq")

    def test_opt_sequence_insert(self, metadata, connection):
        table = Table(
            "testtable",
            metadata,
            Column(
                "id",
                Integer,
                Sequence("my_seq", optional=True),
                primary_key=True,
            ),
            Column("data", String(30)),
        )
        metadata.create_all(connection)
        self._assert_data_autoincrement_returning(
            connection, table, pk_sequence="my_seq"
        )

    def test_autoincrement_insert(self, metadata, connection):
        table = Table(
            "testtable",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("data", String(30)),
        )
        metadata.create_all(connection)
        self._assert_data_autoincrement_returning(connection, table)

    def test_noautoincrement_insert(self, metadata, connection):
        table = Table(
            "testtable",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("data", String(30)),
        )
        metadata.create_all(connection)
        self._assert_data_noautoincrement(connection, table)

    def _assert_data_autoincrement(self, connection, table):
        """
        invoked by:
        * test_opt_sequence_insert
        * test_autoincrement_insert
        """

        with self.sql_execution_asserter(connection) as asserter:
            conn = connection

            # execute with explicit id
            r = conn.execute(table.insert(), {"id": 30, "data": "d1"})
            eq_(r.inserted_primary_key, (30,))

            # execute with prefetch id
            s = table.insert()
            r = conn.execute(s, {"data": "d2"})
            eq_(r.inserted_primary_key, (1,))

            # executemany with explicit ids
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )

            # executemany, uses SERIAL
            conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])

            # single execute, explicit id, inline
            conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})

            # single execute, inline, uses SERIAL
            conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            conn.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (1, "d2"),
                (31, "d3"),
                (32, "d4"),
                (2, "d5"),
                (3, "d6"),
                (33, "d7"),
                (4, "d8"),
            ],
        )

        conn.execute(table.delete())

        # test the same series of events using a reflected version of the table

        m2 = MetaData()
        table = Table(table.name, m2, autoload_with=connection)

        with self.sql_execution_asserter(connection) as asserter:
            conn.execute(table.insert(), {"id": 30, "data": "d1"})
            r = conn.execute(table.insert(), {"data": "d2"})
            eq_(r.inserted_primary_key, (5,))
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )
            conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])
            conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})
            conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            conn.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (5, "d2"),
                (31, "d3"),
                (32, "d4"),
                (6, "d5"),
                (7, "d6"),
                (33, "d7"),
                (8, "d8"),
            ],
        )

    def _assert_data_autoincrement_returning(
        self, connection, table, pk_sequence=None
    ):
        """
        invoked by:
        * test_opt_sequence_returning_insert
        * test_autoincrement_returning_insert
        """
        with self.sql_execution_asserter(connection) as asserter:
            conn = connection

            # execute with explicit id
            r = conn.execute(table.insert(), {"id": 30, "data": "d1"})
            eq_(r.inserted_primary_key, (30,))

            # execute with prefetch id
            r = conn.execute(table.insert(), {"data": "d2"})
            eq_(r.inserted_primary_key, (1,))

            # executemany with explicit ids
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )

            # executemany, uses SERIAL
            r = conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])

            # single execute, explicit id, inline
            r = conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})

            # single execute, inline, uses SERIAL
            r = conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            conn.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (1, "d2"),
                (31, "d3"),
                (32, "d4"),
                (2, "d5"),
                (3, "d6"),
                (33, "d7"),
                (4, "d8"),
            ],
        )
        conn.execute(table.delete())

        # test the same series of events using a reflected version of the table

        m2 = MetaData()
        old_table = table
        table = Table(table.name, m2, autoload_with=connection)

        # Firebird has no metadata to know that we are using this sequence as the primary key generator.
        #   Override the reflected information to add this information.
        if pk_sequence:
            table.columns[0].default = Sequence(pk_sequence)

        with self.sql_execution_asserter(connection) as asserter:
            conn.execute(table.insert(), {"id": 30, "data": "d1"})
            r = conn.execute(table.insert(), {"data": "d2"})
            eq_(r.inserted_primary_key, (5,))
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )
            conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])
            conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})
            conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            conn.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (5, "d2"),
                (31, "d3"),
                (32, "d4"),
                (6, "d5"),
                (7, "d6"),
                (33, "d7"),
                (8, "d8"),
            ],
        )

    def _assert_data_with_sequence(self, connection, table, seqname):
        """
        invoked by:
        * test_sequence_insert
        """

        with self.sql_execution_asserter(connection) as asserter:
            conn = connection
            conn.execute(table.insert(), {"id": 30, "data": "d1"})
            conn.execute(table.insert(), {"data": "d2"})
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )
            conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])
            conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})
            conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            conn.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (1, "d2"),
                (31, "d3"),
                (32, "d4"),
                (2, "d5"),
                (3, "d6"),
                (33, "d7"),
                (4, "d8"),
            ],
        )

    def _assert_data_with_sequence_returning(self, connection, table, seqname):
        """
        invoked by:
        * test_sequence_returning_insert
        """

        with self.sql_execution_asserter(connection) as asserter:
            conn = connection
            conn.execute(table.insert(), {"id": 30, "data": "d1"})
            conn.execute(table.insert(), {"data": "d2"})
            conn.execute(
                table.insert(),
                [{"id": 31, "data": "d3"}, {"id": 32, "data": "d4"}],
            )
            conn.execute(table.insert(), [{"data": "d5"}, {"data": "d6"}])
            conn.execute(table.insert().inline(), {"id": 33, "data": "d7"})
            conn.execute(table.insert().inline(), {"data": "d8"})

        eq_(
            connection.execute(table.select()).fetchall(),
            [
                (30, "d1"),
                (1, "d2"),
                (31, "d3"),
                (32, "d4"),
                (2, "d5"),
                (3, "d6"),
                (33, "d7"),
                (4, "d8"),
            ],
        )

    def _assert_data_noautoincrement(self, connection, table):
        """
        invoked by:
        * test_noautoincrement_insert
        """

        # turning off the cache because we are checking for compile-time warnings
        connection.execution_options(compiled_cache=None)

        conn = connection
        conn.execute(table.insert(), {"id": 30, "data": "d1"})

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    {"data": "d2"},
                )
            nested.rollback()

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    [{"data": "d2"}, {"data": "d3"}],
                )
            nested.rollback()

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    {"data": "d2"},
                )
            nested.rollback()

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    [{"data": "d2"}, {"data": "d3"}],
                )
            nested.rollback()

        conn.execute(
            table.insert(),
            [{"id": 31, "data": "d2"}, {"id": 32, "data": "d3"}],
        )
        conn.execute(table.insert().inline(), {"id": 33, "data": "d4"})
        eq_(
            conn.execute(table.select()).fetchall(),
            [(30, "d1"), (31, "d2"), (32, "d3"), (33, "d4")],
        )
        conn.execute(table.delete())

        # test the same series of events using a reflected version of the table

        m2 = MetaData()
        table = Table(table.name, m2, autoload_with=connection)
        conn = connection

        conn.execute(table.insert(), {"id": 30, "data": "d1"})

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    {"data": "d2"},
                )
            nested.rollback()

        with conn.begin_nested() as nested:
            with expect_warnings(
                ".*has no Python-side or server-side default.*"
            ):
                assert_raises(
                    (exc.DatabaseError),
                    conn.execute,
                    table.insert(),
                    [{"data": "d2"}, {"data": "d3"}],
                )
            nested.rollback()

        conn.execute(
            table.insert(),
            [{"id": 31, "data": "d2"}, {"id": 32, "data": "d3"}],
        )
        conn.execute(table.insert().inline(), {"id": 33, "data": "d4"})
        eq_(
            conn.execute(table.select()).fetchall(),
            [(30, "d1"), (31, "d2"), (32, "d3"), (33, "d4")],
        )


class ExtractTest(fixtures.TablesTest):
    __backend__ = True

    run_inserts = "once"
    run_deletes = None

    class TZ(datetime.tzinfo):
        def tzname(self, dt):
            return "UTC+04:00"

        def utcoffset(self, dt):
            return datetime.timedelta(hours=4)

    @classmethod
    def setup_bind(cls):
        from sqlalchemy import event

        eng = engines.testing_engine(options={"scope": "class"})

        @event.listens_for(eng, "connect")
        def connect(dbapi_conn, rec):
            if requires.datetime_timezone.enabled:
                cursor = dbapi_conn.cursor()
                cursor.execute("SET TIME ZONE 'UTC'")
                cursor.close()

        return eng

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "t",
            metadata,
            Column("dtme", DateTime),
            Column("dt", Date),
            Column("tm", Time),
            Column("intv", _FBInterval),
            # WITH TIME ZONE is FB4+ only (and now raises on FB3); the dttz
            # tests below are gated on datetime_timezone, so degrade the column
            # to a plain TIMESTAMP on FB3 where those tests skip anyway.
            Column(
                "dttz",
                DateTime(timezone=requires.datetime_timezone.enabled),
            ),
        )

    @classmethod
    def insert_data(cls, connection):
        connection.execute(
            cls.tables.t.insert(),
            {
                "dtme": datetime.datetime(2012, 5, 10, 12, 15, 25),
                "dt": datetime.date(2012, 5, 10),
                "tm": datetime.time(12, 15, 25),
                "intv": datetime.timedelta(seconds=570),
                "dttz": datetime.datetime(
                    2012, 5, 10, 12, 15, 25, tzinfo=cls.TZ()
                ),
            },
        )

    def _test(self, connection, expr, field="all", overrides=None):
        t = self.tables.t

        if field == "all":
            fields = {
                "year": 2012,
                "month": 5,
                "day": 10,
                "hour": 12,
                "minute": 15,
            }
        elif field == "time":
            fields = {"hour": 12, "minute": 15, "second": 25}
        elif field == "date":
            fields = {"year": 2012, "month": 5, "day": 10}
        elif field == "all+tz":
            fields = {
                "year": 2012,
                "month": 5,
                "day": 10,
                "hour": 12,
                "timezone_hour": 4,
            }
        else:
            fields = field

        if overrides:
            fields.update(overrides)

        for field in fields:
            try:
                result = connection.execute(
                    select(extract(field, expr)).select_from(t)
                ).scalar()
                eq_(result, fields[field])
            except exc.DatabaseError as e:
                # Ignores "Specified EXTRACT part does not exist in input datatype" error.
                if "EXTRACT part does not exist" not in str(e):
                    raise

    def test_one(self, connection):
        t = self.tables.t
        self._test(connection, t.c.dtme, "all")

    def test_two(self, connection):
        t = self.tables.t
        self._test(
            connection,
            t.c.dtme + t.c.intv,
            overrides={"minute": 24},
        )

    def test_three(self, connection):
        self.tables.t

        actual_ts = self.bind.connect().execute(
            func.current_timestamp()
        ).scalar() - datetime.timedelta(days=5)
        self._test(
            connection,
            func.current_timestamp() - datetime.timedelta(days=5),
            {
                "hour": actual_ts.hour,
                "year": actual_ts.year,
                "month": actual_ts.month,
            },
        )

    def test_four(self, connection):
        t = self.tables.t
        self._test(
            connection,
            datetime.timedelta(days=5) + t.c.dt,
            overrides={
                "day": 15,
                "hour": 0,
                "minute": 0,
            },
        )

    def test_five(self, connection):
        t = self.tables.t
        self._test(
            connection,
            func.coalesce(t.c.dtme, func.current_timestamp()),
        )

    @pytest.mark.skip(
        reason="Fix operations with TIME datatype (operand must be in seconds, not in days)"
    )
    def test_six(self, connection):
        t = self.tables.t
        self._test(
            connection,
            t.c.tm + datetime.timedelta(seconds=30),
            "time",
            overrides={"second": 55},
        )

    def test_seven(self, connection):
        self._test(
            connection,
            literal(datetime.timedelta(seconds=10))
            - literal(datetime.timedelta(seconds=10)),
            "all",
            overrides={
                "hour": 0,
                "minute": 0,
                "month": 0,
                "year": 0,
                "day": 0,
            },
        )

    @pytest.mark.skip(
        reason="Fix operations with TIME datatype (operand must be in seconds, not in days)"
    )
    def test_eight(self, connection):
        t = self.tables.t
        self._test(
            connection,
            t.c.tm + datetime.timedelta(seconds=30),
            {"hour": 12, "minute": 15, "second": 55},
        )

    def test_nine(self, connection):
        self._test(connection, text("t.dt + t.tm"))

    def test_ten(self, connection):
        t = self.tables.t
        self._test(connection, t.c.dt + t.c.tm)

    def test_eleven(self, connection):
        self._test(
            connection,
            func.current_timestamp() - func.current_timestamp(),
            {"year": 0, "month": 0, "day": 0, "hour": 0},
        )

    @requires.datetime_timezone
    def test_twelve(self, connection):
        t = self.tables.t

        actual_ts = connection.scalar(
            func.current_timestamp()
        ) - datetime.datetime(2012, 5, 10, 12, 15, 25, tzinfo=self.TZ())

        self._test(
            connection,
            func.current_timestamp() - t.c.dttz,
            {"day": actual_ts.days},
        )

    @requires.datetime_timezone
    def test_thirteen(self, connection):
        t = self.tables.t
        self._test(connection, t.c.dttz, "all+tz")

    def test_fourteen(self, connection):
        t = self.tables.t
        self._test(connection, t.c.tm, "time")

    def test_fifteen(self, connection):
        t = self.tables.t
        self._test(
            connection,
            datetime.timedelta(days=5) + t.c.dtme,
            overrides={"day": 15},
        )

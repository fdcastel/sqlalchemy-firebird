import datetime
import pytest

from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import exc
from sqlalchemy import extract
from sqlalchemy import ForeignKey
from sqlalchemy import func
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
from sqlalchemy.testing import assert_raises
from sqlalchemy.testing import AssertsExecutionResults
from sqlalchemy.testing import engines
from sqlalchemy.testing import eq_
from sqlalchemy.testing import expect_warnings
from sqlalchemy.testing import fixtures
from sqlalchemy.testing import requires

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
            Column("dttz", DateTime(timezone=True)),
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

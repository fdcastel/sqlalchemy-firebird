import datetime

from sqlalchemy import bindparam
from sqlalchemy import cast
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import extract
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
from sqlalchemy.testing import config
from sqlalchemy.testing import engines
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL
from sqlalchemy.testing.assertions import AssertsExecutionResults
from sqlalchemy.testing.assertions import eq_
from sqlalchemy.engine.url import make_url
from firebird.driver import driver_config
from sqlalchemy_firebird.firebird import FBDialect_firebird


class ConnectionTest(fixtures.TablesTest):
    def test_is_disconnect(self):
        try:
            with testing.db.begin() as first_conn:
                con1_id = first_conn.exec_driver_sql(
                    "SELECT CURRENT_CONNECTION FROM rdb$database"
                ).scalar()

                with testing.db.begin() as second_conn:
                    # Kills first_conn
                    second_conn.exec_driver_sql(
                        "DELETE FROM mon$attachments WHERE mon$attachment_id = ?",
                        (con1_id,),
                    )

                # Attemps to read from first_conn
                first_conn.exec_driver_sql(
                    "SELECT CURRENT_CONNECTION FROM rdb$database"
                )

                assert False
        except Exception as err:
            eq_(testing.db.dialect.is_disconnect(err.orig, None, None), True)


#
# Tests from postgresql/test_dialect.py
#


class ExecuteManyTest(fixtures.TablesTest):
    __backend__ = True

    run_create_tables = "each"
    run_deletes = None

    @config.fixture()
    def connection(self):
        eng = engines.testing_engine(options={"use_reaper": False})

        conn = eng.connect()
        trans = conn.begin()
        yield conn
        if trans.is_active:
            trans.rollback()
        conn.close()
        eng.dispose()

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "data",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("x", String),
            Column("y", String),
            Column("z", Integer, server_default="5"),
        )

        Table(
            "Unitéble2",
            metadata,
            Column("méil", Integer, primary_key=True),
            Column("\u6e2c\u8a66", Integer),
        )

    def test_insert_unicode_keys(self, connection):
        table = self.tables["Unitéble2"]

        stmt = table.insert()

        connection.execute(
            stmt,
            [
                {"méil": 1, "\u6e2c\u8a66": 1},
                {"méil": 2, "\u6e2c\u8a66": 2},
                {"méil": 3, "\u6e2c\u8a66": 3},
            ],
        )

        eq_(connection.execute(table.select()).all(), [(1, 1), (2, 2), (3, 3)])

    @testing.requires.identity_columns
    def test_update(self, connection):
        connection.execute(
            self.tables.data.insert(),
            [
                {"x": "x1", "y": "y1"},
                {"x": "x2", "y": "y2"},
                {"x": "x3", "y": "y3"},
            ],
        )

        connection.execute(
            self.tables.data.update()
            .where(self.tables.data.c.x == bindparam("xval"))
            .values(y=bindparam("yval")),
            [{"xval": "x1", "yval": "y5"}, {"xval": "x3", "yval": "y6"}],
        )
        eq_(
            connection.execute(
                select(self.tables.data).order_by(self.tables.data.c.id)
            ).fetchall(),
            [(1, "x1", "y5", 5), (2, "x2", "y2", 5), (3, "x3", "y6", 5)],
        )


class MiscBackendTest(
    fixtures.TestBase, AssertsExecutionResults, AssertsCompiledSQL
):
    __backend__ = True

    @testing.provide_metadata
    def test_date_reflection(self):
        has_timezones = testing.requires.datetime_timezone.enabled

        metadata = self.metadata
        Table(
            "fbdate",
            metadata,
            Column("date1", DateTime(timezone=has_timezones)),
            Column("date2", DateTime(timezone=False)),
        )
        metadata.create_all(testing.db)
        m2 = MetaData()
        t2 = Table("fbdate", m2, autoload_with=testing.db)
        assert t2.c.date1.type.timezone is has_timezones
        assert t2.c.date2.type.timezone is False

    @testing.requires.datetime_timezone
    def test_extract(self, connection):
        fivedaysago = connection.execute(
            select(func.now().op("AT TIME ZONE")("UTC"))
        ).scalar() - datetime.timedelta(days=5)

        for field, exp in (
            ("year", fivedaysago.year),
            ("month", fivedaysago.month),
            ("day", fivedaysago.day),
        ):
            r = connection.execute(
                select(
                    extract(
                        field,
                        func.now().op("AT TIME ZONE")("UTC")
                        + datetime.timedelta(days=-5),
                    )
                )
            ).scalar()
            eq_(r, exp)

    @testing.provide_metadata
    def test_checksfor_sequence(self, connection):
        meta1 = self.metadata
        seq = Sequence("fooseq")
        t = Table("mytable", meta1, Column("col1", Integer, seq))
        seq.drop(connection)
        connection.execute(text("CREATE SEQUENCE fooseq"))
        t.create(connection, checkfirst=True)

    @testing.requires.identity_columns
    def test_sequence_detection_tricky_names(self, metadata, connection):
        for tname, cname in [
            ("tb1" * 30, "abc"),
            ("tb2", "abc" * 30),
            ("tb3" * 30, "abc" * 30),
            ("tb4", "abc"),
        ]:
            t = Table(
                tname[: connection.dialect.max_identifier_length],
                metadata,
                Column(
                    cname[: connection.dialect.max_identifier_length],
                    Integer,
                    primary_key=True,
                ),
            )
            t.create(connection)
            r = connection.execute(t.insert())
            eq_(r.inserted_primary_key, (1,))

    def test_quoted_name_bindparam_ok(self):
        from sqlalchemy.sql.elements import quoted_name

        with testing.db.connect() as conn:
            eq_(
                conn.scalar(
                    select(
                        cast(
                            literal(quoted_name("some_name", False)),
                            String,
                        )
                    )
                ),
                "some_name",
            )

    @testing.provide_metadata
    @testing.requires.identity_columns
    def test_preexecute_passivedefault(self, connection):
        """test that when we get a primary key column back from
        reflecting a table which has a default value on it, we pre-
        execute that DefaultClause upon insert."""

        meta = self.metadata
        connection.execute(
            text(
                """
                 CREATE TABLE speedy_users
                 (
                     speedy_user_id   INTEGER GENERATED BY DEFAULT AS IDENTITY   PRIMARY KEY,
                     user_name        VARCHAR(30)    NOT NULL,
                     user_password    VARCHAR(30)    NOT NULL
                 );
                """
            )
        )
        connection.commit()

        t = Table("speedy_users", meta, autoload_with=connection)
        r = connection.execute(
            t.insert(), dict(user_name="user", user_password="lala")
        )
        eq_(r.inserted_primary_key, (1,))
        result = connection.execute(t.select()).fetchall()
        assert result == [(1, "user", "lala")]
        connection.execute(text("DROP TABLE speedy_users"))

    def test_select_rowcount(self):
        # https://firebird-driver.readthedocs.io/en/latest/python-db-api-compliance.html#caveats

        # Determining rowcount for SELECT statements is problematic: the
        # rowcount is reported as zero until at least one row has been fetched
        # from the result set, and the rowcount is misreported if the result
        # set is larger than 1302 rows.

        conn = testing.db.connect()
        cursor = conn.exec_driver_sql(
            "SELECT 1 FROM rdb$database UNION ALL SELECT 2 FROM rdb$database"
        )
        eq_(cursor.rowcount, 0)


class CreateConnectArgsTest(fixtures.TestBase):
    def test_issue_69_same_host_distinct_ports(self):
        # Two servers on the same host with different ports must produce
        # distinct driver_config server registrations (issue #69).
        dialect = FBDialect_firebird()

        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@myhost:3050/db_a")
        )
        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@myhost:3051/db_b")
        )

        srv_a = driver_config.get_server("myhost:3050")
        srv_b = driver_config.get_server("myhost:3051")
        assert srv_a is not None and srv_b is not None
        assert srv_a is not srv_b
        eq_(srv_a.host.value, "myhost")
        eq_(srv_b.host.value, "myhost")
        eq_(srv_a.port.value, "3050")
        eq_(srv_b.port.value, "3051")

        db_a = driver_config.get_database("db_a")
        db_b = driver_config.get_database("db_b")
        eq_(db_a.server.value, "myhost:3050")
        eq_(db_b.server.value, "myhost:3051")

    def test_default_port_when_omitted(self):
        FBDialect_firebird().create_connect_args(
            make_url("firebird+firebird://u:p@otherhost/db_c")
        )
        srv = driver_config.get_server("otherhost:3050")
        assert srv is not None
        eq_(srv.port.value, "3050")

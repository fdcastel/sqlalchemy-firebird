import datetime

from sqlalchemy import bindparam
from sqlalchemy import cast
from sqlalchemy import Column
from sqlalchemy import create_engine
from sqlalchemy import exc
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
from sqlalchemy.testing.assertions import assert_raises
from sqlalchemy.testing.assertions import eq_
from sqlalchemy.testing.assertions import is_false
from sqlalchemy.testing.assertions import is_true
from sqlalchemy.engine.url import make_url
from firebird.driver import driver_config
import sqlalchemy_firebird as sqlalchemy_firebird_pkg
import sqlalchemy_firebird.types as fb_types
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


class PingTest(fixtures.TestBase):
    """do_ping uses firebird-driver's native Connection.ping() for
    pool_pre_ping, instead of compiling/executing SELECT 1 (F2)."""

    __backend__ = True

    def test_do_ping_uses_native_ping(self):
        # do_ping must delegate to the driver's native ping() and must not
        # start a transaction (the SQL fallback "SELECT 1 FROM rdb$database"
        # would). Spy on the real ping() rather than mocking its behavior.
        with testing.db.connect() as conn:
            dbapi_conn = conn.connection.dbapi_connection
            original_ping = dbapi_conn.ping
            calls = []

            def spy():
                calls.append(True)
                return original_ping()

            dbapi_conn.ping = spy
            try:
                is_true(testing.db.dialect.do_ping(dbapi_conn))
                eq_(len(calls), 1)
                # Native ping pings the attachment without opening a
                # transaction, unlike the SELECT-based default.
                eq_(dbapi_conn.is_active(), False)
            finally:
                del dbapi_conn.ping

    def test_pool_pre_ping_recovers_killed_connection(self):
        # End-to-end: a connection dropped server-side is detected by the
        # native ping (DatabaseError -> is_disconnect) and transparently
        # replaced when pool_pre_ping is on.
        eng = engines.testing_engine(options={"pool_pre_ping": True})
        try:
            with eng.connect() as conn:
                con_id = conn.exec_driver_sql(
                    "SELECT CURRENT_CONNECTION FROM rdb$database"
                ).scalar()

            with testing.db.begin() as killer:
                killer.exec_driver_sql(
                    "DELETE FROM mon$attachments WHERE mon$attachment_id = ?",
                    (con_id,),
                )

            with eng.connect() as conn:
                new_id = conn.exec_driver_sql(
                    "SELECT CURRENT_CONNECTION FROM rdb$database"
                ).scalar()
                assert new_id != con_id
        finally:
            eng.dispose()


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

        srv_a = driver_config.get_server("myhost/3050")
        srv_b = driver_config.get_server("myhost/3051")
        assert srv_a is not None and srv_b is not None
        assert srv_a is not srv_b
        eq_(srv_a.host.value, "myhost")
        eq_(srv_b.host.value, "myhost")
        eq_(srv_a.port.value, "3050")
        eq_(srv_b.port.value, "3051")

        db_a = driver_config.get_database("myhost/3050/db_a")
        db_b = driver_config.get_database("myhost/3051/db_b")
        eq_(db_a.server.value, "myhost/3050")
        eq_(db_b.server.value, "myhost/3051")

    def test_default_port_when_omitted(self):
        FBDialect_firebird().create_connect_args(
            make_url("firebird+firebird://u:p@otherhost/db_c")
        )
        srv = driver_config.get_server("otherhost/3050")
        assert srv is not None
        eq_(srv.port.value, "3050")

    def test_ipv6_literal_host_distinct_ports(self):
        # IPv6 literal hosts on the same address but different ports must
        # produce distinct registrations. The "/" separator keeps the key
        # unambiguous without bracket-wrapping the embedded colons
        # (issue #69 review comment).
        dialect = FBDialect_firebird()

        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@[::1]:3050/db_v6a")
        )
        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@[::1]:3051/db_v6b")
        )

        srv_a = driver_config.get_server("::1/3050")
        srv_b = driver_config.get_server("::1/3051")
        assert srv_a is not None and srv_b is not None
        assert srv_a is not srv_b
        eq_(srv_a.host.value, "::1")
        eq_(srv_b.host.value, "::1")
        eq_(srv_a.port.value, "3050")
        eq_(srv_b.port.value, "3051")

        db_a = driver_config.get_database("::1/3050/db_v6a")
        db_b = driver_config.get_database("::1/3051/db_v6b")
        eq_(db_a.server.value, "::1/3050")
        eq_(db_b.server.value, "::1/3051")

    def test_ipv6_literal_host_default_port(self):
        FBDialect_firebird().create_connect_args(
            make_url("firebird+firebird://u:p@[2001:db8::1]/db_v6c")
        )
        srv = driver_config.get_server("2001:db8::1/3050")
        assert srv is not None
        eq_(srv.host.value, "2001:db8::1")
        eq_(srv.port.value, "3050")

    def test_distinct_hosts_distinct_servers(self):
        # Different hosts (same port) must not collide on the registry key.
        dialect = FBDialect_firebird()

        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@host_one:3050/db_h1")
        )
        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@host_two:3050/db_h2")
        )

        srv_1 = driver_config.get_server("host_one/3050")
        srv_2 = driver_config.get_server("host_two/3050")
        assert srv_1 is not None and srv_2 is not None
        assert srv_1 is not srv_2
        eq_(srv_1.host.value, "host_one")
        eq_(srv_2.host.value, "host_two")

    def test_same_database_path_distinct_servers(self):
        # Two engines pointing at the same database path on different servers
        # must get distinct driver_config database registrations, so neither
        # clobbers the other's server mapping (A7).
        dialect = FBDialect_firebird()

        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@host_a:3050/shared.fdb")
        )
        dialect.create_connect_args(
            make_url("firebird+firebird://u:p@host_b:3050/shared.fdb")
        )

        db_a = driver_config.get_database("host_a/3050/shared.fdb")
        db_b = driver_config.get_database("host_b/3050/shared.fdb")
        assert db_a is not None and db_b is not None
        assert db_a is not db_b
        # Each registration keeps its own server mapping (no clobbering)...
        eq_(db_a.server.value, "host_a/3050")
        eq_(db_b.server.value, "host_b/3050")
        # ...and both resolve to the same real database path.
        eq_(db_a.database.value, "shared.fdb")
        eq_(db_b.database.value, "shared.fdb")

    def test_connect_args_uses_registered_database_key(self):
        # The database name handed to .connect() must be the registered config
        # key, so firebird-driver resolves the server/path from driver_config.
        _, opts = FBDialect_firebird().create_connect_args(
            make_url("firebird+firebird://u:p@somehost:3050/mydata.fdb")
        )
        eq_(opts["database"], "somehost/3050/mydata.fdb")
        assert "host" not in opts
        assert "port" not in opts


class PublicExportsTest(fixtures.TestBase):
    """The package re-exports the Firebird column types so users can do
    ``from sqlalchemy_firebird import FBVARCHAR, INT128, ...`` (G1)."""

    def test_types_are_exported(self):
        sfb = sqlalchemy_firebird_pkg
        for name in (
            "FBVARCHAR",
            "FBBLOB",
            "FBINT128",
            "FBDECFLOAT",
            "FBUUID",
            "FBNUMERIC",
            "FBTIMESTAMP",
        ):
            is_true(hasattr(sfb, name), f"{name} not exported")
            eq_(getattr(sfb, name), getattr(fb_types, name))

    def test_unprefixed_aliases(self):
        sfb = sqlalchemy_firebird_pkg
        eq_(sfb.INT128, fb_types.FBINT128)
        eq_(sfb.DECFLOAT, fb_types.FBDECFLOAT)

    def test_insert_construct_exported(self):
        # The Firebird UPDATE OR INSERT construct is part of the public API
        # (F1): ``from sqlalchemy_firebird import insert``.
        sfb = sqlalchemy_firebird_pkg
        from sqlalchemy_firebird.dml import Insert as _Insert

        is_true("insert" in sfb.__all__)
        is_true("Insert" in sfb.__all__)
        is_true(sfb.Insert is _Insert)
        t = Table("uoi_export", MetaData(), Column("id", Integer))
        is_true(isinstance(sfb.insert(t), _Insert))

    def test_all_covers_public_types(self):
        sfb = sqlalchemy_firebird_pkg
        # Everything advertised in __all__ must be importable.
        for name in sfb.__all__:
            is_true(hasattr(sfb, name), f"{name} in __all__ but missing")
        # Every public (FB-prefixed) type in types.py must be exported, so a
        # newly added type can't silently go unexported.
        public_types = [n for n in dir(fb_types) if n.startswith("FB")]
        missing = [n for n in public_types if n not in sfb.__all__]
        eq_(missing, [])


class DocumentedKwargsTest(fixtures.TestBase):
    """The dialect-specific construct arguments named in the dialect docs
    must stay registered (G2)."""

    def test_index_kwargs(self):
        from sqlalchemy import Index

        m = MetaData()
        t = Table("dk_t", m, Column("c", Integer))
        ix = Index(
            "dk_ix",
            t.c.c,
            firebird_descending=True,
            firebird_where=t.c.c > 0,
        )
        eq_(ix.dialect_options["firebird"]["descending"], True)
        is_true(ix.dialect_options["firebird"]["where"] is not None)

    def test_table_on_commit_kwarg(self):
        t = Table(
            "dk_gtt",
            MetaData(),
            Column("c", Integer),
            firebird_on_commit="PRESERVE ROWS",
        )
        eq_(t.dialect_options["firebird"]["on_commit"], "PRESERVE ROWS")


class CapabilityFlagsTest(fixtures.TestBase):
    """The version capability flags (J1) are the single source of truth for
    version-gated features. They map cleanly to FB4+/FB5+ and assume a modern
    server when the version is unknown (bare compile)."""

    _fb4 = (
        "_has_identity_always",
        "_has_binary_types",
        "_has_time_zone_types",
        "_has_int128",
        "_has_decfloat",
        "_has_overriding",
    )
    _fb5 = (
        "_has_partial_indexes",
        "_has_rdb_keywords",
        "_has_dml_order_rows",
    )

    def _dialect(self, svi):
        d = FBDialect_firebird()
        d.server_version_info = svi
        return d

    def test_firebird_3(self):
        d = self._dialect((3, 0, 0))
        for name in self._fb4 + self._fb5:
            is_false(getattr(d, name), name)

    def test_firebird_4(self):
        d = self._dialect((4, 0, 0))
        for name in self._fb4:
            is_true(getattr(d, name), name)
        for name in self._fb5:
            is_false(getattr(d, name), name)

    def test_firebird_5(self):
        d = self._dialect((5, 0, 0))
        for name in self._fb4 + self._fb5:
            is_true(getattr(d, name), name)

    def test_unknown_version_assumes_modern(self):
        # Before initialize() (e.g. bare stringify) the version is unknown;
        # flags default to True so modern SQL is emitted.
        d = self._dialect(None)
        for name in self._fb4 + self._fb5:
            is_true(getattr(d, name), name)


class ReservedWordsTest(fixtures.TestBase):
    """On Firebird 5.0+ the preparer's reserved words come from the live
    RDB$KEYWORDS table; older servers use the bundled fb_info30/40 sets (J5)."""

    __backend__ = True

    @testing.requires.firebird_5_or_higher
    def test_reserved_words_from_live_rdb_keywords(self, connection):
        live = {
            row[0].strip().lower()
            for row in connection.exec_driver_sql(
                "SELECT rdb$keyword_name FROM rdb$keywords "
                "WHERE rdb$keyword_reserved = TRUE"
            )
        }
        is_true(len(live) > 0)
        eq_(connection.dialect.identifier_preparer.reserved_words, live)

    def test_reserved_word_is_quoted(self, connection):
        # A reserved identifier is quoted on every supported version.
        eq_(
            connection.dialect.identifier_preparer.quote("select"),
            '"select"',
        )


class DialectNameTest(fixtures.TestBase):
    def test_dialect_name_is_backend_name(self):
        # By SQLAlchemy convention dialect.name is the backend name
        # ("firebird"), not "firebird.firebird". Tools such as Alembic branch
        # on dialect.name == "firebird" (A5 / H3).
        eq_(FBDialect_firebird.name, "firebird")
        eq_(FBDialect_firebird.driver, "firebird")

    def test_created_engine_dialect_name(self):
        # create_engine() does not connect; it only loads the dialect.
        eng = create_engine("firebird+firebird://sysdba@/path/to/db.fdb")
        eq_(eng.dialect.name, "firebird")
        eq_(eng.name, "firebird")


class TransactionOptionsTest(fixtures.TablesTest):
    """Behavioral coverage for the TPB-based isolation / read-only support
    (proves the options are actually applied by Firebird, beyond being
    reported back by get_isolation_level / get_readonly)."""

    __backend__ = True
    run_deletes = "each"

    @classmethod
    def define_tables(cls, metadata):
        Table(
            "tx_opt",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=False),
            Column("data", String(50)),
        )

    def test_readonly_blocks_writes_and_reports_state(self):
        t = self.tables.tx_opt
        with config.db.begin() as conn:
            conn.execute(t.insert(), {"id": 1, "data": "x"})

        with config.db.connect() as conn:
            ro = conn.execution_options(firebird_readonly=True)
            is_true(ro.dialect.get_readonly(ro.connection.dbapi_connection))

            # Reads succeed inside a read-only transaction...
            eq_(ro.scalar(select(t.c.data).where(t.c.id == 1)), "x")

            # ...but Firebird rejects writes.
            assert_raises(
                exc.DBAPIError,
                ro.execute,
                t.insert(),
                {"id": 2, "data": "y"},
            )
            ro.rollback()

        # The rejected write must not have landed.
        with config.db.connect() as conn:
            eq_(conn.scalar(select(func.count()).select_from(t)), 1)

    def test_snapshot_vs_read_committed_visibility(self):
        # REPEATABLE READ maps to Firebird SNAPSHOT (a stable view), while
        # READ COMMITTED sees other transactions' commits on each statement.
        t = self.tables.tx_opt
        with config.db.begin() as conn:
            conn.execute(t.insert(), {"id": 1, "data": "a"})

        snapshot = config.db.connect().execution_options(
            isolation_level="REPEATABLE READ"
        )
        read_committed = config.db.connect().execution_options(
            isolation_level="READ COMMITTED"
        )
        try:
            count = select(func.count()).select_from(t)
            # Both transactions start and see the single seeded row.
            eq_(snapshot.scalar(count), 1)
            eq_(read_committed.scalar(count), 1)

            # A separate connection commits a new row.
            with config.db.begin() as writer:
                writer.execute(t.insert(), {"id": 2, "data": "b"})

            # SNAPSHOT keeps its stable view; READ COMMITTED sees the commit.
            eq_(snapshot.scalar(count), 1)
            eq_(read_committed.scalar(count), 2)
        finally:
            snapshot.close()
            read_committed.close()

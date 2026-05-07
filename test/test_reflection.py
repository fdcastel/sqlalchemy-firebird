import pytest
from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import exc
from sqlalchemy import ForeignKey
from sqlalchemy import Identity
from sqlalchemy import Index
from sqlalchemy import inspect
from sqlalchemy import Integer
from sqlalchemy import join
from sqlalchemy import MetaData
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy import Sequence
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy import UniqueConstraint
from sqlalchemy.schema import CreateIndex
from sqlalchemy.sql.schema import CheckConstraint
from sqlalchemy.testing import AssertsCompiledSQL
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import AssertsExecutionResults
from sqlalchemy.testing.assertions import ComparesIndexes
from sqlalchemy.testing.assertions import eq_
from sqlalchemy.testing.assertions import is_
from sqlalchemy.testing.assertions import is_true

from sqlalchemy import MetaData
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy.testing import AssertsExecutionResults
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures

from sqlalchemy_firebird.types import FBINTEGER

#
# Tests from postgresql/test_reflection.py
#


class ReflectionFixtures:
    @testing.fixture(
        params=[
            ("engine", True),
            ("connection", True),
            ("engine", False),
            ("connection", False),
        ]
    )
    def inspect_fixture(self, request, metadata, testing_engine):
        engine, future = request.param

        eng = testing_engine(future=future)

        conn = eng.connect()

        if engine == "connection":
            yield inspect(eng), conn
        else:
            yield inspect(conn), conn

        conn.close()


class DomainReflectionTest(fixtures.TestBase, AssertsExecutionResults):
    """Test Firebird domains"""

    __backend__ = True

    @classmethod
    def setup_test_class(cls):
        with testing.db.begin() as con:
            for ddl in [
                "CREATE DOMAIN testdomain AS INTEGER DEFAULT 42 NOT NULL",
                "CREATE DOMAIN testdomain2 AS INTEGER DEFAULT 0",
                'CREATE DOMAIN "Quoted.Domain" AS INTEGER DEFAULT 0',
                "CREATE DOMAIN nullable_domain AS VARCHAR(30) CHECK (VALUE IN('FOO', 'BAR'))",
                "CREATE DOMAIN not_nullable_domain AS VARCHAR(30) NOT NULL",
                "CREATE DOMAIN my_int AS int CHECK (VALUE > 1)",
            ]:
                try:
                    con.exec_driver_sql(ddl)
                except exc.DBAPIError as e:
                    if "already exists" not in str(e):
                        raise e
            con.exec_driver_sql(
                "CREATE TABLE testtable (question integer, answer testdomain)"
            )
            con.exec_driver_sql(
                "CREATE TABLE testtable2(question "
                "integer, answer testdomain2, anything integer)"
            )
            con.exec_driver_sql(
                'CREATE TABLE quote_test (id integer, data "Quoted.Domain")'
            )
            con.exec_driver_sql(
                "CREATE TABLE nullable_domain_test "
                "(not_nullable_domain_col nullable_domain not null,"
                "nullable_local not_nullable_domain)"
            )

    @classmethod
    def teardown_test_class(cls):
        with testing.db.begin() as con:
            con.exec_driver_sql("DROP TABLE nullable_domain_test")
            con.exec_driver_sql("DROP TABLE quote_test")
            con.exec_driver_sql("DROP TABLE testtable2")
            con.exec_driver_sql("DROP TABLE testtable")
            con.exec_driver_sql("DROP DOMAIN my_int")
            con.exec_driver_sql("DROP DOMAIN not_nullable_domain")
            con.exec_driver_sql("DROP DOMAIN nullable_domain")
            con.exec_driver_sql('DROP DOMAIN "Quoted.Domain"')
            con.exec_driver_sql("DROP DOMAIN testdomain2")
            con.exec_driver_sql("DROP DOMAIN testdomain")

    def test_table_is_reflected(self, connection):
        metadata = MetaData()
        table1 = Table("testtable", metadata, autoload_with=connection)
        eq_(
            set(table1.columns.keys()),
            {"question", "answer"},
            "Columns of reflected table didn't equal expected columns",
        )
        assert isinstance(table1.c.answer.type, Integer)

        table2 = Table(
            "testtable2",
            metadata,
            autoload_with=connection,
        )
        eq_(
            set(table2.columns.keys()),
            {"question", "answer", "anything"},
            "Columns of reflected table didn't equal expected columns",
        )
        assert isinstance(table2.c.anything.type, Integer)

    def test_nullable_from_domain(self, connection):
        metadata = MetaData()
        table = Table(
            "nullable_domain_test", metadata, autoload_with=connection
        )
        is_(table.c.not_nullable_domain_col.nullable, False)
        is_(table.c.nullable_local.nullable, False)

    def test_domain_is_reflected(self, connection):
        metadata = MetaData()
        table1 = Table("testtable", metadata, autoload_with=connection)
        eq_(
            str(table1.columns.answer.server_default.arg),
            "42",
            "Reflected default value didn't equal expected value",
        )
        assert not table1.columns.answer.nullable, (
            "Expected reflected column to not be nullable."
        )

        table2 = Table(
            "testtable2",
            metadata,
            autoload_with=connection,
        )
        eq_(
            str(table2.columns.answer.server_default.arg),
            "0",
            "Reflected default value didn't equal expected value",
        )
        assert table2.columns.answer.nullable, (
            "Expected reflected column to be nullable."
        )

    def test_quoted_domain_is_reflected(self, connection):
        metadata = MetaData()
        table = Table("quote_test", metadata, autoload_with=connection)
        eq_(table.c.data.type.__class__, FBINTEGER)

    @property
    def all_domains(self):
        return [
            {
                "name": "my_int",
                "nullable": True,
                "default": None,
                "check": "VALUE > 1",
                "comment": None,
            },
            {
                "name": "not_nullable_domain",
                "nullable": False,
                "default": None,
                "check": None,
                "comment": None,
            },
            {
                "name": "nullable_domain",
                "nullable": True,
                "default": None,
                "check": "VALUE IN('FOO', 'BAR')",
                "comment": None,
            },
            {
                "name": "Quoted.Domain",
                "nullable": True,
                "default": "0",
                "check": None,
                "comment": None,
            },
            {
                "name": "testdomain",
                "nullable": False,
                "default": "42",
                "check": None,
                "comment": None,
            },
            {
                "name": "testdomain2",
                "nullable": True,
                "default": "0",
                "check": None,
                "comment": None,
            },
        ]

    def test_inspect_domains(self, connection):
        inspector = inspect(connection)
        ds = inspector.get_domains()
        eq_(ds, self.all_domains)


class ReflectionTest(
    ReflectionFixtures, AssertsCompiledSQL, ComparesIndexes, fixtures.TestBase
):
    __backend__ = True

    def test_reflected_primary_key_order(self, metadata, connection):
        meta1 = metadata
        subject = Table(
            "subject",
            meta1,
            Column("p1", Integer, primary_key=True),
            Column("p2", Integer, primary_key=True),
            PrimaryKeyConstraint("p2", "p1"),
        )
        meta1.create_all(connection)
        meta2 = MetaData()
        subject = Table("subject", meta2, autoload_with=connection)
        eq_(subject.primary_key.columns.keys(), ["p2", "p1"])

    def test_pg_weirdchar_reflection(self, metadata, connection):
        meta1 = metadata
        subject = Table(
            "subject", meta1, Column("id$", Integer, primary_key=True)
        )
        referer = Table(
            "referer",
            meta1,
            Column("id", Integer, primary_key=True),
            Column("ref", Integer, ForeignKey("subject.id$")),
        )
        meta1.create_all(connection)
        meta2 = MetaData()
        subject = Table("subject", meta2, autoload_with=connection)
        referer = Table("referer", meta2, autoload_with=connection)
        self.assert_(
            (subject.c["id$"] == referer.c.ref).compare(
                subject.join(referer).onclause
            )
        )

    def test_reflect_default_over_128_chars(self, metadata, connection):
        Table(
            "t",
            metadata,
            Column("x", String(200), server_default="abcd" * 40),
        ).create(connection)

        m = MetaData()
        t = Table("t", m, autoload_with=connection)
        eq_(
            t.c.x.server_default.arg.text,
            "'%s'" % ("abcd" * 40),
        )

    def test_has_temporary_table(self, metadata, connection):
        assert not inspect(connection).has_table("some_temp_table")
        user_tmp = Table(
            "some_temp_table",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(50)),
            prefixes=["GLOBAL TEMPORARY"],
        )
        user_tmp.create(connection)
        assert inspect(connection).has_table("some_temp_table")

    def test_cross_schema_reflection_one(self, metadata, connection):
        meta1 = metadata

        users = Table(
            "test_schema$users",
            meta1,
            Column("user_id", Integer, primary_key=True),
            Column("user_name", String(30), nullable=False),
        )
        addresses = Table(
            "test_schema$email_addresses",
            meta1,
            Column("address_id", Integer, primary_key=True),
            Column("remote_user_id", Integer, ForeignKey(users.c.user_id)),
            Column("email_address", String(20)),
        )
        meta1.create_all(connection)
        meta2 = MetaData()
        addresses = Table(
            "test_schema$email_addresses",
            meta2,
            autoload_with=connection,
        )
        users = Table("test_schema$users", meta2, must_exist=True)
        j = join(users, addresses)
        self.assert_(
            (users.c.user_id == addresses.c.remote_user_id).compare(j.onclause)
        )

    def test_cross_schema_reflection_two(self, metadata, connection):
        meta1 = metadata
        subject = Table(
            "subject", meta1, Column("id", Integer, primary_key=True)
        )
        referer = Table(
            "referer",
            meta1,
            Column("id", Integer, primary_key=True),
            Column("ref", Integer, ForeignKey("subject.id")),
            schema="test_schema",
        )
        meta1.create_all(connection)
        meta2 = MetaData()
        subject = Table("subject", meta2, autoload_with=connection)
        referer = Table(
            "referer", meta2, schema="test_schema", autoload_with=connection
        )
        self.assert_(
            (subject.c.id == referer.c.ref).compare(
                subject.join(referer).onclause
            )
        )

    def test_cross_schema_reflection_three(self, metadata, connection):
        meta1 = metadata
        subject = Table(
            "test_schema_2$subject",
            meta1,
            Column("id", Integer, primary_key=True),
        )
        referer = Table(
            "test_schema$referer",
            meta1,
            Column("id", Integer, primary_key=True),
            Column("ref", Integer, ForeignKey("test_schema_2$subject.id")),
        )
        meta1.create_all(connection)
        meta2 = MetaData()
        subject = Table(
            "test_schema_2$subject",
            meta2,
            autoload_with=connection,
        )
        referer = Table(
            "test_schema$referer",
            meta2,
            autoload_with=connection,
        )
        self.assert_(
            (subject.c.id == referer.c.ref).compare(
                subject.join(referer).onclause
            )
        )

    def test_cross_schema_reflection_four(self, metadata, connection):
        meta1 = metadata
        subject = Table(
            "test_schema_2$subject",
            meta1,
            Column("id", Integer, primary_key=True),
        )
        referer = Table(
            "test_schema$referer",
            meta1,
            Column("id", Integer, primary_key=True),
            Column("ref", Integer, ForeignKey("test_schema_2$subject.id")),
        )
        meta1.create_all(connection)

        connection.detach()

        meta2 = MetaData()
        subject = Table(
            "test_schema_2$subject",
            meta2,
            autoload_with=connection,
        )
        referer = Table(
            "test_schema$referer",
            meta2,
            autoload_with=connection,
        )
        self.assert_(
            (subject.c.id == referer.c.ref).compare(
                subject.join(referer).onclause
            )
        )

    def test_cross_schema_reflection_metadata_uses_schema(
        self, metadata, connection
    ):
        # test [ticket:3716]

        Table(
            "test_schema$some_table",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("sid", Integer, ForeignKey("some_other_table.id")),
        )
        Table(
            "some_other_table",
            metadata,
            Column("id", Integer, primary_key=True),
        )
        metadata.create_all(connection)
        meta2 = MetaData()
        meta2.reflect(connection)

        eq_(
            set(meta2.tables),
            {"some_other_table", "test_schema$some_table"},
        )

    @pytest.mark.skip(
        reason="Wait for https://github.com/sqlalchemy/sqlalchemy/issues/10789 in SQLAlchemy 2.1"
    )
    def test_uppercase_lowercase_table(self, metadata, connection):
        a_table = Table("a", metadata, Column("x", Integer))
        A_table = Table("A", metadata, Column("x", Integer))

        A_table.create(connection, checkfirst=True)
        assert inspect(connection).has_table("A")
        a_table.create(connection)
        assert inspect(connection).has_table("a")
        assert not inspect(connection).has_table("A")

    @pytest.mark.skip(
        reason="Wait for https://github.com/sqlalchemy/sqlalchemy/issues/10789 in SQLAlchemy 2.1"
    )
    def test_uppercase_lowercase_sequence(self, connection):
        a_seq = Sequence("a")
        A_seq = Sequence("A")

        a_seq.create(connection)
        assert connection.dialect.has_sequence(connection, "a")
        assert not connection.dialect.has_sequence(connection, "A")
        A_seq.create(connection, checkfirst=True)
        assert connection.dialect.has_sequence(connection, "A")

        a_seq.drop(connection)
        A_seq.drop(connection)

    def test_index_reflection(self, metadata, connection):
        """Reflecting expression-based indexes works"""

        Table(
            "party",
            metadata,
            Column("id", String(10), nullable=False),
            Column("name", String(20), index=True),
            Column("aname", String(20)),
            Column("other", String(20)),
        )
        metadata.create_all(connection)
        connection.exec_driver_sql(
            """
            CREATE DESCENDING INDEX idx3 ON party
                COMPUTED BY (LOWER(name)||other||LOWER(aname))
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX idx1 ON party COMPUTED BY (id||name||other||CAST(id AS VARCHAR(30)))"
        )

        if testing.requires.partial_indices.enabled:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX idx2 ON party (id) WHERE name = 'test'"
            )

        expected = [
            {
                "name": "idx1",
                "column_names": ["id", "name", "other", None],
                "unique": False,
                "expressions": [
                    "id",
                    "name",
                    "other",
                    "CAST(id AS VARCHAR(30))",
                ],
                "dialect_options": {
                    "firebird_descending": False,
                    "firebird_where": None,
                },
            },
            {
                "name": "idx2",
                "column_names": ["id"],
                "unique": True,
                "dialect_options": {
                    "firebird_descending": False,
                    "firebird_where": "name = 'test'",
                },
            },
            {
                "name": "idx3",
                "column_names": [None, "other", None],
                "unique": False,
                "expressions": ["LOWER(name)", "other", "LOWER(aname)"],
                "dialect_options": {
                    "firebird_descending": True,
                    "firebird_where": None,
                },
            },
            {
                "name": "ix_party_name",
                "column_names": ["name"],
                "unique": False,
                "dialect_options": {
                    "firebird_descending": False,
                    "firebird_where": None,
                },
            },
        ]

        if not testing.requires.partial_indices.enabled:
            expected.pop(1)

        insp = inspect(connection)
        eq_(insp.get_indexes("party"), expected)

        m2 = MetaData()
        t2 = Table("party", m2, autoload_with=connection)
        self.compare_table_index_with_expected(t2, expected, "firebird")

    @testing.requires.partial_indices
    def test_index_reflection_partial(self, metadata, connection):
        """Reflect the filter definition on partial indexes"""

        metadata = metadata

        t1 = Table(
            "table1",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("name", String(20)),
            Column("x", Integer),
        )
        Index("idx1", t1.c.id, firebird_where=t1.c.name == "test")
        Index("idx2", t1.c.id, firebird_where=t1.c.x >= 5)

        metadata.create_all(connection)

        ind = connection.dialect.get_indexes(connection, t1.name, None)

        partial_definitions = []
        for ix in ind:
            if "dialect_options" in ix:
                partial_definitions.append(
                    ix["dialect_options"]["firebird_where"]
                )

        eq_(
            sorted(partial_definitions),
            ["name = 'test'", "x >= 5"],
        )

        t2 = Table("table1", MetaData(), autoload_with=connection)
        idx = list(sorted(t2.indexes, key=lambda idx: idx.name))[0]

        self.assert_compile(
            CreateIndex(idx),
            "CREATE INDEX idx1 ON table1 (id) WHERE name = 'test'",
        )

    def test_foreign_key_option_inspection(self, metadata, connection):
        Table(
            "person",
            metadata,
            Column("id", String(length=32), nullable=False, primary_key=True),
            Column(
                "company_id",
                ForeignKey(
                    "company.id",
                    name="person_company_id_fkey",
                    onupdate="NO ACTION",  # None
                    ondelete="CASCADE",
                ),
            ),
        )
        Table(
            "company",
            metadata,
            Column("id", String(length=32), nullable=False, primary_key=True),
            Column("name", String(length=255)),
            Column(
                "industry_id",
                ForeignKey(
                    "industry.id",
                    name="company_industry_id_fkey",
                    onupdate="SET DEFAULT",
                    ondelete="SET NULL",
                ),
            ),
        )
        Table(
            "industry",
            metadata,
            Column("id", Integer(), nullable=False, primary_key=True),
            Column("name", String(length=255)),
        )
        fk_ref = {
            "person_company_id_fkey": {
                "name": "person_company_id_fkey",
                "constrained_columns": ["company_id"],
                "referred_schema": None,
                "referred_table": "company",
                "referred_columns": ["id"],
                "options": {
                    "ondelete": "CASCADE",
                },
            },
            "company_industry_id_fkey": {
                "name": "company_industry_id_fkey",
                "constrained_columns": ["industry_id"],
                "referred_schema": None,
                "referred_table": "industry",
                "referred_columns": ["id"],
                "options": {"onupdate": "SET DEFAULT", "ondelete": "SET NULL"},
            },
        }
        metadata.create_all(connection)
        inspector = inspect(connection)
        fks = inspector.get_foreign_keys(
            "person"
        ) + inspector.get_foreign_keys("company")
        for fk in fks:
            eq_(fk, fk_ref[fk["name"]])

    def test_reflection_with_unique_constraint(self, metadata, connection):
        insp = inspect(connection)

        meta = metadata
        uc_table = Table(
            "fbsql_uc",
            meta,
            Column("a", String(10)),
            UniqueConstraint("a", name="uc_a"),
        )

        uc_table.create(connection)

        indexes = {i["name"] for i in insp.get_indexes("fbsql_uc")}
        constraints = {
            i["name"] for i in insp.get_unique_constraints("fbsql_uc")
        }

        self.assert_("uc_a" in indexes)
        self.assert_("uc_a" in constraints)

        reflected = Table("fbsql_uc", MetaData(), autoload_with=connection)

        indexes = {i.name for i in reflected.indexes}
        constraints = {uc.name for uc in reflected.constraints}

        self.assert_("uc_a" in indexes)
        self.assert_("uc_a" in constraints)

    def test_reflect_unique_index(self, metadata, connection):
        insp = inspect(connection)

        meta = metadata

        # a unique index OTOH we are able to detect is an index
        # and not a unique constraint
        uc_table = Table(
            "fbsql_uc",
            meta,
            Column("a", String(10)),
            Index("ix_a", "a", unique=True),
        )

        uc_table.create(connection)

        indexes = {i["name"]: i for i in insp.get_indexes("fbsql_uc")}
        constraints = {
            i["name"] for i in insp.get_unique_constraints("fbsql_uc")
        }

        self.assert_("ix_a" in indexes)
        assert indexes["ix_a"]["unique"]
        self.assert_("ix_a" not in constraints)

        reflected = Table("fbsql_uc", MetaData(), autoload_with=connection)

        indexes = {i.name: i for i in reflected.indexes}
        constraints = {uc.name for uc in reflected.constraints}

        self.assert_("ix_a" in indexes)
        assert indexes["ix_a"].unique
        self.assert_("ix_a" not in constraints)

    def test_reflect_check_constraint(self, metadata, connection):
        meta = metadata

        Table(
            "fbsql_cc",
            meta,
            Column("a", Integer()),
            Column("b", String),
            CheckConstraint("a > 1 AND a < 5", name="cc1"),
            CheckConstraint("a = 1 OR (a > 2 AND a < 5)", name="cc2"),
            CheckConstraint("b <> 'hi\nim a name   \nyup\n'", name="cc4"),
        )

        meta.create_all(connection)

        reflected = Table("fbsql_cc", MetaData(), autoload_with=connection)

        check_constraints = {
            uc.name: uc.sqltext.text
            for uc in reflected.constraints
            if isinstance(uc, CheckConstraint)
        }

        eq_(
            check_constraints,
            {
                "cc1": "a > 1 AND a < 5",
                "cc2": "a = 1 OR (a > 2 AND a < 5)",
                "cc4": "b <> 'hi\nim a name   \nyup\n'",
            },
        )


class IdentityReflectionTest(fixtures.TablesTest):
    __backend__ = True
    __requires__ = ("identity_columns",)

    _names = ("t1", "T2", "MiXeDCaSe!")

    @classmethod
    def define_tables(cls, metadata):
        for name in cls._names:
            Table(
                name,
                metadata,
                Column(
                    "id1",
                    Integer,
                    Identity(
                        always=True,
                        start=2,
                        increment=3,
                    ),
                ),
                Column("id2", Integer, Identity()),
                Column("id3", BigInteger, Identity()),
                Column("id4", SmallInteger, Identity()),
            )

    @testing.combinations(*_names, argnames="name")
    def test_reflect_identity(self, connection, name):
        firebird_4_or_higher = testing.requires.firebird_4_or_higher.enabled

        insp = inspect(connection)
        expected = dict(
            always=True if firebird_4_or_higher else False,
            start=2,
            increment=3 if firebird_4_or_higher else 1,
        )

        default = dict(
            always=False,
            start=1 if firebird_4_or_higher else 0,
            increment=1,
        )
        cols = insp.get_columns(name)
        for col in cols:
            if col["name"] == "id1":
                is_true("identity" in col)
                eq_(
                    col["identity"],
                    expected,
                )
            elif col["name"] == "id2":
                is_true("identity" in col)
                eq_(col["identity"], default)
            elif col["name"] == "id3":
                is_true("identity" in col)
                eq_(col["identity"], default)
            elif col["name"] == "id4":
                is_true("identity" in col)
                eq_(col["identity"], default)

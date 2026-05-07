from sqlalchemy import Date, Identity, and_
from sqlalchemy import cast
from sqlalchemy import column
from sqlalchemy import Column
from sqlalchemy import Computed
from sqlalchemy import exc
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import Index
from sqlalchemy import BigInteger
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import SmallInteger
from sqlalchemy import schema
from sqlalchemy import select
from sqlalchemy import String
from sqlalchemy import table
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.sql import sqltypes
from sqlalchemy.testing import assert_raises_message
from sqlalchemy.testing import AssertsCompiledSQL
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import eq_ignore_whitespace
from sqlalchemy.types import TypeEngine

import sqlalchemy_firebird.types as FbTypes

from sqlalchemy_firebird.firebird import FBDialect_firebird


class CompileTest(fixtures.TablesTest, AssertsCompiledSQL):
    __dialect__ = FBDialect_firebird()

    def test_alias(self):
        t = table("sometable", column("col1"), column("col2"))
        s = select(t.alias())
        self.assert_compile(
            s,
            "SELECT sometable_1.col1, sometable_1.col2 "
            "FROM sometable AS sometable_1",
        )

    @testing.provide_metadata
    def test_function(self):
        self.assert_compile(
            func.foo(1, 2),
            "foo(CAST(:foo_1 AS INTEGER), CAST(:foo_2 AS INTEGER))",
        )
        self.assert_compile(func.current_time(), "CURRENT_TIME")
        self.assert_compile(func.foo(), "foo")
        t = Table(
            "sometable",
            self.metadata,
            Column("col1", Integer),
            Column("col2", Integer),
        )
        self.assert_compile(
            select(func.max(t.c.col1)),
            "SELECT max(sometable.col1) AS max_1 FROM sometable",
        )

    def test_charset(self):
        """Exercise CHARACTER SET options on string types."""
        columns = [
            (FbTypes.FBCHAR, [1], {}, "CHAR(1)"),
            (
                FbTypes.FBCHAR,
                [1],
                {"charset": "OCTETS"},
                "CHAR(1) CHARACTER SET OCTETS",
            ),
            (FbTypes.FBVARCHAR, [1], {}, "VARCHAR(1)"),
            (
                FbTypes.FBVARCHAR,
                [1],
                {"charset": "OCTETS"},
                "VARCHAR(1) CHARACTER SET OCTETS",
            ),
        ]
        for type_, args, kw, res in columns:
            self.assert_compile(type_(*args, **kw), res)

    def test_quoting_initial_chars(self):
        self.assert_compile(column("_somecol"), '"_somecol"')
        self.assert_compile(column("$somecol"), '"$somecol"')

    #
    # Tests from postgresql/test_compiler.py
    #

    def test_plain_stringify_returning(self):
        t = Table(
            "t",
            MetaData(),
            Column("myid", Integer, primary_key=True),
            Column("name", String, server_default="some str"),
            Column("description", String, default=func.lower("hi")),
        )
        stmt = t.insert().values().return_defaults()
        eq_ignore_whitespace(
            str(stmt.compile()),
            "INSERT INTO t (description) VALUES (lower(:lower_1)) "
            "RETURNING t.myid, t.name, t.description",
        )

    def test_update_returning(self):
        table1 = table(
            "mytable",
            column("myid", Integer),
            column("name", String(128)),
            column("description", String(128)),
        )
        u = (
            update(table1)
            .values(dict(name="foo"))
            .returning(table1.c.myid, table1.c.name)
        )
        self.assert_compile(
            u,
            "UPDATE mytable SET name=CAST(:name AS VARCHAR(128)) "
            "RETURNING mytable.myid, mytable.name",
        )
        u = update(table1).values(dict(name="foo")).returning(table1)
        self.assert_compile(
            u,
            "UPDATE mytable SET name=CAST(:name AS VARCHAR(128)) "
            "RETURNING mytable.myid, mytable.name, "
            "mytable.description",
        )
        u = (
            update(table1)
            .values(dict(name="foo"))
            .returning(func.length(table1.c.name))
        )
        self.assert_compile(
            u,
            "UPDATE mytable SET name=CAST(:name AS VARCHAR(128)) "
            "RETURNING CHAR_LENGTH(mytable.name) AS length_1",
        )

    def test_insert_returning(self):
        table1 = table(
            "mytable",
            column("myid", Integer),
            column("name", String(128)),
            column("description", String(128)),
        )
        i = (
            insert(table1)
            .values(dict(name="foo"))
            .returning(table1.c.myid, table1.c.name)
        )
        self.assert_compile(
            i,
            "INSERT INTO mytable (name) VALUES "
            "(CAST(:name AS VARCHAR(128))) RETURNING mytable.myid, "
            "mytable.name",
        )
        i = insert(table1).values(dict(name="foo")).returning(table1)
        self.assert_compile(
            i,
            "INSERT INTO mytable (name) VALUES "
            "(CAST(:name AS VARCHAR(128))) RETURNING mytable.myid, "
            "mytable.name, mytable.description",
        )
        i = (
            insert(table1)
            .values(dict(name="foo"))
            .returning(func.length(table1.c.name))
        )
        self.assert_compile(
            i,
            "INSERT INTO mytable (name) VALUES "
            "(CAST(:name AS VARCHAR(128))) RETURNING CHAR_LENGTH(mytable.name) "
            "AS length_1",
        )

    @testing.fixture
    def column_expression_fixture(self):
        class MyString(TypeEngine):
            def column_expression(self, column):
                return func.lower(column)

        return table(
            "some_table", column("name", String), column("value", MyString)
        )

    @testing.combinations("columns", "table", argnames="use_columns")
    def test_plain_returning_column_expression(
        self, column_expression_fixture, use_columns
    ):
        """test #8770"""
        table1 = column_expression_fixture

        if use_columns == "columns":
            stmt = insert(table1).returning(table1)
        else:
            stmt = insert(table1).returning(table1.c.name, table1.c.value)

        # Type MyString have render_bind_cast = False
        self.assert_compile(
            stmt,
            'INSERT INTO some_table (name, "value") '
            "VALUES (CAST(:name AS BLOB SUB_TYPE TEXT), :value) RETURNING some_table.name, "
            'lower(some_table."value") AS "value"',
        )

    def test_cast_double_pg_double(self):
        """test #5465:

        test sqlalchemy Double/DOUBLE to Firebird DOUBLE
        """
        d1 = sqltypes.Double

        stmt = select(cast(column("foo"), d1))
        self.assert_compile(
            stmt, "SELECT CAST(foo AS DOUBLE) AS foo FROM rdb$database"
        )

    def test_create_table_with_multiple_options(self):
        m = MetaData()
        tbl = Table(
            "atable",
            m,
            Column("id", Integer),
            prefixes=["GLOBAL TEMPORARY"],
            firebird_on_commit="PRESERVE ROWS",
        )
        self.assert_compile(
            schema.CreateTable(tbl),
            "CREATE GLOBAL TEMPORARY TABLE atable (id INTEGER) "
            "ON COMMIT PRESERVE ROWS",
        )

    def test_create_index_descending(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("data", Integer))

        idx1 = Index("test_idx1", tbl.c.data, firebird_descending=True)
        self.assert_compile(
            schema.CreateIndex(idx1),
            "CREATE DESCENDING INDEX test_idx1 ON testtbl (data)",
        )

    def test_create_partial_index(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("data", Integer))
        idx = Index(
            "test_idx1",
            tbl.c.data,
            firebird_where=and_(tbl.c.data > 5, tbl.c.data < 10),
        )
        idx = Index(
            "test_idx1",
            tbl.c.data,
            firebird_where=and_(tbl.c.data > 5, tbl.c.data < 10),
        )

        # test quoting and all that

        idx2 = Index(
            "test_idx2",
            tbl.c.data,
            firebird_where=and_(tbl.c.data > "a", tbl.c.data < "b's"),
        )
        self.assert_compile(
            schema.CreateIndex(idx),
            "CREATE INDEX test_idx1 ON testtbl (data) "
            "WHERE data > 5 AND data < 10",
        )
        self.assert_compile(
            schema.CreateIndex(idx2),
            "CREATE INDEX test_idx2 ON testtbl (data) "
            "WHERE data > 'a' AND data < 'b''s'",
        )

        idx3 = Index(
            "test_idx2",
            tbl.c.data,
            firebird_where=text("data > 'a' AND data < 'b''s'"),
        )
        self.assert_compile(
            schema.CreateIndex(idx3),
            "CREATE INDEX test_idx2 ON testtbl (data) "
            "WHERE data > 'a' AND data < 'b''s'",
        )

    def test_create_index_with_text_or_composite(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("d1", String), Column("d2", Integer))

        idx = Index("test_idx1", text("x"))
        tbl.append_constraint(idx)

        idx2 = Index("test_idx2", text("y"), tbl.c.d2)

        self.assert_compile(
            schema.CreateIndex(idx),
            "CREATE INDEX test_idx1 ON testtbl COMPUTED BY (x)",
        )
        self.assert_compile(
            schema.CreateIndex(idx2),
            "CREATE INDEX test_idx2 ON testtbl COMPUTED BY (y||d2)",
        )

    def test_create_index_with_multiple_options(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("data", String))

        idx1 = Index(
            "test_idx1",
            tbl.c.data,
            firebird_descending=True,
            firebird_where=and_(tbl.c.data > 5, tbl.c.data < 10),
        )

        self.assert_compile(
            schema.CreateIndex(idx1),
            "CREATE DESCENDING INDEX test_idx1 ON testtbl "
            "(data) "
            "WHERE data > 5 AND data < 10",
        )

    def test_create_index_expr_gets_parens(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("x", Integer), Column("y", Integer))

        idx1 = Index("test_idx1", 5 // (tbl.c.x + tbl.c.y))
        self.assert_compile(
            schema.CreateIndex(idx1),
            "CREATE INDEX test_idx1 ON testtbl COMPUTED BY (5 / (x + y))",
        )

    def test_create_index_literals(self):
        m = MetaData()
        tbl = Table("testtbl", m, Column("data", Integer))

        idx1 = Index("test_idx1", tbl.c.data + 5)
        self.assert_compile(
            schema.CreateIndex(idx1),
            "CREATE INDEX test_idx1 ON testtbl COMPUTED BY (data + 5)",
        )

    def test_substring(self):
        self.assert_compile(
            func.substring("abc", 1, 2),
            "SUBSTRING(CAST(:substring_1 AS BLOB SUB_TYPE TEXT) FROM CAST(:substring_2 AS INTEGER) FOR CAST(:substring_3 AS INTEGER))",
        )
        self.assert_compile(
            func.substring("abc", 1),
            "SUBSTRING(CAST(:substring_1 AS BLOB SUB_TYPE TEXT) FROM CAST(:substring_2 AS INTEGER))",
        )

    def test_for_update(self):
        table1 = table(
            "mytable", column("myid"), column("name"), column("description")
        )

        self.assert_compile(
            table1.select().where(table1.c.myid == 7).with_for_update(),
            "SELECT mytable.myid, mytable.name, mytable.description "
            "FROM mytable WHERE mytable.myid = CAST(:myid_1 AS INTEGER) FOR UPDATE",
        )

        self.assert_compile(
            table1.select()
            .where(table1.c.myid == 7)
            .with_for_update(nowait=True),
            "SELECT mytable.myid, mytable.name, mytable.description "
            "FROM mytable WHERE mytable.myid = CAST(:myid_1 AS INTEGER) FOR UPDATE WITH LOCK",
        )

        self.assert_compile(
            table1.select()
            .where(table1.c.myid == 7)
            .with_for_update(skip_locked=True),
            "SELECT mytable.myid, mytable.name, mytable.description "
            "FROM mytable WHERE mytable.myid = CAST(:myid_1 AS INTEGER) "
            "FOR UPDATE WITH LOCK SKIP LOCKED",
        )

    def test_reserved_words(self):
        table = Table(
            "pg_table",
            MetaData(),
            Column("col1", Integer),
            Column("character_length", Integer),
        )
        x = select(table.c.col1, table.c.character_length)

        self.assert_compile(
            x,
            """SELECT pg_table.col1, pg_table."character_length" FROM pg_table""",
        )

    @testing.provide_metadata
    @testing.combinations(
        ("no_persisted", "ignore"), ("persisted", True), id_="ia"
    )
    def test_column_computed(self, persisted):
        kwargs = {"persisted": persisted} if persisted != "ignore" else {}

        t = Table(
            "t",
            self.metadata,
            Column("x", Integer),
            Column("y", Integer, Computed("x + 2", **kwargs)),
        )
        if persisted == "ignore":
            self.assert_compile(
                schema.CreateTable(t),
                "CREATE TABLE t (x INTEGER, y INTEGER GENERATED "
                "ALWAYS AS (x + 2))",
            )
        else:
            assert_raises_message(
                exc.CompileError,
                "Firebird computed columns do not support a persistence method",
                schema.CreateTable(t).compile,
                dialect=self.__dialect__,
            )

    @testing.combinations(True, False)
    def test_column_identity(self, pk):
        # all other tests are in test_identity_column.py
        m = MetaData()
        t = Table(
            "t",
            m,
            Column(
                "y",
                Integer,
                Identity(always=True, start=4, increment=7),
                primary_key=pk,
            ),
        )
        self.assert_compile(
            schema.CreateTable(t),
            "CREATE TABLE t (y INTEGER GENERATED ALWAYS AS IDENTITY "
            "(START WITH 4 INCREMENT BY 7)%s)"
            % (", PRIMARY KEY (y)" if pk else ""),
        )

    @testing.combinations(
        (Integer, "INTEGER"),
        (BigInteger, "BIGINT"),
        (SmallInteger, "SMALLINT"),
        argnames="type_,expected_sql_type",
    )
    def test_autoincrement_primary_key_uses_column_type(
        self, type_, expected_sql_type
    ):
        # Regression test for issue #88: the autoincrement primary key
        # must use the column's declared type instead of hardcoded INTEGER.
        m = MetaData()
        t = Table(
            "t",
            m,
            Column("id", type_, primary_key=True, autoincrement=True),
        )
        self.assert_compile(
            schema.CreateTable(t),
            "CREATE TABLE t (id %s GENERATED BY DEFAULT AS IDENTITY, "
            "PRIMARY KEY (id))" % expected_sql_type,
        )

    def test_column_identity_null(self):
        # all other tests are in test_identity_column.py
        m = MetaData()
        t = Table(
            "t",
            m,
            Column(
                "y",
                Integer,
                Identity(always=True, start=4, increment=7),
                nullable=True,
            ),
        )
        self.assert_compile(
            schema.CreateTable(t),
            "CREATE TABLE t (y INTEGER GENERATED ALWAYS AS IDENTITY "
            "(START WITH 4 INCREMENT BY 7) NULL)",
        )

    @testing.fixture
    def update_tables(self):
        self.weather = table(
            "weather",
            column("temp_lo", Integer),
            column("temp_hi", Integer),
            column("prcp", Integer),
            column("city", String),
            column("date", Date),
        )
        self.accounts = table(
            "accounts",
            column("sales_id", Integer),
            column("sales_person", Integer),
            column("contact_first_name", String),
            column("contact_last_name", String),
            column("name", String),
        )
        self.salesmen = table(
            "salesmen",
            column("id", Integer),
            column("first_name", String),
            column("last_name", String),
        )
        self.employees = table(
            "employees",
            column("id", Integer),
            column("sales_count", String),
        )

    def test_bitwise_xor(self):
        c1 = column("c1", Integer)
        c2 = column("c2", Integer)
        self.assert_compile(
            select(c1.bitwise_xor(c2)),
            "SELECT BIN_XOR(c1, c2) AS anon_1 FROM rdb$database",
        )

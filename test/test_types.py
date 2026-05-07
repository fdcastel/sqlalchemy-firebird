from sqlalchemy import Column
from sqlalchemy import Float
from sqlalchemy import MetaData
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures
from sqlalchemy.testing import is_instance_of
from sqlalchemy.testing import is_not_none

import sqlalchemy.types as sa_types
import sqlalchemy_firebird.types as fb_types


TEST_CHARSET = "UTF8"
TEST_COLLATION = "UNICODE_CI"


def eq_col(col: Column, expected_type, **expected_options):
    is_instance_of(col.type, expected_type)

    if expected_options:
        for k, v in expected_options.items():
            actual = getattr(col.type, k, None)
            is_not_none(actual, f'"{col}" type does not have "{k}".')
            eq_(actual, v)


class TypesTest(fixtures.TestBase):
    @testing.provide_metadata
    def test_infinite_float(self, connection):
        t = Table("test_infinite_float", self.metadata, Column("data", Float))
        self.metadata.create_all(testing.db)
        connection.execute(t.insert(), dict(data=float("inf")))
        eq_(connection.execute(t.select()).fetchall(), [(float("inf"),)])

    @testing.provide_metadata
    def test_blob_types(self, connection):
        t = Table(
            "test_blob_types",
            self.metadata,
            Column("b", sa_types.BLOB),
            Column("fb", fb_types.FBBLOB),
            Column("fbs", fb_types.FBBLOB(segment_size=100)),
            Column("t", sa_types.TEXT),
            Column("ft", fb_types.FBTEXT),
            Column("fts", fb_types.FBTEXT(segment_size=200)),
            Column("ftc", fb_types.FBTEXT(charset=TEST_CHARSET)),
            Column(
                "ftcc",
                fb_types.FBTEXT(
                    charset=TEST_CHARSET, collation=TEST_COLLATION
                ),
            ),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_blob_types", rm, autoload_with=testing.db)

        eq_col(rt.columns["b"], fb_types.FBBLOB)
        eq_col(rt.columns["fb"], fb_types.FBBLOB)
        eq_col(rt.columns["fbs"], fb_types.FBBLOB, segment_size=100)
        eq_col(rt.columns["t"], sa_types.TEXT)
        eq_col(rt.columns["ft"], fb_types.FBTEXT)
        eq_col(rt.columns["fts"], fb_types.FBTEXT, segment_size=200)
        eq_col(rt.columns["ftc"], fb_types.FBTEXT, charset=TEST_CHARSET)
        eq_col(
            rt.columns["ftcc"],
            fb_types.FBTEXT,
            charset=TEST_CHARSET,
            collation=TEST_COLLATION,
        )

    @testing.provide_metadata
    def test_character_types(self, connection):
        t = Table(
            "test_character_types",
            self.metadata,
            Column("c", sa_types.CHAR),
            Column("cl", sa_types.CHAR(length=10)),
            Column("nc", sa_types.NCHAR),
            Column("ncl", sa_types.NCHAR(length=11)),
            Column("vc", sa_types.VARCHAR),
            Column("vcl", sa_types.VARCHAR(length=12)),
            Column("nvc", sa_types.NVARCHAR),
            Column("nvcl", sa_types.NVARCHAR(length=13)),
            Column("fc", fb_types.FBCHAR),
            Column("fcl", fb_types.FBCHAR(length=20)),
            Column("fclc", fb_types.FBCHAR(length=21, charset=TEST_CHARSET)),
            Column(
                "fclcc",
                fb_types.FBCHAR(
                    length=22, charset=TEST_CHARSET, collation=TEST_COLLATION
                ),
            ),
            Column("fb", fb_types.FBBINARY),
            Column("fbl", fb_types.FBBINARY(length=31)),
            Column("fnc", fb_types.FBNCHAR),
            Column("fncl", fb_types.FBNCHAR(length=32)),
            Column("fvc", fb_types.FBVARCHAR),
            Column("fvcl", fb_types.FBVARCHAR(length=33)),
            Column(
                "fvclc", fb_types.FBVARCHAR(length=34, charset=TEST_CHARSET)
            ),
            Column(
                "fvclcc",
                fb_types.FBVARCHAR(
                    length=35, charset=TEST_CHARSET, collation=TEST_COLLATION
                ),
            ),
            Column("fvb", fb_types.FBVARBINARY),
            Column("fvbl", fb_types.FBVARBINARY(length=36)),
            Column("fnvc", fb_types.FBNVARCHAR),
            Column("fnvcl", fb_types.FBNVARCHAR(length=37)),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_character_types", rm, autoload_with=testing.db)

        (eq_col(rt.columns["c"], fb_types.FBCHAR),)
        (eq_col(rt.columns["cl"], fb_types.FBCHAR, length=10),)
        (eq_col(rt.columns["nc"], fb_types.FBNCHAR),)
        (eq_col(rt.columns["ncl"], fb_types.FBNCHAR, length=11),)
        (eq_col(rt.columns["vc"], fb_types.FBTEXT),)
        (eq_col(rt.columns["vcl"], fb_types.FBVARCHAR, length=12),)
        (
            eq_col(
                rt.columns["nvc"],
                fb_types.FBTEXT,
                charset=fb_types.NATIONAL_CHARSET,
            ),
        )
        (eq_col(rt.columns["nvcl"], fb_types.FBNVARCHAR, length=13),)
        (eq_col(rt.columns["fc"], fb_types.FBCHAR),)
        (eq_col(rt.columns["fcl"], fb_types.FBCHAR, length=20),)
        (
            eq_col(
                rt.columns["fclc"],
                fb_types.FBCHAR,
                length=21,
                charset=TEST_CHARSET,
            ),
        )
        (
            eq_col(
                rt.columns["fclcc"],
                fb_types.FBCHAR,
                length=22,
                charset=TEST_CHARSET,
                collation=TEST_COLLATION,
            ),
        )
        (eq_col(rt.columns["fb"], fb_types.FBBINARY),)
        (eq_col(rt.columns["fbl"], fb_types.FBBINARY, length=31),)
        (eq_col(rt.columns["fnc"], fb_types.FBNCHAR),)
        (eq_col(rt.columns["fncl"], fb_types.FBNCHAR, length=32),)
        eq_col(rt.columns["fvc"], fb_types.FBTEXT, charset=TEST_CHARSET)
        (eq_col(rt.columns["fvcl"], fb_types.FBVARCHAR, length=33),)
        (
            eq_col(
                rt.columns["fvclc"],
                fb_types.FBVARCHAR,
                length=34,
                charset=TEST_CHARSET,
            ),
        )
        (
            eq_col(
                rt.columns["fvclcc"],
                fb_types.FBVARCHAR,
                length=35,
                charset=TEST_CHARSET,
                collation=TEST_COLLATION,
            ),
        )
        eq_col(
            rt.columns["fvb"],
            fb_types.FBTEXT,
            charset=fb_types.BINARY_CHARSET,
        )
        (eq_col(rt.columns["fvbl"], fb_types.FBVARBINARY, length=36),)
        eq_col(
            rt.columns["fnvc"],
            fb_types.FBTEXT,
            charset=fb_types.NATIONAL_CHARSET,
        )
        (eq_col(rt.columns["fnvcl"], fb_types.FBNVARCHAR, length=37),)

    @testing.provide_metadata
    def test_integer_types(self, connection):
        t = Table(
            "test_integer_types",
            self.metadata,
            Column("si", sa_types.SMALLINT),
            Column("i", sa_types.INTEGER),
            Column("bi", sa_types.BIGINT),
            Column("fsi", fb_types.FBSMALLINT),
            Column("fi", fb_types.FBINTEGER),
            Column("fbi", fb_types.FBBIGINT),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_integer_types", rm, autoload_with=testing.db)

        (eq_col(rt.columns["si"], fb_types.FBSMALLINT),)
        (eq_col(rt.columns["i"], fb_types.FBINTEGER),)
        (eq_col(rt.columns["bi"], fb_types.FBBIGINT),)
        (eq_col(rt.columns["fsi"], fb_types.FBSMALLINT),)
        (eq_col(rt.columns["fi"], fb_types.FBINTEGER),)
        (eq_col(rt.columns["fbi"], fb_types.FBBIGINT),)

    @testing.provide_metadata
    @testing.requires.firebird_4_or_higher
    def test_float_types(self, connection):
        t = Table(
            "test_float_types",
            self.metadata,
            Column("f", sa_types.FLOAT),
            Column("f24", sa_types.FLOAT(precision=24)),
            Column("f53", sa_types.FLOAT(precision=53)),
            Column("r", sa_types.REAL),
            Column("dp", sa_types.DOUBLE_PRECISION),
            Column("ff", fb_types.FBFLOAT),
            Column("ff24", fb_types.FBFLOAT(precision=24)),
            Column("ff53", fb_types.FBFLOAT(precision=53)),
            Column("fr", fb_types.FBREAL),
            Column("fdp", fb_types.FBDOUBLE_PRECISION),
            Column("fdf", fb_types.FBDECFLOAT),
            Column("fdf16", fb_types.FBDECFLOAT(precision=16)),
            Column("fdf34", fb_types.FBDECFLOAT(precision=34)),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_float_types", rm, autoload_with=testing.db)

        (eq_col(rt.columns["f"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["f24"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["f53"], fb_types.FBDOUBLE_PRECISION),)
        (eq_col(rt.columns["r"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["dp"], fb_types.FBDOUBLE_PRECISION),)
        (eq_col(rt.columns["ff"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["ff24"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["ff53"], fb_types.FBDOUBLE_PRECISION),)
        (eq_col(rt.columns["fr"], fb_types.FBFLOAT),)
        (eq_col(rt.columns["fdp"], fb_types.FBDOUBLE_PRECISION),)
        (eq_col(rt.columns["fdf"], fb_types.FBDECFLOAT, precision=34),)
        (eq_col(rt.columns["fdf16"], fb_types.FBDECFLOAT, precision=16),)
        (eq_col(rt.columns["fdf34"], fb_types.FBDECFLOAT, precision=34),)

    @testing.provide_metadata
    def test_fixed_types(self, connection):
        t = Table(
            "test_fixed_types",
            self.metadata,
            Column("n4", sa_types.NUMERIC(precision=4, scale=2)),
            Column("d4", sa_types.DECIMAL(precision=4, scale=2)),
            Column("n9", sa_types.NUMERIC(precision=9, scale=3)),
            Column("d9", sa_types.DECIMAL(precision=9, scale=3)),
            Column("n18", sa_types.NUMERIC(precision=18, scale=4)),
            Column("d18", sa_types.DECIMAL(precision=18, scale=4)),
            Column("fn4", fb_types.FBNUMERIC(precision=4, scale=2)),
            Column("fd4", fb_types.FBDECIMAL(precision=4, scale=2)),
            Column("fn9", fb_types.FBNUMERIC(precision=9, scale=3)),
            Column("fd9", fb_types.FBDECIMAL(precision=9, scale=3)),
            Column("fn18", fb_types.FBNUMERIC(precision=18, scale=4)),
            Column("fd18", fb_types.FBDECIMAL(precision=18, scale=4)),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_fixed_types", rm, autoload_with=testing.db)

        (eq_col(rt.columns["n4"], fb_types.FBNUMERIC, precision=4, scale=2),)
        (eq_col(rt.columns["d4"], fb_types.FBDECIMAL, precision=4, scale=2),)
        (eq_col(rt.columns["n9"], fb_types.FBNUMERIC, precision=9, scale=3),)
        (eq_col(rt.columns["d9"], fb_types.FBDECIMAL, precision=9, scale=3),)
        (eq_col(rt.columns["n18"], fb_types.FBNUMERIC, precision=18, scale=4),)
        (eq_col(rt.columns["d18"], fb_types.FBDECIMAL, precision=18, scale=4),)
        (eq_col(rt.columns["fn4"], fb_types.FBNUMERIC, precision=4, scale=2),)
        (eq_col(rt.columns["fd4"], fb_types.FBDECIMAL, precision=4, scale=2),)
        (eq_col(rt.columns["fn9"], fb_types.FBNUMERIC, precision=9, scale=3),)
        (eq_col(rt.columns["fd9"], fb_types.FBDECIMAL, precision=9, scale=3),)
        (
            eq_col(
                rt.columns["fn18"], fb_types.FBNUMERIC, precision=18, scale=4
            ),
        )
        (
            eq_col(
                rt.columns["fd18"], fb_types.FBDECIMAL, precision=18, scale=4
            ),
        )

    @testing.provide_metadata
    @testing.requires.firebird_4_or_higher
    def test_fb4_types(self, connection):
        t = Table(
            "test_fb4_types",
            self.metadata,
            Column("n38", sa_types.NUMERIC(precision=38, scale=8)),
            Column("d38", sa_types.DECIMAL(precision=38, scale=8)),
            Column("fli", fb_types.FBINT128),
            Column("fn38", fb_types.FBNUMERIC(precision=38, scale=8)),
            Column("fd38", fb_types.FBDECIMAL(precision=38, scale=8)),
        )
        self.metadata.create_all(testing.db)

        rm = MetaData()
        rt = Table("test_fb4_types", rm, autoload_with=testing.db)

        (eq_col(rt.columns["n38"], fb_types.FBNUMERIC, precision=38, scale=8),)
        (eq_col(rt.columns["d38"], fb_types.FBDECIMAL, precision=38, scale=8),)
        (eq_col(rt.columns["fli"], fb_types.FBINT128),)
        (
            eq_col(
                rt.columns["fn38"], fb_types.FBNUMERIC, precision=38, scale=8
            ),
        )
        (
            eq_col(
                rt.columns["fd38"], fb_types.FBDECIMAL, precision=38, scale=8
            ),
        )

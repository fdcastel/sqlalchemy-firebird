from sqlalchemy import Column, Integer, select
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures

import sqlalchemy_firebird.types as fb_types

TEST_UNICODE = "próf-áêïôù-🗄️.fdb"
TEST_BINARY = TEST_UNICODE.encode("utf-8")

# firebird-driver returns BlobReader objects for BLOBs larger than
# stream_blob_threshold (default 64 KiB). Use 200 KiB to trigger the
# streaming path that issue #58 was about.
LARGE_BINARY = b"\xa5" * (200 * 1024)
LARGE_TEXT = ("loremipsum-" * 20_000)[: 200 * 1024]


class IssuesTest(fixtures.TestBase):
    @testing.provide_metadata
    @testing.combinations(
        (fb_types.FBTEXT, "hi"),
        (fb_types.FBTEXT, TEST_UNICODE),
        (fb_types.FBBLOB, TEST_BINARY),
        argnames="type_, expected",
    )
    def test_issue_76(self, connection, type_, expected):
        metadata = self.metadata

        the_blob = Table("the_blob", metadata, Column("the_value", type_))
        metadata.create_all(testing.db)

        connection.execute(
            the_blob.insert().values(
                dict(
                    the_value=expected,
                )
            )
        )

        eq_(
            connection.execute(select(the_blob.c.the_value)).scalar(),
            expected,
        )

    @testing.provide_metadata
    @testing.combinations(
        (fb_types.FBBLOB, LARGE_BINARY),
        (fb_types.FBTEXT, LARGE_TEXT),
        argnames="type_, expected",
        id_="na",
    )
    def test_issue_58_large_blob(self, connection, type_, expected):
        # Regression test for issue #58: BLOBs above firebird-driver's
        # stream_blob_threshold were returned as BlobReader objects that
        # SQLAlchemy could not consume (and which got closed alongside
        # the cursor before the result processors ran).
        metadata = self.metadata

        large_blob = Table(
            "large_blob",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("the_value", type_),
        )
        metadata.create_all(testing.db)

        connection.execute(
            large_blob.insert().values(id=1, the_value=expected)
        )

        result = connection.execute(select(large_blob.c.the_value)).scalar()

        eq_(type(result), type(expected))
        eq_(len(result), len(expected))
        eq_(result, expected)

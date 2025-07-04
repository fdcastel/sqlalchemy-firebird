from sqlalchemy import Column, select
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures

import sqlalchemy_firebird.types as fb_types

TEST_UNICODE = "próf-áêïôù-🗄️.fdb"
TEST_BINARY = TEST_UNICODE.encode('utf-8')

class IssuesTest(fixtures.TestBase):
    @testing.provide_metadata
    @testing.combinations(
        (fb_types.FBTEXT, 'hi'),
        (fb_types.FBTEXT, TEST_UNICODE),
        (fb_types.FBBLOB, TEST_BINARY), argnames="type_, expected"
    )
    def test_issue_76(self, connection, type_, expected):
        metadata = self.metadata

        the_blob = Table(
            "the_blob",
            metadata,
            Column("the_value", type_)
        )
        metadata.create_all(testing.db)

        connection.execute(
            the_blob.insert()
                .values(dict(
                    the_value=expected,
                ))
            )

        eq_(
            connection.execute(
                select(the_blob.c.the_value)
            ).scalar(),
            expected,
        )

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable, DropTable, CreateIndex, DropIndex
from sqlalchemy.testing.provision import temp_table_keyword_args


@temp_table_keyword_args.for_db("firebird")
def _firebird_temp_table_keyword_args(cfg, eng):
    return {
        "prefixes": ["GLOBAL TEMPORARY"],
        "firebird.firebird_on_commit": "PRESERVE ROWS",
    }


@event.listens_for(Engine, "after_execute")
def receive_after_execute(connection, statement, *arg):
    #
    # TEST-INFRASTRUCTURE ONLY. This module (sqlalchemy_firebird.provision) is
    # imported solely by SQLAlchemy's test harness, never by production code.
    #
    # Firebird cannot reliably use database objects created by DDL that has not
    # yet been committed in the same transaction, and the compliance suite
    # frequently builds a schema and then reflects/queries it without an
    # explicit commit in between. Auto-commit DDL so the new objects are
    # visible to the statements that follow. (Production code does not need
    # this: metadata.create_all() and normal transaction handling commit DDL.)
    #
    # The listener is attached to the base Engine class, so it must be scoped
    # to Firebird connections — otherwise it would fire for every engine in the
    # test process, including non-Firebird ones.
    #
    # Note: statements executed with connection.exec_driver_sql() don't pass
    # through here. Use connection.execute(text()) instead.
    #
    if connection.dialect.name != "firebird":
        return
    if isinstance(statement, (CreateTable, DropTable, CreateIndex, DropIndex)):
        # Using Connection protected methods here because the public ones cause errors with TransactionManager
        connection._commit_impl()
        connection._begin_impl(connection._transaction)

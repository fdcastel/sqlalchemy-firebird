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
    # Important: Statements executed with connection.exec_driver_sql() don't pass through here.
    #            Use connection.execute(text()) instead.
    #
    if isinstance(statement, (CreateTable, DropTable, CreateIndex, DropIndex)):
        # Using Connection protected methods here because the public ones cause errors with TransactionManager
        connection._commit_impl()
        connection._begin_impl(connection._transaction)

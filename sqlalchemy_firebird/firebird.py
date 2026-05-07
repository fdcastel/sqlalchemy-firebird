"""
.. dialect:: firebird+firebird
    :name: firebird
    :dbapi: firebird-driver
    :connectstring: firebird+firebird://user:password@host:port/path/to/db[?key=value&key=value...]
    :url: https://pypi.org/project/firebird-driver/
    :documentation: https://firebird-driver.readthedocs.io/en/latest/

    The firebird-driver package provides driver for Python 3.8+ and Firebird 3+.
    This driver uses new Firebird OO API provided by fbclient library.
"""  # noqa

import sys

from datetime import datetime
from datetime import time
from math import modf
from sqlalchemy import util
from .base import FBDialect

import firebird.driver
from firebird.driver import driver_config
from firebird.driver import get_timezone


class FBDialect_firebird(FBDialect):
    name = "firebird.firebird"
    driver = "firebird-driver"
    supports_statement_cache = True

    @classmethod
    def dbapi(cls):
        # For SQLAlchemy 1.4 compatibility only. Deprecated in 2.0.
        return firebird.driver

    @classmethod
    def import_dbapi(cls):
        return firebird.driver

    @util.memoized_property
    def _isolation_lookup(self):
        return {
            "AUTOCOMMIT": "autocommit",
            "READ COMMITTED": "read_committed",
            "REPEATABLE READ": "repeatable_read",
            "SERIALIZABLE": "serializable",
        }

    def get_isolation_level_values(self, dbapi_connection):
        return list(self._isolation_lookup)

    def set_isolation_level(self, dbapi_connection, level):
        dbapi_connection.set_isolation_level(self._isolation_lookup[level])

    def set_readonly(self, connection, value):
        connection.readonly = value

    def get_readonly(self, connection):
        return connection.readonly

    def set_deferrable(self, connection, value):
        connection.deferrable = value

    def get_deferrable(self, connection):
        return connection.deferrable

    def do_terminate(self, dbapi_connection) -> None:
        dbapi_connection.terminate()

    def create_connect_args(self, url):
        opts = url.translate_connect_args(username="user")

        qry = url.query
        if qry.get("fb_client_library"):
            # Set driver_config.fb_client_library and remove it from remaining keys passed to .connect()
            driver_config.fb_client_library.value = qry["fb_client_library"]
            qry = remove_keys(qry, {"fb_client_library"})

        if opts.get("host"):
            host_name = opts["host"]
            database_name = opts["database"]

            port_number = "3050"
            if opts.get("port") is not None:
                port_number = str(opts["port"])
                del opts["port"]

            # Key the driver_config server registration by "host:port" so
            # multiple Firebird servers on the same host (different ports)
            # get distinct entries (issue #69). IPv6 literals are wrapped
            # in [] to disambiguate their embedded colons.
            if ":" in host_name:
                server_name = f"[{host_name}]:{port_number}"
            else:
                server_name = f"{host_name}:{port_number}"

            cfg_driver_server = driver_config.get_server(server_name)
            if cfg_driver_server is None:
                cfg_driver_server = driver_config.register_server(server_name)
            cfg_driver_server.host.value = host_name
            cfg_driver_server.port.value = port_number

            cfg_driver_database = driver_config.get_database(database_name)
            if cfg_driver_database is None:
                cfg_driver_database = driver_config.register_database(
                    database_name
                )
            cfg_driver_database.server.value = server_name
            cfg_driver_database.database.value = opts["database"]

            del opts["host"]

        opts.update(qry)
        return ([], opts)

    def do_rollback(self, dbapi_connection):
        if dbapi_connection.is_active():
            dbapi_connection.rollback()

    def do_commit(self, dbapi_connection):
        if dbapi_connection.is_active():
            dbapi_connection.commit()

    def _get_server_version_info(self, connection):
        dbapi_connection = (
            connection.connection.dbapi_connection
            if self.using_sqlalchemy2
            else connection.connection
        )
        minor, major = modf(dbapi_connection.info.engine_version)
        return (int(major), int(minor * 10))

    def adapt_timezone(self, param):
        # Convert tzinfo for firebird-driver. Requires tzinfo.tzname() method implemented.
        if isinstance(param, datetime) and param.tzinfo:
            return param.replace(tzinfo=get_timezone(param.tzname()))
        elif isinstance(param, time) and param.tzinfo:
            return param.replace(tzinfo=get_timezone(param.tzname()))
        return param

    def do_execute(self, cursor, statement, parameters, context=None):
        # Firebird-driver needs special time zone handling.
        #   https://github.com/FirebirdSQL/python3-driver/issues/19#issuecomment-1523045743
        adapted_parameters = [self.adapt_timezone(p) for p in parameters]
        self._disable_blob_streaming(cursor)
        super().do_execute(cursor, statement, adapted_parameters, context)

    def do_executemany(self, cursor, statement, parameters, context=None):
        self._disable_blob_streaming(cursor)
        super().do_executemany(cursor, statement, parameters, context)

    def do_execute_no_params(self, cursor, statement, context=None):
        self._disable_blob_streaming(cursor)
        super().do_execute_no_params(cursor, statement, context)

    @staticmethod
    def _disable_blob_streaming(cursor):
        # SQLAlchemy fully consumes a cursor and then closes it before its
        # result processors run. firebird-driver closes any BlobReader
        # objects together with the cursor, which would make BLOB columns
        # unreadable from within SQLAlchemy result rows (issue #58). Force
        # all BLOBs to be returned as fully materialized bytes/str by
        # raising the per-cursor streaming threshold past any practical
        # size; this leaves driver_config.stream_blob_threshold untouched.
        if hasattr(cursor, "stream_blob_threshold"):
            cursor.stream_blob_threshold = sys.maxsize


def remove_keys(d, keys):
    return {x: d[x] for x in d if x not in keys}


dialect = FBDialect_firebird

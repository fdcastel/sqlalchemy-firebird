# Allow circular references between FBDialect and FBInspector
from __future__ import annotations

from typing import Any, List, TypedDict
from typing import Optional

from sqlalchemy import bindparam
from sqlalchemy import exc
from sqlalchemy import schema as sa_schema
from sqlalchemy import sql
from sqlalchemy import text
from sqlalchemy import types as sa_types
from sqlalchemy import util
from sqlalchemy.engine import default
from sqlalchemy.engine import reflection
from sqlalchemy.engine.interfaces import BindTyping
from sqlalchemy.sql import coercions
from sqlalchemy.sql import compiler
from sqlalchemy.sql import expression
from sqlalchemy.sql import roles

import sqlalchemy_firebird.types as fb_types


# Expression separator for COMPUTER BY expressions
EXPRESSION_SEPARATOR = "||"


def coalesce(*arg):
    # https://stackoverflow.com/questions/4978738/is-there-a-python-equivalent-of-the-c-sharp-null-coalescing-operator#comment37717570_16247152
    return next((a for a in arg if a is not None), None)


class FBCompiler(sql.compiler.SQLCompiler):
    def render_bind_cast(self, type_, dbapi_type, sqltext):
        return f"""CAST({sqltext} AS {
            self.dialect.type_compiler_instance.process(
                dbapi_type, identifier_preparer=self.preparer
            )
        })"""

    def visit_empty_set_expr(self, element_types, **kw):
        return "SELECT 1 FROM rdb$database WHERE 1 != 1"

    def visit_sequence(self, sequence, **kw):
        return "GEN_ID(%s, 1)" % self.preparer.format_sequence(sequence)

    def limit_clause(self, select, **kw):
        return self._handle_limit_fetch_clause(
            None, select._offset_clause, select._limit_clause, **kw
        )

    def for_update_clause(self, select, **kw):
        tmp = " FOR UPDATE"
        if select._for_update_arg.nowait:
            tmp += " WITH LOCK"
        if select._for_update_arg.skip_locked:
            tmp += " WITH LOCK SKIP LOCKED"

        return tmp

    def fetch_clause(
        self,
        select,
        fetch_clause=None,
        require_offset=False,
        use_literal_execute_for_simple_int=False,
        **kw,
    ):
        if fetch_clause is None:
            fetch_clause = select._fetch_clause

        return self._handle_limit_fetch_clause(
            fetch_clause, select._offset_clause, None, **kw
        )

    def _handle_limit_fetch_clause(
        self, fetch_clause, offset_clause, limit_clause, **kw
    ):
        # Albeit non-standard, ROWS is a better choice than OFFSET / FETCH in Firebird since
        #   it is supported since Firebird 2.5 and it works with expressions.
        # https://firebirdsql.org/file/documentation/html/en/refdocs/fblangref40/firebird-40-language-reference.html#fblangref40-dml-select-rows
        text = ""

        if (fetch_clause is not None) and (offset_clause is not None):
            # OFFSET 2 ROWS FETCH NEXT 5 ROWS ONLY  =>  ROWS 2 + 1 TO 2 + 5
            text += (
                " \n ROWS "
                + self.process(offset_clause, **kw)
                + " + 1 TO "
                + self.process(offset_clause, **kw)
                + " + "
                + self.process(fetch_clause, **kw)
            )
        elif (limit_clause is not None) and (offset_clause is not None):
            # LIMIT 5 OFFSET 2  =>  ROWS 2 + 1 TO 2 + 5
            text += (
                " \n ROWS "
                + self.process(offset_clause, **kw)
                + " + 1 TO "
                + self.process(offset_clause, **kw)
                + " + "
                + self.process(limit_clause, **kw)
            )
        elif fetch_clause is not None:
            # FETCH NEXT 5 ROWS ONLY  =>  ROWS 1 TO 5
            text += " \n ROWS 1 TO " + self.process(fetch_clause, **kw)
        elif limit_clause is not None:
            # LIMIT 5  =>  ROWS 1 TO 5
            text += " \n ROWS 1 TO " + self.process(limit_clause, **kw)
        elif offset_clause is not None:
            # OFFSET 2 ROWS  =>  ROWS 2 + 1 TO 9223372036854775807
            text += (
                " \n ROWS "
                + self.process(offset_clause, **kw)
                + " + 1 TO 9223372036854775807"
            )

        return text

    def visit_substring_func(self, func, **kw):
        s = self.process(func.clauses.clauses[0])
        start = self.process(func.clauses.clauses[1])
        if len(func.clauses.clauses) > 2:
            length = self.process(func.clauses.clauses[2])
            return f"SUBSTRING({s} FROM {start} FOR {length})"

        return f"SUBSTRING({s} FROM {start})"

    def visit_truediv_binary(self, binary, operator, **kw):
        return (
            self.process(binary.left, **kw)
            + " / "
            + "(%s + 0.0)" % self.process(binary.right, **kw)
        )

    def visit_mod_binary(self, binary, operator, **kw):
        return "MOD(%s, %s)" % (
            self.process(binary.left, **kw),
            self.process(binary.right, **kw),
        )

    def visit_bitwise_xor_op_binary(self, binary, operator, **kw):
        return "BIN_XOR(%s, %s)" % (
            self.process(binary.left, **kw),
            self.process(binary.right, **kw),
        )

    def visit_now_func(self, fn, **kw):
        return "CURRENT_TIMESTAMP"

    def function_argspec(self, fn, **kw):
        if fn.clauses is not None and len(fn.clauses) > 0:
            return self.process(fn.clause_expr, **kw)

        return ""

    def visit_char_length_func(self, fn, **kw):
        return "CHAR_LENGTH" + self.function_argspec(fn, **kw)

    def visit_length_func(self, fn, **kw):
        return "CHAR_LENGTH" + self.function_argspec(fn, **kw)

    def default_from(self):
        return " FROM rdb$database"

    def returning_clause(self, stmt, returning_cols, **kw):
        return super().returning_clause(stmt, returning_cols, **kw)


class FBDDLCompiler(sql.compiler.DDLCompiler):
    def get_column_specification(self, column, **kwargs):
        colspec = self.preparer.format_column(column)

        impl_type = column.type.dialect_impl(self.dialect)
        if isinstance(impl_type, sa_types.TypeDecorator):
            impl_type = impl_type.impl

        has_identity = column.identity is not None

        compiled_type = self.dialect.type_compiler_instance.process(
            column.type,
            type_expression=column,
            identifier_preparer=self.preparer,
        )

        if (
            column.primary_key
            and column is column.table._autoincrement_column
            and not has_identity
            and (
                column.default is None
                or (
                    isinstance(column.default, sa_schema.Sequence)
                    and column.default.optional
                )
            )
            and self.dialect.supports_identity_columns
        ):
            colspec += " %s GENERATED BY DEFAULT AS IDENTITY" % compiled_type
        else:
            colspec += " " + compiled_type
            default_ = self.get_column_default_string(column)
            if default_ is not None:
                colspec += " DEFAULT " + default_

            if column.computed is not None:
                colspec += " " + self.process(column.computed)
            if has_identity:
                colspec += " " + self.process(column.identity)

            if not column.nullable and not has_identity:
                colspec += " NOT NULL"
            elif column.nullable and has_identity:
                colspec += " NULL"

        return colspec

    def visit_create_index(
        self, create, include_schema=False, include_table_schema=True, **kw
    ):
        preparer = self.preparer
        index = create.element
        self._verify_index_table(index)

        if index.name is None:
            raise exc.CompileError(
                "CREATE INDEX requires that the index have a name."
            )

        txt = "CREATE "
        if index.unique:
            txt += "UNIQUE "

        descending = index.dialect_options["firebird"]["descending"]
        if descending is True:
            txt += "DESCENDING "

        txt += "INDEX %s ON %s " % (
            self._prepared_index_name(index, include_schema=include_schema),
            preparer.format_table(
                index.table, use_schema=include_table_schema
            ),
        )

        if index.expressions is None:
            raise exc.CompileError(
                "CREATE INDEX requires at least one column or expression."
            )

        first_expression = (
            index.expressions[0]
            if len(index.expressions) > 0
            else index.expressions
        )

        if isinstance(first_expression, expression.ColumnClause):
            # INDEX on columns
            txt += "(%s)" % (
                ", ".join(
                    self.sql_compiler.process(
                        expr, include_table=False, literal_binds=True
                    )
                    for expr in index.expressions
                )
            )
        else:
            # INDEX on expression
            txt += "COMPUTED BY (%s)" % EXPRESSION_SEPARATOR.join(
                self.sql_compiler.process(
                    expr, include_table=False, literal_binds=True
                )
                for expr in index.expressions
            )

        # Partial indices (Firebird 5.0+)
        whereclause = index.dialect_options["firebird"]["where"]
        if whereclause is not None:
            whereclause = coercions.expect(
                roles.DDLExpressionRole, whereclause
            )

            where_compiled = self.sql_compiler.process(
                whereclause, include_table=False, literal_binds=True
            )
            txt += " WHERE " + where_compiled

        return txt

    def post_create_table(self, table):
        table_opts = []
        fb_opts = table.dialect_options["firebird"]

        if fb_opts["on_commit"]:
            on_commit_options = fb_opts["on_commit"]
            table_opts.append("\n ON COMMIT %s" % on_commit_options)

        return "".join(table_opts)

    def visit_computed_column(self, generated, **kw):
        if generated.persisted is not None:
            raise exc.CompileError(
                "Firebird computed columns do not support a persistence "
                "method setting; set the 'persisted' flag to None for "
                "Firebird support."
            )

        return "GENERATED ALWAYS AS (%s)" % self.sql_compiler.process(
            generated.sqltext, include_table=False, literal_binds=True
        )

    def get_identity_options(self, identity_options):
        firebird_3 = (
            self.dialect.server_version_info
            and self.dialect.server_version_info < (4,)
        )

        txt = []
        if identity_options.start is not None:
            start = identity_options.start

            # Firebird 3 has distinct START WITH semantic.
            # https://firebirdsql.org/file/documentation/release_notes/html/en/4_0/rlsnotes40.html#rnfb40-compat-sql-sequence-start-value
            # Previous versions of dialect tried to hide this (adjusting here the reflected start value).
            # This was removed since it opens a can of worms (e.g. when reading databases NOT created by SQLAlchemy).

            txt.append("START WITH %d" % start)

        if not firebird_3:
            if identity_options.increment is not None:
                txt.append("INCREMENT BY %d" % identity_options.increment)

        return " ".join(txt)

    def visit_identity_column(self, identity, **kw):
        firebird_3 = (
            self.dialect.server_version_info
            and self.dialect.server_version_info < (4,)
        )

        kind = (
            "ALWAYS" if identity.always and (not firebird_3) else "BY DEFAULT"
        )
        text = "GENERATED %s AS IDENTITY" % kind

        options = self.get_identity_options(identity)
        if options:
            text += " (%s)" % options

        return text


class FBTypeCompiler(compiler.GenericTypeCompiler):
    def visit_boolean(self, type_, **kw):
        return self.visit_BOOLEAN(type_, **kw)

    def visit_datetime(self, type_, **kw):
        return self.visit_TIMESTAMP(type_, **kw)

    def _render_firebird_string_type(
        self,
        name: str,
        length: Optional[int] = None,
        collation: Optional[str] = None,
        charset: Optional[str] = None,
    ) -> str:
        firebird_3 = (
            self.dialect.server_version_info
            and self.dialect.server_version_info < (4,)
        )

        if name in ["BINARY", "VARBINARY", "NCHAR", "NVARCHAR"]:
            charset = None
            collation = None

        if name == "NVARCHAR":
            name = "NATIONAL CHARACTER VARYING"

        if firebird_3:
            if name == "BINARY":
                name = "CHAR"
                charset = fb_types.BINARY_CHARSET
                collation = None
            elif name == "VARBINARY":
                name = "VARCHAR"
                charset = fb_types.BINARY_CHARSET
                collation = None

        text = name
        if length is None:
            if name == "VARBINARY" or (
                name == "VARCHAR" and charset == fb_types.BINARY_CHARSET
            ):
                text = "BLOB SUB_TYPE BINARY"
                charset = fb_types.BINARY_CHARSET
                collation = None
            elif name == "VARCHAR":
                text = "BLOB SUB_TYPE TEXT"
            elif name == "NATIONAL CHARACTER VARYING":
                text = "BLOB SUB_TYPE TEXT"
                charset = fb_types.NATIONAL_CHARSET
                collation = None

        text = text + (length and "(%d)" % length or "")

        if charset is not None:
            text += f" CHARACTER SET {charset}"

        if collation is not None:
            text += f" COLLATE {collation}"

        return text

    def visit_CHAR(self, type_: fb_types.FBCHAR, **kw: Any) -> str:
        return self._render_firebird_string_type(
            "CHAR",
            type_.length,
            type_.collation,
            getattr(type_, "charset", None),
        )

    def visit_NCHAR(self, type_: fb_types.FBNCHAR, **kw: Any) -> str:
        return self._render_firebird_string_type(
            "NCHAR", type_.length, type_.collation
        )

    def visit_VARCHAR(self, type_: fb_types.FBVARCHAR, **kw: Any) -> str:
        return self._render_firebird_string_type(
            "VARCHAR",
            type_.length,
            type_.collation,
            getattr(type_, "charset", None),
        )

    def visit_NVARCHAR(self, type_: fb_types.FBNCHAR, **kw: Any) -> str:
        return self._render_firebird_string_type(
            "NVARCHAR", type_.length, type_.collation
        )

    def visit_BINARY(self, type_: fb_types.FBBINARY, **kw) -> str:
        return self._render_firebird_string_type("BINARY", type_.length)

    def visit_VARBINARY(self, type_: fb_types.FBVARBINARY, **kw) -> str:
        return self._render_firebird_string_type("VARBINARY", type_.length)

    def visit_TEXT(self, type_, **kw):
        return self.visit_BLOB(type_, override_subtype=1, **kw)

    def visit_BLOB(self, type_, override_subtype=None, **kw):
        text = "BLOB"

        subtype = coalesce(override_subtype, getattr(type_, "subtype", None))
        if subtype is not None:
            text += " SUB_TYPE TEXT" if subtype == 1 else " SUB_TYPE BINARY"

        segment_size = getattr(type_, "segment_size", None)
        if segment_size is not None:
            text += f" SEGMENT SIZE {segment_size}"

        charset = getattr(type_, "charset", None)
        if charset is not None:
            text += f" CHARACTER SET {charset}"

        collation = getattr(type_, "collation", None)
        if collation is not None:
            text += f" COLLATE {collation}"

        return text

    def visit_INT128(self, type_, **kw):
        return "INT128"

    def visit_FLOAT(self, type_, **kw):
        return "FLOAT" + (type_.precision and "(%d)" % type_.precision or "")

    def visit_DECFLOAT(self, type_, **kw):
        return "DECFLOAT" + (
            type_.precision and "(%d)" % type_.precision or ""
        )

    def visit_NUMERIC(self, type_, **kw):
        return "NUMERIC(%(precision)s, %(scale)s)" % {
            "precision": coalesce(type_.precision, 18),
            "scale": coalesce(type_.scale, 4),
        }

    def visit_DECIMAL(self, type_, **kw):
        return "DECIMAL(%(precision)s, %(scale)s)" % {
            "precision": coalesce(type_.precision, 18),
            "scale": coalesce(type_.scale, 4),
        }

    def visit_TIMESTAMP(self, type_, **kw):
        if self.dialect.server_version_info < (4,):
            return super().visit_TIMESTAMP(type_, **kw)

        return "TIMESTAMP%s %s" % (
            (
                "(%d)" % type_.precision
                if getattr(type_, "precision", None) is not None
                else ""
            ),
            (type_.timezone and "WITH" or "WITHOUT") + " TIME ZONE",
        )

    def visit_TIME(self, type_, **kw):
        if self.dialect.server_version_info < (4,):
            return super().visit_TIME(type_, **kw)

        return "TIME%s %s" % (
            (
                "(%d)" % type_.precision
                if getattr(type_, "precision", None) is not None
                else ""
            ),
            (type_.timezone and "WITH" or "WITHOUT") + " TIME ZONE",
        )


class FBIdentifierPreparer(sql.compiler.IdentifierPreparer):
    illegal_initial_characters = compiler.ILLEGAL_INITIAL_CHARACTERS.union(
        ["_"]
    )

    def __init__(self, dialect):
        super().__init__(dialect, omit_schema=True)


class FBExecutionContext(default.DefaultExecutionContext):
    def fire_sequence(self, seq, type_):
        return self._execute_scalar(
            (
                "SELECT GEN_ID(%s, 1) FROM rdb$database"
                % self.dialect.identifier_preparer.format_sequence(seq)
            ),
            type_,
        )


class ReflectedDomain(TypedDict):
    """Represents a reflected domain."""

    name: str
    """The string name of the underlying data type of the domain."""
    nullable: bool
    """Indicates if the domain allows null or not."""
    default: Optional[str]
    """The string representation of the default value of this domain
    or ``None`` if none present.
    """
    check: Optional[str]
    """The constraint defined in the domain, if any.
    """
    comment: Optional[str]
    """The comment of the domain, if any.
    """


class FBInspector(reflection.Inspector):
    dialect: FBDialect

    def get_domains(
        self, schema: Optional[str] = None
    ) -> List[ReflectedDomain]:
        with self._operation_context() as conn:
            return self.dialect._load_domains(
                conn, schema, info_cache=self.info_cache
            )


class FBDialect(default.DefaultDialect):
    # By SQLAlchemy convention ``name`` is the backend name (Alembic and
    # other tools branch on ``dialect.name == "firebird"``); the URL driver
    # suffix is carried by ``driver`` on the concrete dialect.
    name = "firebird"

    bind_typing = BindTyping.RENDER_CASTS

    supports_alter = True
    supports_sane_rowcount = True
    supports_sane_multi_rowcount = False

    supports_native_boolean = True
    supports_native_decimal = True

    supports_schemas = False
    supports_sequences = True
    sequences_optional = False
    postfetch_lastrowid = False
    use_insertmanyvalues = False

    supports_comments = True
    supports_default_values = True
    supports_default_metavalue = True
    supports_empty_insert = False
    supports_identity_columns = True

    statement_compiler = FBCompiler
    ddl_compiler = FBDDLCompiler
    type_compiler_cls = FBTypeCompiler
    preparer = FBIdentifierPreparer
    execution_ctx_cls = FBExecutionContext
    inspector = FBInspector

    update_returning = True
    delete_returning = True
    insert_returning = True

    supports_unicode_binds = True
    supports_empty_insert = False
    supports_is_distinct_from = True

    requires_name_normalize = True

    colspecs = {
        sa_types.String: fb_types._FBString,
        sa_types.Numeric: fb_types.FBNUMERIC,
        sa_types.Float: fb_types.FBFLOAT,
        sa_types.Double: fb_types.FBDOUBLE_PRECISION,
        sa_types.Date: fb_types.FBDATE,
        sa_types.Time: fb_types.FBTIME,
        sa_types.DateTime: fb_types.FBTIMESTAMP,
        sa_types.Interval: fb_types._FBInterval,
        sa_types.BigInteger: fb_types.FBBIGINT,
        sa_types.Integer: fb_types.FBINTEGER,
        sa_types.SmallInteger: fb_types.FBSMALLINT,
        sa_types.BINARY: fb_types.FBBINARY,
        sa_types.VARBINARY: fb_types.FBVARBINARY,
        sa_types.LargeBinary: fb_types.FBBLOB,
    }

    # SELECT TRIM(rdb$type_name) FROM rdb$types WHERE rdb$field_name = 'RDB$FIELD_TYPE' ORDER BY 1
    ischema_names = {
        "BLOB": fb_types.FBBLOB,
        # "BLOB_ID": unused
        "BOOLEAN": fb_types.FBBOOLEAN,
        "CSTRING": fb_types.FBVARCHAR,
        "DATE": fb_types.FBDATE,
        "DECFLOAT(16)": fb_types.FBDECFLOAT,
        "DECFLOAT(34)": fb_types.FBDECFLOAT,
        "DOUBLE": fb_types.FBDOUBLE_PRECISION,
        "FLOAT": fb_types.FBFLOAT,
        "INT128": fb_types.FBINT128,
        "INT64": fb_types.FBBIGINT,
        "LONG": fb_types.FBINTEGER,
        # "QUAD": unused,
        "SHORT": fb_types.FBSMALLINT,
        "TEXT": fb_types.FBCHAR,
        "TIME": fb_types.FBTIME,
        "TIME WITH TIME ZONE": fb_types.FBTIME,
        "TIMESTAMP": fb_types.FBTIMESTAMP,
        "TIMESTAMP WITH TIME ZONE": fb_types.FBTIMESTAMP,
        "VARYING": fb_types.FBVARCHAR,
    }

    construct_arguments = [
        (
            sa_schema.Table,
            {
                "on_commit": None,
            },
        ),
        (
            sa_schema.Index,
            {
                "descending": None,
                "where": None,
            },
        ),
    ]

    def initialize(self, connection):
        super().initialize(connection)

        if self.server_version_info < (4,):
            # Firebird 3.0
            from .fb_info30 import MAX_IDENTIFIER_LENGTH, RESERVED_WORDS
        else:
            # Firebird 4.0 or higher
            from .fb_info40 import MAX_IDENTIFIER_LENGTH, RESERVED_WORDS

        self.max_identifier_length = MAX_IDENTIFIER_LENGTH
        self.preparer.reserved_words = RESERVED_WORDS

    @reflection.cache
    def has_table(self, connection, table_name, schema=None, **kw):
        has_table_query = """
            SELECT 1 AS has_table
            FROM rdb$relations
            WHERE rdb$relation_name = ?
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(has_table_query, (tablename,))
        return c.first() is not None

    @reflection.cache
    def has_sequence(self, connection, sequence_name, schema=None, **kw):
        has_sequence_query = """
            SELECT 1 AS has_sequence 
            FROM rdb$generators
            WHERE rdb$generator_name = ?
        """
        sequencename = self.denormalize_name(sequence_name)
        c = connection.exec_driver_sql(has_sequence_query, (sequencename,))
        return c.first() is not None

    @reflection.cache
    def get_table_names(self, connection, schema=None, **kw):
        tables_query = """
            SELECT TRIM(rdb$relation_name) AS relation_name
            FROM rdb$relations
            WHERE rdb$relation_type IN (0 /* TABLE */)
              AND COALESCE(rdb$system_flag, 0) = 0
            ORDER BY 1
        """

        return [
            self.normalize_name(row.relation_name)
            for row in connection.exec_driver_sql(tables_query)
        ]

    @reflection.cache
    def get_temp_table_names(self, connection, schema=None, **kw):
        temp_tables_query = """
            SELECT TRIM(rdb$relation_name) AS relation_name
            FROM rdb$relations
            WHERE rdb$relation_type IN (4 /* TEMPORARY_TABLE_PRESERVE */, 
                                        5 /* TEMPORARY_TABLE_DELETE */)
              AND COALESCE(rdb$system_flag, 0) = 0
            ORDER BY 1
        """
        return [
            self.normalize_name(row.relation_name)
            for row in connection.exec_driver_sql(temp_tables_query)
        ]

    @reflection.cache
    def get_view_names(self, connection, schema=None, **kw):
        views_query = """
            SELECT TRIM(rdb$relation_name) AS relation_name
            FROM rdb$relations
            WHERE rdb$relation_type IN (1 /* VIEW */)
              AND COALESCE(rdb$system_flag, 0) = 0
            ORDER BY 1
        """
        return [
            self.normalize_name(row.relation_name)
            for row in connection.exec_driver_sql(views_query)
        ]

    @reflection.cache
    def get_sequence_names(self, connection, schema=None, **kw):
        sequences_query = """
            SELECT TRIM(rdb$generator_name) AS generator_name
            FROM rdb$generators
            WHERE COALESCE(rdb$system_flag, 0) = 0
        """
        # Do not need ORDER BY
        return [
            self.normalize_name(row.generator_name)
            for row in connection.exec_driver_sql(sequences_query)
        ]

    @reflection.cache
    def get_view_definition(self, connection, view_name, schema=None, **kw):
        view_query = """
            SELECT rdb$view_source AS view_source
            FROM rdb$relations
            WHERE rdb$relation_type IN (1 /* VIEW */)
              AND rdb$relation_name = ?
        """
        viewname = self.denormalize_name(view_name)
        c = connection.exec_driver_sql(view_query, (viewname,))
        row = c.fetchone()
        if row:
            return row.view_source

        raise exc.NoSuchTableError(view_name)

    # ------------------------------------------------------------------ #
    # Reflection
    #
    # The single-table ``get_*`` methods below are thin wrappers around the
    # SQLAlchemy 2.0 batched ``get_multi_*`` family. Driving every reflection
    # query from ``rdb$relations`` (rather than one query per table) lets
    # ``MetaData.reflect()`` and Alembic autogenerate reflect a whole schema
    # in a handful of queries, mirroring the in-tree PostgreSQL dialect.
    # ------------------------------------------------------------------ #

    def _value_or_raise(self, data, table_name, schema):
        try:
            return dict(data)[(schema, table_name)]
        except KeyError:
            raise exc.NoSuchTableError(
                f"{schema}.{table_name}" if schema else table_name
            ) from None

    def _relation_type_condition(self, kind, scope):
        # Map SQLAlchemy's ObjectKind / ObjectScope onto Firebird's
        # rdb$relation_type values. ``ANY``/``ANY`` (used by the single-table
        # wrappers) imposes no type restriction, preserving the historical
        # behaviour of reflecting any relation referenced by name.
        if (
            kind is reflection.ObjectKind.ANY
            and scope is reflection.ObjectScope.ANY
        ):
            return None

        types = []
        if reflection.ObjectKind.TABLE in kind:
            if reflection.ObjectScope.DEFAULT in scope:
                types.append("0")  # persistent table
            if reflection.ObjectScope.TEMPORARY in scope:
                types.extend(["4", "5"])  # global temporary tables
        if (
            reflection.ObjectKind.VIEW in kind
            and reflection.ObjectScope.DEFAULT in scope
        ):
            types.append("1")  # view
        # Firebird has no materialized views.

        if not types:
            return "1 = 0"
        return "r.rdb$relation_type IN (%s)" % ", ".join(types)

    def _relation_filter(self, kind, scope, filter_names):
        # Build the WHERE fragment (on the rdb$relations alias ``r``) shared by
        # every batched reflection query, along with its bind parameters and a
        # map from the on-disk (denormalized) relation name back to the name
        # the caller asked for.
        conditions = ["COALESCE(r.rdb$system_flag, 0) = 0"]
        type_condition = self._relation_type_condition(kind, scope)
        if type_condition is not None:
            conditions.append(type_condition)

        params = {}
        name_map = None
        has_filter_names = bool(filter_names)
        if has_filter_names:
            name_map = {self.denormalize_name(n): n for n in filter_names}
            conditions.append("r.rdb$relation_name IN :relation_names")
            params["relation_names"] = list(name_map)

        return " AND ".join(conditions), params, has_filter_names, name_map

    def _relation_key(self, schema, relation_name, name_map):
        # Mirror SQLAlchemy's default multi-reflection: when explicit names
        # were requested, key results by the *input* name (so the caller can
        # look them up unchanged); otherwise key by the normalized relation
        # name, matching what get_table_names() / get_view_names() return.
        if name_map is not None:
            mapped = name_map.get(relation_name)
            if mapped is not None:
                return (schema, mapped)
        return (schema, self.normalize_name(relation_name))

    def _exec_reflection_query(
        self, connection, query, params, has_filter_names
    ):
        # Reflection queries go through connection.execute(text()) rather than
        # exec_driver_sql so the test-suite DDL-autocommit listener in
        # provision.py can make freshly created objects visible. See that
        # module for the rationale.
        stmt = text(query)
        if has_filter_names:
            stmt = stmt.bindparams(bindparam("relation_names", expanding=True))
        return connection.execute(stmt, params)

    @reflection.cache
    def get_columns(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_columns(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_columns(  # noqa: C901
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        columns_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rf.rdb$field_name) AS field_name,
                   COALESCE(rf.rdb$null_flag, f.rdb$null_flag) AS null_flag,
                   TRIM(t.rdb$type_name) AS field_type,
                   f.rdb$field_length / COALESCE(cs.rdb$bytes_per_character, 1) AS field_length,
                   f.rdb$field_precision AS field_precision,
                   f.rdb$field_scale * -1 AS field_scale,
                   f.rdb$field_sub_type AS field_sub_type,
                   f.rdb$segment_length AS segment_length,
                   TRIM(cs.rdb$character_set_name) as character_set_name,
                   TRIM(cl.rdb$collation_name) as collation_name,
                   COALESCE(rf.rdb$default_source, f.rdb$default_source) AS default_source,
                   TRIM(rf.rdb$description) AS description,
                   f.rdb$computed_source AS computed_source,
                   rf.rdb$identity_type AS identity_type,
                   g.rdb$initial_value AS initial_value,
                   g.rdb$generator_increment AS generator_increment
            FROM rdb$relations r
                 JOIN rdb$relation_fields rf
                   ON rf.rdb$relation_name = r.rdb$relation_name
                 JOIN rdb$fields f
                   ON f.rdb$field_name = rf.rdb$field_source
                 JOIN rdb$types t
                   ON t.rdb$type = f.rdb$field_type
                  AND t.rdb$field_name = 'RDB$FIELD_TYPE'
                 LEFT JOIN rdb$character_sets cs
                        ON cs.rdb$character_set_id = f.rdb$character_set_id
                 LEFT JOIN rdb$collations cl
                        ON cl.rdb$collation_id = rf.rdb$collation_id
                       AND cl.rdb$character_set_id = cs.rdb$character_set_id
                 LEFT JOIN rdb$generators g
                        ON g.rdb$generator_name = rf.rdb$generator_name
            WHERE COALESCE(f.rdb$system_flag, 0) = 0
              AND {relation_filter}
            ORDER BY r.rdb$relation_name, rf.rdb$field_position
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, columns_query, params, has_filter_names
        )

        columns = util.defaultdict(list)
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            cols = columns[key]
            orig_colname = row.field_name
            colname = self.normalize_name(orig_colname)

            # Extract data type
            colclass = self.ischema_names.get(row.field_type)
            if colclass is None:
                util.warn(
                    "Unknown type '%s' in column '%s'. Check FBDialect.ischema_names."
                    % (row.field_type, colname)
                )
                coltype = sa_types.NULLTYPE
            elif issubclass(colclass, fb_types._FBString):
                if row.character_set_name == fb_types.BINARY_CHARSET:
                    if colclass == fb_types.FBCHAR:
                        colclass = fb_types.FBBINARY
                    elif colclass == fb_types.FBVARCHAR:
                        colclass = fb_types.FBVARBINARY
                if row.character_set_name == fb_types.NATIONAL_CHARSET:
                    if colclass == fb_types.FBCHAR:
                        colclass = fb_types.FBNCHAR
                    elif colclass == fb_types.FBVARCHAR:
                        colclass = fb_types.FBNVARCHAR

                coltype = colclass(
                    length=row.field_length,
                    charset=row.character_set_name,
                    collation=row.collation_name,
                )
            elif colclass in (
                fb_types.FBFLOAT,
                fb_types.FBDOUBLE_PRECISION,
                fb_types.FBDECFLOAT,
            ):
                # FLOAT, DOUBLE PRECISION or DECFLOAT
                coltype = colclass(row.field_precision)
            elif issubclass(colclass, fb_types._FBInteger):
                # NUMERIC / DECIMAL types are stored as INTEGER types
                if row.field_sub_type == 0:
                    # INTEGERs
                    coltype = colclass()
                elif row.field_sub_type == 1:
                    # NUMERIC
                    coltype = fb_types.FBNUMERIC(
                        precision=row.field_precision, scale=row.field_scale
                    )
                else:
                    # DECIMAL
                    coltype = fb_types.FBDECIMAL(
                        precision=row.field_precision, scale=row.field_scale
                    )
            elif issubclass(colclass, sa_types.DateTime):
                has_timezone = "WITH TIME ZONE" in row.field_type
                coltype = colclass(timezone=has_timezone)
            elif issubclass(colclass, fb_types.FBBLOB):
                if row.field_sub_type == 1:
                    coltype = fb_types.FBTEXT(
                        row.segment_length,
                        row.character_set_name,
                        row.collation_name,
                    )
                else:
                    coltype = fb_types.FBBLOB(row.segment_length)
            else:
                coltype = colclass()

            # Extract default value
            defvalue = None
            if row.default_source is not None:
                # the value comes down as "DEFAULT 'value'": there may be
                # more than one whitespace around the "DEFAULT" keyword
                # and it may also be lower case
                # (see also http://tracker.firebirdsql.org/browse/CORE-356)
                defexpr = row.default_source.lstrip()
                assert defexpr[:8].rstrip().upper() == "DEFAULT", (
                    "Unrecognized default value: %s" % defexpr
                )
                defvalue = defexpr[8:].strip()
                defvalue = defvalue if defvalue != "NULL" else None

            col_d = {
                "name": colname,
                "type": coltype,
                "nullable": not bool(row.null_flag),
                "default": defvalue,
            }

            if orig_colname.lower() == orig_colname:
                col_d["quote"] = True

            if row.computed_source is not None:
                col_d["computed"] = {"sqltext": row.computed_source}

            if row.description is not None:
                col_d["comment"] = row.description

            if row.identity_type is not None:
                col_d["identity"] = {
                    "always": row.identity_type == 0,
                    "start": row.initial_value,
                    "increment": row.generator_increment,
                }

            col_d["autoincrement"] = "identity" in col_d

            cols.append(col_d)

        return columns.items()

    @reflection.cache
    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_pk_constraint(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_pk_constraint(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        pk_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(se.rdb$field_name) AS fname
            FROM rdb$relations r
                 LEFT JOIN rdb$relation_constraints rc
                        ON rc.rdb$relation_name = r.rdb$relation_name
                       AND rc.rdb$constraint_type = 'PRIMARY KEY'
                 LEFT JOIN rdb$index_segments se
                        ON se.rdb$index_name = rc.rdb$index_name
            WHERE {relation_filter}
            ORDER BY r.rdb$relation_name, se.rdb$field_position
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, pk_query, params, has_filter_names
        )

        # Every in-scope relation gets an entry; an empty entry is exactly
        # ReflectionDefaults.pk_constraint().
        result = {}
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            pk = result.setdefault(
                key, {"constrained_columns": [], "name": None}
            )
            if row.cname is not None:
                pk["name"] = self.normalize_name(row.cname)
                if row.fname is not None:
                    pk["constrained_columns"].append(
                        self.normalize_name(row.fname)
                    )

        return result.items()

    @reflection.cache
    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_foreign_keys(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_foreign_keys(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        fk_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(cse.rdb$field_name) AS fname,
                   TRIM(ix2.rdb$relation_name) AS targetrname,
                   TRIM(se.rdb$field_name) AS targetfname,
                   TRIM(rfc.rdb$update_rule) AS update_rule,
                   TRIM(rfc.rdb$delete_rule) AS delete_rule
            FROM rdb$relations r
                 LEFT JOIN rdb$relation_constraints rc
                        ON rc.rdb$relation_name = r.rdb$relation_name
                       AND rc.rdb$constraint_type = 'FOREIGN KEY'
                 LEFT JOIN rdb$ref_constraints rfc
                        ON rfc.rdb$constraint_name = rc.rdb$constraint_name
                 LEFT JOIN rdb$indices ix1
                        ON ix1.rdb$index_name = rc.rdb$index_name
                 LEFT JOIN rdb$indices ix2
                        ON ix2.rdb$index_name = ix1.rdb$foreign_key
                 LEFT JOIN rdb$index_segments cse
                        ON cse.rdb$index_name = ix1.rdb$index_name
                 LEFT JOIN rdb$index_segments se
                        ON se.rdb$index_name = ix2.rdb$index_name
                       AND se.rdb$field_position = cse.rdb$field_position
            WHERE {relation_filter}
            ORDER BY r.rdb$relation_name, rc.rdb$constraint_name, se.rdb$field_position
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, fk_query, params, has_filter_names
        )

        # Each in-scope relation gets an entry, defaulting to the empty list
        # (== ReflectionDefaults.foreign_keys()) when it has no foreign keys.
        result = {}
        fk_by_name = {}
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            if key not in result:
                result[key] = []
                fk_by_name[key] = {}
            if row.cname is None:
                continue

            cname = self.normalize_name(row.cname)
            fk = fk_by_name[key].get(cname)
            if fk is None:
                fk = {
                    "name": cname,
                    "constrained_columns": [],
                    "referred_schema": None,
                    "referred_table": self.normalize_name(row.targetrname),
                    "referred_columns": [],
                    "options": {},
                }
                fk_by_name[key][cname] = fk
                result[key].append(fk)
            fk["constrained_columns"].append(self.normalize_name(row.fname))
            fk["referred_columns"].append(self.normalize_name(row.targetfname))
            if row.update_rule not in ["NO ACTION", "RESTRICT"]:
                fk["options"]["onupdate"] = row.update_rule
            if row.delete_rule not in ["NO ACTION", "RESTRICT"]:
                fk["options"]["ondelete"] = row.delete_rule

        return result.items()

    @reflection.cache
    def get_indexes(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_indexes(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def _get_column_sets(self, connection, schema, kind, scope, filter_names):
        # Map each in-scope relation to the set of its (normalized) column
        # names, used to tell apart columns from functions inside an
        # expression-based index definition.
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        colset_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rf.rdb$field_name) AS field_name
            FROM rdb$relations r
                 JOIN rdb$relation_fields rf
                   ON rf.rdb$relation_name = r.rdb$relation_name
            WHERE {relation_filter}
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, colset_query, params, has_filter_names
        )
        colsets = util.defaultdict(set)
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            colsets[key].add(self.normalize_name(row.field_name))
        return colsets

    def get_multi_indexes(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        condition_source_expr = "TRIM(SUBSTRING(ix.rdb$condition_source FROM 6 FOR CHAR_LENGTH(ix.rdb$condition_source) - 5))"

        if self.server_version_info < (5,):
            # Firebird 4 and lower doesn't have RDB$CONDITION_SOURCE (for partial indices)
            condition_source_expr = "CAST(NULL AS BLOB SUB_TYPE TEXT)"

        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        # Exclude indexes that back a FOREIGN KEY (rdb$foreign_key) or a
        # PRIMARY KEY constraint via the join condition, so a table whose only
        # indexes are constraint-backed still appears (with an empty entry)
        # instead of vanishing from the result set.
        indexes_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(ix.rdb$index_name) AS index_name,
                   ix.rdb$unique_flag AS unique_flag,
                   ix.rdb$index_type AS descending_flag,
                   TRIM(ic.rdb$field_name) AS field_name,
                   TRIM(ix.rdb$expression_source) AS expression_source,
                   {condition_source} AS condition_source
            FROM rdb$relations r
                 LEFT OUTER JOIN rdb$indices ix
                   ON ix.rdb$relation_name = r.rdb$relation_name
                  AND ix.rdb$foreign_key IS NULL
                  AND ix.rdb$index_name NOT IN (
                          SELECT rc.rdb$index_name
                          FROM rdb$relation_constraints rc
                          WHERE rc.rdb$constraint_type = 'PRIMARY KEY'
                            AND rc.rdb$index_name IS NOT NULL
                      )
                 LEFT OUTER JOIN rdb$index_segments ic
                   ON ic.rdb$index_name = ix.rdb$index_name
            WHERE {relation_filter}
            ORDER BY r.rdb$relation_name, ix.rdb$index_name, ic.rdb$field_position
        """.format(
            condition_source=condition_source_expr,
            relation_filter=relation_filter,
        )

        c = self._exec_reflection_query(
            connection, indexes_query, params, has_filter_names
        )

        result = {}  # key -> {index_name -> indexrec}
        order = util.defaultdict(list)  # key -> index_name order
        has_expressions = False
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            indexes = result.setdefault(key, {})
            if row.index_name is None:
                # relation with no reflectable index -> empty entry
                continue
            indexrec = indexes.get(row.index_name)
            if indexrec is None:
                indexrec = {
                    "name": self.normalize_name(row.index_name),
                    "column_names": [],
                    "unique": bool(row.unique_flag),
                }
                if row.expression_source is not None:
                    # Remove outermost parenthesis added by Firebird
                    expr = row.expression_source[1:-1]
                    indexrec["expressions"] = expr.split(EXPRESSION_SEPARATOR)
                    has_expressions = True
                indexrec["dialect_options"] = {
                    "firebird_descending": bool(row.descending_flag),
                    "firebird_where": row.condition_source,
                }
                indexes[row.index_name] = indexrec
                order[key].append(row.index_name)

            indexrec["column_names"].append(
                self.normalize_name(row.field_name)
            )

        # For expression-based indexes, distinguish column references from
        # functions in the stored expression. One query covers all relations.
        if has_expressions:
            colsets = self._get_column_sets(
                connection, schema, kind, scope, filter_names
            )
            for key, indexes in result.items():
                colset = colsets.get(key, set())
                for indexrec in indexes.values():
                    expr = indexrec.get("expressions")
                    if expr is not None:
                        indexrec["column_names"] = [
                            x if self.normalize_name(x) in colset else None
                            for x in expr
                        ]

        return {
            key: [result[key][name] for name in order[key]] for key in result
        }.items()

    @reflection.cache
    def get_unique_constraints(
        self, connection, table_name, schema=None, **kw
    ):
        data = self.get_multi_unique_constraints(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_unique_constraints(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        unique_constraints_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(se.rdb$field_name) AS column_name
            FROM rdb$relations r
                 LEFT JOIN rdb$relation_constraints rc
                        ON rc.rdb$relation_name = r.rdb$relation_name
                       AND rc.rdb$constraint_type = 'UNIQUE'
                 LEFT JOIN rdb$index_segments se
                        ON se.rdb$index_name = rc.rdb$index_name
            WHERE {relation_filter}
            ORDER BY r.rdb$relation_name, rc.rdb$constraint_name, se.rdb$field_position
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, unique_constraints_query, params, has_filter_names
        )

        result = {}  # key -> {cname -> uc dict}
        order = util.defaultdict(list)  # key -> cname order
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            ucs = result.setdefault(key, {})
            if row.cname is None:
                continue
            cname = self.normalize_name(row.cname)
            cc = ucs.get(cname)
            if cc is None:
                cc = {"name": cname, "column_names": []}
                ucs[cname] = cc
                order[key].append(cname)
            cc["column_names"].append(self.normalize_name(row.column_name))

        return {
            key: [result[key][name] for name in order[key]] for key in result
        }.items()

    @reflection.cache
    def get_table_comment(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_table_comment(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_table_comment(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        # ``comment`` is a Firebird keyword, so the result column is aliased
        # ``table_comment`` to keep attribute access on the row unambiguous.
        table_comment_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(r.rdb$description) AS table_comment
            FROM rdb$relations r
            WHERE {relation_filter}
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, table_comment_query, params, has_filter_names
        )

        # A relation with no comment yields {"text": None}, which equals
        # ReflectionDefaults.table_comment(); a missing relation simply has no
        # entry, so the single-table wrapper raises NoSuchTableError.
        return {
            self._relation_key(schema, row.relation_name, name_map): {
                "text": row.table_comment
            }
            for row in c
        }.items()

    @reflection.cache
    def get_check_constraints(self, connection, table_name, schema=None, **kw):
        data = self.get_multi_check_constraints(
            connection,
            schema=schema,
            filter_names=[table_name],
            scope=reflection.ObjectScope.ANY,
            kind=reflection.ObjectKind.ANY,
            **kw,
        )
        return self._value_or_raise(data, table_name, schema)

    def get_multi_check_constraints(
        self,
        connection,
        *,
        schema=None,
        filter_names=None,
        scope=reflection.ObjectScope.DEFAULT,
        kind=reflection.ObjectKind.TABLE,
        **kw,
    ):
        relation_filter, params, has_filter_names, name_map = (
            self._relation_filter(kind, scope, filter_names)
        )
        check_constraints_query = """
            SELECT TRIM(r.rdb$relation_name) AS relation_name,
                   TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(SUBSTRING(tr.rdb$trigger_source FROM 8 FOR CHAR_LENGTH(tr.rdb$trigger_source) - 8)) AS sqltext
            FROM rdb$relations r
                 LEFT JOIN rdb$relation_constraints rc
                        ON rc.rdb$relation_name = r.rdb$relation_name
                       AND rc.rdb$constraint_type = 'CHECK'
                 LEFT JOIN rdb$check_constraints ck
                        ON ck.rdb$constraint_name = rc.rdb$constraint_name
                 LEFT JOIN rdb$triggers tr
                        ON tr.rdb$trigger_name = ck.rdb$trigger_name
                       AND tr.rdb$trigger_type = 1 /* BEFORE UPDATE */
            WHERE {relation_filter}
            ORDER BY r.rdb$relation_name, rc.rdb$constraint_name
        """.format(relation_filter=relation_filter)

        c = self._exec_reflection_query(
            connection, check_constraints_query, params, has_filter_names
        )

        result = {}  # key -> {cname -> cc dict}
        order = util.defaultdict(list)  # key -> cname order
        for row in c:
            key = self._relation_key(schema, row.relation_name, name_map)
            ccs = result.setdefault(key, {})
            if row.cname is None:
                continue
            cname = self.normalize_name(row.cname)
            if cname not in ccs:
                ccs[cname] = {"name": cname, "sqltext": row.sqltext}
                order[key].append(cname)

        return {
            key: [result[key][name] for name in order[key]] for key in result
        }.items()

    @reflection.cache
    def _load_domains(self, connection, schema=None, **kw):
        domains_query = """
            SELECT TRIM(f.rdb$field_name) AS fname,
                   f.rdb$null_flag AS null_flag,
                   NULLIF(TRIM(SUBSTRING(f.rdb$default_source FROM 8 FOR CHAR_LENGTH(f.rdb$default_source) - 7)), 'NULL') fdefault,
                   TRIM(SUBSTRING(f.rdb$validation_source FROM 8 FOR CHAR_LENGTH(f.rdb$validation_source) - 8)) fcheck,
                   TRIM(f.rdb$description) fcomment
            FROM rdb$fields f
            WHERE COALESCE(f.rdb$system_flag, 0) = 0
              AND f.rdb$field_name NOT STARTING WITH 'RDB$'
            ORDER BY 1
        """
        result = connection.exec_driver_sql(domains_query)
        return [
            {
                "name": self.normalize_name(row["fname"]),
                "nullable": not bool(row.null_flag),
                "default": row["fdefault"],
                "check": row["fcheck"],
                "comment": row["fcomment"],
            }
            for row in result.mappings()
        ]

    def is_disconnect(self, e, connection, cursor):
        if isinstance(e, (self.dbapi.DatabaseError)):
            sqlcode = e.sqlcode
            gdscode = e.gds_codes[0]
            return (
                sqlcode == -902
                and gdscode
                in (
                    335544726,  # net_read_err     Error reading data from the connection
                    335544727,  # net_write_err    Error writing data to the connection
                    335544721,  # network_error    Unable to complete network request to host "@1"
                    335544856,  # att_shutdown     Connection shutdown
                )
            )

        return False

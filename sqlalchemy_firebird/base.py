# Allow circular references between FBDialect and FBInspector
from __future__ import annotations

from packaging import version

from typing import Any, List, TypedDict
from typing import Optional

from sqlalchemy import __version__ as SQLALCHEMY_VERSION
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
        if self.dialect.using_sqlalchemy2:
            return super().returning_clause(stmt, returning_cols, **kw)

        # For SQLAlchemy 1.4 compatibility only. Unneeded in 2.0.
        columns = [
            self._label_returning_column(stmt, c)
            for c in expression._select_iterables(returning_cols)
        ]

        return "RETURNING " + ", ".join(columns)


class FBDDLCompiler(sql.compiler.DDLCompiler):
    def get_column_specification(self, column, **kwargs):
        colspec = self.preparer.format_column(column)

        impl_type = column.type.dialect_impl(self.dialect)
        if isinstance(impl_type, sa_types.TypeDecorator):
            impl_type = impl_type.impl

        has_identity = column.identity is not None

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
            colspec += " INTEGER GENERATED BY DEFAULT AS IDENTITY"
        else:
            type_compiler_instance = (
                self.dialect.type_compiler_instance
                if self.dialect.using_sqlalchemy2
                else self.dialect.type_compiler
            )

            colspec += " " + type_compiler_instance.process(
                column.type,
                type_expression=column,
                identifier_preparer=self.preparer,
            )
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
        firebird_3_or_lower = (
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

        if not firebird_3_or_lower:
            if identity_options.increment is not None:
                txt.append("INCREMENT BY %d" % identity_options.increment)

        return " ".join(txt)

    def visit_identity_column(self, identity, **kw):
        firebird_3_or_lower = (
            self.dialect.server_version_info
            and self.dialect.server_version_info < (4,)
        )

        kind = (
            "ALWAYS"
            if identity.always and (not firebird_3_or_lower)
            else "BY DEFAULT"
        )
        text = "GENERATED %s AS IDENTITY" % kind

        options = self.get_identity_options(identity)
        if options:
            text += " (%s)" % options

        return text


class FBTypeCompiler(compiler.GenericTypeCompiler):
    def visit_boolean(self, type_, **kw):
        if self.dialect.server_version_info < (3,):
            return self.visit_SMALLINT(type_, **kw)

        return self.visit_BOOLEAN(type_, **kw)

    def visit_datetime(self, type_, **kw):
        return self.visit_TIMESTAMP(type_, **kw)

    def _render_string_type(
        self,
        name: str,
        length: Optional[int]=None,
        collation: Optional[str]=None,
        charset: Optional[str]=None,
    ) -> str:
        firebird_3_or_lower = (
            self.dialect.server_version_info
            and self.dialect.server_version_info < (4,)
        )

        if name in ["BINARY", "VARBINARY", "NCHAR", "NVARCHAR"]:
            charset = None
            collation = None

        if name == "NVARCHAR":
            name = "NATIONAL CHARACTER VARYING"

        if firebird_3_or_lower:
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
        return self._render_string_type(
            "CHAR",
            type_.length,
            type_.collation,
            getattr(type_, "charset", None),
        )

    def visit_NCHAR(self, type_: fb_types.FBNCHAR, **kw: Any) -> str:
        return self._render_string_type("NCHAR", type_.length, type_.collation)

    def visit_VARCHAR(self, type_: fb_types.FBVARCHAR, **kw: Any) -> str:
        return self._render_string_type(
            "VARCHAR",
            type_.length,
            type_.collation,
            getattr(type_, "charset", None),
        )

    def visit_BINARY(self, type_: fb_types.FBBINARY, **kw) -> str:
        return self._render_string_type("BINARY", type_.length)

    def visit_VARBINARY(self, type_: fb_types.FBVARBINARY, **kw) -> str:
        return self._render_string_type("VARBINARY", type_.length)

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
    bind_typing = BindTyping.RENDER_CASTS

    supports_alter = True
    supports_sane_rowcount = True
    supports_sane_multi_rowcount = False

    supports_native_boolean = True  # False for Firebird 2.5
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
    supports_identity_columns = True  # False for Firebird 2.5

    statement_compiler = FBCompiler
    ddl_compiler = FBDDLCompiler
    type_compiler_cls = FBTypeCompiler
    type_compiler = FBTypeCompiler  # For SQLAlchemy 1.4 compatibility only. Unneeded in 2.0.
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
        sa_types.Numeric: fb_types._FBNumeric,
        sa_types.Float: fb_types.FBFLOAT,
        sa_types.Double: fb_types.FBDOUBLE_PRECISION,
        sa_types.Date: fb_types.FBDATE,
        sa_types.Time: fb_types.FBTIME,
        sa_types.DateTime: fb_types.FBTIMESTAMP,
        sa_types.Interval: fb_types._FBInterval,
        sa_types.BigInteger: fb_types.FBBIGINT,
        sa_types.Integer: fb_types.FBINTEGER,
        sa_types.SmallInteger: fb_types.FBSMALLINT,
        sa_types.BINARY: fb_types._FBLargeBinary,
        sa_types.VARBINARY: fb_types._FBLargeBinary,
        sa_types.LargeBinary: fb_types._FBLargeBinary,
    }

    # SELECT TRIM(rdb$type_name) FROM rdb$types WHERE rdb$field_name = 'RDB$FIELD_TYPE' ORDER BY 1
    ischema_names = {
        "BLOB": fb_types._FBLargeBinary,
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

    using_sqlalchemy2 = version.parse(SQLALCHEMY_VERSION).major >= 2

    def initialize(self, connection):
        super().initialize(connection)

        if self.server_version_info < (3,):
            # Firebird 2.5
            from .fb_info25 import MAX_IDENTIFIER_LENGTH, RESERVED_WORDS

            self.supports_identity_columns = False
            self.supports_native_boolean = False
        elif self.server_version_info < (4,):
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

    @reflection.cache
    def get_columns(  # noqa: C901
        self, connection, table_name, schema=None, **kw
    ):
        columns_query = """
            SELECT TRIM(rf.rdb$field_name) AS field_name,
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
                   f.rdb$computed_source AS computed_source
                  ,rf.rdb$identity_type AS identity_type,                      -- [fb3+]
                   g.rdb$initial_value AS initial_value,                       -- [fb3+]
                   g.rdb$generator_increment AS generator_increment            -- [fb3+]
            FROM rdb$relation_fields rf
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
                 LEFT JOIN rdb$generators g                                    -- [fb3+]
                        ON g.rdb$generator_name = rf.rdb$generator_name        -- [fb3+]
            WHERE COALESCE(f.rdb$system_flag, 0) = 0
              AND rf.rdb$relation_name = ?
            ORDER BY rf.rdb$field_position
        """

        is_firebird_25 = self.server_version_info < (3,)
        has_identity_columns = not is_firebird_25
        if not has_identity_columns:
            # Firebird 2.5 doesn't have RDB$GENERATOR_NAME nor RDB$IDENTITY_TYPE in RDB$RELATION_FIELDS
            #   Remove query lines containing [fb3+]
            lines = str.splitlines(columns_query)
            filtered = filter(lambda x: "[fb3+]" not in x, lines)
            columns_query = "\r\n".join(list(filtered))

        tablename = self.denormalize_name(table_name)
        c = list(connection.exec_driver_sql(columns_query, (tablename,)))

        cols = []
        for row in c:
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
            elif issubclass(colclass, fb_types._FBNumeric):
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
            elif issubclass(colclass, fb_types._FBLargeBinary):
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

            if has_identity_columns:
                if row.identity_type is not None:
                    col_d["identity"] = {
                        "always": row.identity_type == 0,
                        "start": row.initial_value,
                        "increment": row.generator_increment,
                    }

                col_d["autoincrement"] = "identity" in col_d
            else:
                # For Firebird 2.5

                # A backend is better off not returning "autoincrement" at all,
                # instead of potentially returning "False" for an auto-incrementing
                # primary key column. (see test_autoincrement_col)
                pass

            cols.append(col_d)

        if cols:
            return cols

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.columns()
            if self.using_sqlalchemy2
            else []
        )

    @reflection.cache
    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        pk_query = """
            SELECT TRIM(rc.rdb$constraint_name) AS cname, TRIM(se.rdb$field_name) AS fname
            FROM rdb$relation_constraints rc
                 JOIN rdb$index_segments se
                   ON se.rdb$index_name = rc.rdb$index_name
            WHERE rc.rdb$constraint_type = 'PRIMARY KEY'
              AND rc.rdb$relation_name = ?
            ORDER BY se.rdb$field_position
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(pk_query, (tablename,))

        rows = c.fetchall()
        pkfields = (
            [self.normalize_name(r.fname) for r in rows] if rows else None
        )
        if pkfields:
            return {
                "constrained_columns": pkfields,
                "name": self.normalize_name(rows[0].cname) if rows else None,
            }

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.pk_constraint()
            if self.using_sqlalchemy2
            else {"constrained_columns": [], "name": None}
        )

    @reflection.cache
    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        fk_query = """
            SELECT TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(cse.rdb$field_name) AS fname,
                   TRIM(ix2.rdb$relation_name) AS targetrname,
                   TRIM(se.rdb$field_name) AS targetfname,
                   TRIM(rfc.rdb$update_rule) AS update_rule,
                   TRIM(rfc.rdb$delete_rule) AS delete_rule
            FROM rdb$relation_constraints rc
                 JOIN rdb$ref_constraints rfc 
                   ON rfc.rdb$constraint_name = rc.rdb$constraint_name
                 JOIN rdb$indices ix1 
                   ON ix1.rdb$index_name = rc.rdb$index_name
                 JOIN rdb$indices ix2 
                   ON ix2.rdb$index_name = ix1.rdb$foreign_key
                 JOIN rdb$index_segments cse 
                   ON cse.rdb$index_name = ix1.rdb$index_name
                 JOIN rdb$index_segments se 
                   ON se.rdb$index_name = ix2.rdb$index_name
                  AND se.rdb$field_position = cse.rdb$field_position
            WHERE rc.rdb$constraint_type = 'FOREIGN KEY'
              AND rc.rdb$relation_name = ?
            ORDER BY rc.rdb$constraint_name, se.rdb$field_position
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(fk_query, (tablename,))

        fks = util.defaultdict(
            lambda: {
                "name": None,
                "constrained_columns": [],
                "referred_schema": None,
                "referred_table": None,
                "referred_columns": [],
                "options": {},
            }
        )

        for row in c:
            cname = self.normalize_name(row.cname)
            fk = fks[cname]
            if not fk["name"]:
                fk["name"] = cname
                fk["referred_table"] = self.normalize_name(row.targetrname)
            fk["constrained_columns"].append(self.normalize_name(row.fname))
            fk["referred_columns"].append(self.normalize_name(row.targetfname))
            if row.update_rule not in ["NO ACTION", "RESTRICT"]:
                fk["options"]["onupdate"] = row.update_rule
            if row.delete_rule not in ["NO ACTION", "RESTRICT"]:
                fk["options"]["ondelete"] = row.delete_rule

        result = list(fks.values())
        if result:
            return result

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.foreign_keys()
            if self.using_sqlalchemy2
            else []
        )

    @reflection.cache
    def get_indexes(self, connection, table_name, schema=None, **kw):
        condition_source_expr = "TRIM(SUBSTRING(ix.rdb$condition_source FROM 6 FOR CHAR_LENGTH(ix.rdb$condition_source) - 5))"

        if self.server_version_info < (5,):
            # Firebird 4 and lower doesn't have RDB$CONDITION_SOURCE (for partial indices)
            condition_source_expr = "CAST(NULL AS BLOB SUB_TYPE TEXT)"

        indexes_query = f"""
            SELECT TRIM(ix.rdb$index_name) AS index_name,
                   ix.rdb$unique_flag AS unique_flag,
                   ix.rdb$index_type AS descending_flag,
                   TRIM(ic.rdb$field_name) AS field_name,
                   TRIM(ix.rdb$expression_source) expression_source,
                   {condition_source_expr} condition_source
            FROM rdb$indices ix
                LEFT OUTER JOIN rdb$index_segments ic
                  ON ic.rdb$index_name = ix.rdb$index_name
                LEFT OUTER JOIN rdb$relation_constraints rc
                             ON rc.rdb$index_name = ic.rdb$index_name
            WHERE ix.rdb$relation_name = :relation_name
              AND ix.rdb$foreign_key IS NULL
              AND COALESCE(rc.rdb$constraint_type, '') <> 'PRIMARY KEY'
            ORDER BY ix.rdb$index_name, ic.rdb$field_position
        """
        tablename = self.denormalize_name(table_name)

        # Do not use connection.exec_driver_sql() here.
        #    During tests we need to commit CREATE INDEX before this query. See provision.py listener.
        c = connection.execute(
            text(indexes_query), {"relation_name": tablename}
        )

        indexes = util.defaultdict(dict)
        for row in c:
            indexrec = indexes[row.index_name]
            if "name" not in indexrec:
                indexrec["name"] = self.normalize_name(row.index_name)
                indexrec["column_names"] = []
                indexrec["unique"] = bool(row.unique_flag)
                if row.expression_source is not None:
                    expr = row.expression_source[
                        1:-1
                    ]  # Remove outermost parenthesis added by Firebird
                    indexrec["expressions"] = expr.split(EXPRESSION_SEPARATOR)
                indexrec["dialect_options"] = {
                    "firebird_descending": bool(row.descending_flag),
                    "firebird_where": row.condition_source,
                }

            indexrec["column_names"].append(
                self.normalize_name(row.field_name)
            )

        def _get_column_set(tablename):
            colqry = """
                SELECT TRIM(r.rdb$field_name) AS fname
                FROM rdb$relation_fields r
                WHERE r.rdb$relation_name = ?
            """
            return {
                self.normalize_name(row.fname)
                for row in connection.exec_driver_sql(colqry, (tablename,))
            }

        def _adjust_column_names_for_expressions(result, tablename):
            # Identify which expression elements are columns
            colset = _get_column_set(tablename)
            for i in result:
                expr = i.get("expressions")
                if expr is not None:
                    i["column_names"] = [
                        x if self.normalize_name(x) in colset else None
                        for x in expr
                    ]
            return result

        result = list(indexes.values())
        if result:
            return _adjust_column_names_for_expressions(result, tablename)

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.indexes()
            if self.using_sqlalchemy2
            else []
        )

    @reflection.cache
    def get_unique_constraints(
        self, connection, table_name, schema=None, **kw
    ):
        unique_constraints_query = """
            SELECT TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(se.rdb$field_name) AS column_name
            FROM rdb$index_segments se
                 JOIN rdb$relation_constraints rc
                   ON rc.rdb$index_name = se.rdb$index_name
                 JOIN rdb$relations r
                   ON r.rdb$relation_name = rc.rdb$relation_name
                  AND COALESCE(r.rdb$system_flag, 0) = 0
            WHERE rc.rdb$constraint_type = 'UNIQUE'
              AND r.rdb$relation_name = ?
            ORDER BY rc.rdb$constraint_name, se.rdb$field_position
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(unique_constraints_query, (tablename,))

        ucs = util.defaultdict(lambda: {"name": None, "column_names": []})

        for row in c:
            cname = self.normalize_name(row.cname)
            cc = ucs[cname]
            if not cc["name"]:
                cc["name"] = cname
            cc["column_names"].append(self.normalize_name(row.column_name))

        result = list(ucs.values())
        if result:
            return result

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.unique_constraints()
            if self.using_sqlalchemy2
            else []
        )

    @reflection.cache
    def get_table_comment(self, connection, table_name, schema=None, **kw):
        table_comment_query = """
            SELECT TRIM(rdb$description) AS comment
            FROM rdb$relations
            WHERE rdb$relation_name = ?
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(table_comment_query, (tablename,))

        row = c.fetchone()
        if row:
            return {"text": row[0]}

        raise exc.NoSuchTableError(table_name)

    @reflection.cache
    def get_check_constraints(self, connection, table_name, schema=None, **kw):
        check_constraints_query = """
            SELECT TRIM(rc.rdb$constraint_name) AS cname,
                   TRIM(SUBSTRING(tr.rdb$trigger_source FROM 8 FOR CHAR_LENGTH(tr.rdb$trigger_source) - 8)) AS sqltext
            FROM rdb$relation_constraints rc
                 JOIN rdb$check_constraints ck
                   ON ck.rdb$constraint_name = rc.rdb$constraint_name
                 JOIN rdb$triggers tr
                   ON tr.rdb$trigger_name = ck.rdb$trigger_name AND
                      tr.rdb$trigger_type = 1 /* BEFORE UPDATE */
            WHERE rc.rdb$constraint_type = 'CHECK' AND
                  rc.rdb$relation_name = ?
            ORDER BY 1
        """
        tablename = self.denormalize_name(table_name)
        c = connection.exec_driver_sql(check_constraints_query, (tablename,))

        ccs = util.defaultdict(
            lambda: {
                "name": None,
                "sqltext": None,
            }
        )

        for row in c:
            cname = self.normalize_name(row.cname)
            cc = ccs[cname]
            if not cc["name"]:
                cc["name"] = cname
                cc["sqltext"] = row.sqltext

        result = list(ccs.values())
        if result:
            return result

        if not self.has_table(connection, table_name, schema):
            raise exc.NoSuchTableError(table_name)

        return (
            reflection.ReflectionDefaults.check_constraints()
            if self.using_sqlalchemy2
            else []
        )

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
        is_fdb = self.driver == "fdb"
        if isinstance(e, (self.dbapi.DatabaseError)):
            sqlcode = e.args[1] if is_fdb else e.sqlcode
            gdscode = e.args[2] if is_fdb else e.gds_codes[0]
            return sqlcode == -902 and gdscode in (
                335544726,  # net_read_err     Error reading data from the connection
                335544727,  # net_write_err    Error writing data to the connection
                335544721,  # network_error    Unable to complete network request to host "@1"
                335544856,  # att_shutdown     Connection shutdown
            )

        return False

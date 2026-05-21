import datetime as dt
import uuid as _uuid

from typing import Any
from typing import Optional
from sqlalchemy import Dialect, types as sqltypes


# Character set of BINARY/VARBINARY
BINARY_CHARSET = "OCTETS"

# Character set of NCHAR/NVARCHAR
NATIONAL_CHARSET = "ISO8859_1"


class _FBString(sqltypes.String):
    render_bind_cast = True

    def __init__(
        self,
        charset: Optional[str] = None,
        length: Optional[int] = None,
        collation: Optional[str] = None,
        **kw: Any,
    ):
        # ``**kw`` swallows the internal keywords SQLAlchemy injects when it
        # adapts another String subclass into this impl (e.g. Enum's
        # ``_enums``), which String.__init__ would otherwise reject.
        self.charset = charset
        super().__init__(length=length, collation=collation)


class FBCHAR(_FBString, sqltypes.CHAR):
    __visit_name__ = "CHAR"

    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        super().__init__(length=length, **kwargs)


class FBBINARY(FBCHAR):
    __visit_name__ = "BINARY"

    # Synonym for CHAR(n) CHARACTER SET OCTETS
    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        kwargs["charset"] = BINARY_CHARSET
        super().__init__(length=length, **kwargs)


class FBNCHAR(FBCHAR, sqltypes.NCHAR):
    __visit_name__ = "NCHAR"

    # Synonym for CHAR(n) CHARACTER SET ISO8859_1
    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        kwargs["charset"] = NATIONAL_CHARSET
        super().__init__(length=length, **kwargs)


class FBVARCHAR(_FBString, sqltypes.VARCHAR):
    __visit_name__ = "VARCHAR"

    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        super().__init__(length=length, **kwargs)


class FBVARBINARY(FBVARCHAR):
    __visit_name__ = "VARBINARY"

    # Synonym for VARCHAR(n) CHARACTER SET OCTETS
    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        kwargs["charset"] = BINARY_CHARSET
        super().__init__(length=length, **kwargs)


class FBNVARCHAR(FBVARCHAR, sqltypes.NVARCHAR):
    __visit_name__ = "NVARCHAR"

    # Synonym for VARCHAR(n) CHARACTER SET ISO8859_1
    def __init__(self, length: Optional[int] = None, **kwargs: Any):
        kwargs["charset"] = NATIONAL_CHARSET
        super().__init__(length=length, **kwargs)


class FBFLOAT(sqltypes.FLOAT):
    __visit_name__ = "FLOAT"
    render_bind_cast = True


class FBDOUBLE_PRECISION(sqltypes.DOUBLE_PRECISION):
    __visit_name__ = "DOUBLE_PRECISION"
    render_bind_cast = True


class FBDECFLOAT(sqltypes.Numeric):
    __visit_name__ = "DECFLOAT"
    render_bind_cast = True

    def __init__(self, precision=None, **kwargs):
        # Unlike Numeric, DECFLOAT returns floats unless asdecimal is set.
        kwargs.setdefault("asdecimal", False)
        super().__init__(precision=precision, **kwargs)


class FBREAL(FBFLOAT):
    __visit_name__ = "REAL"


class FBDECIMAL(sqltypes.DECIMAL):
    __visit_name__ = "DECIMAL"
    render_bind_cast = True


class FBNUMERIC(sqltypes.NUMERIC):
    __visit_name__ = "NUMERIC"
    render_bind_cast = True


class FBDATE(sqltypes.DATE):
    render_bind_cast = True


class FBTIME(sqltypes.TIME):
    render_bind_cast = True


class FBTIMESTAMP(sqltypes.TIMESTAMP):
    render_bind_cast = True


class _FBInteger(sqltypes.Integer):
    render_bind_cast = True


class FBSMALLINT(_FBInteger):
    __visit_name__ = "SMALLINT"


class FBINTEGER(_FBInteger):
    __visit_name__ = "INTEGER"


class FBBIGINT(_FBInteger):
    __visit_name__ = "BIGINT"


class FBINT128(_FBInteger):
    __visit_name__ = "INT128"


class FBUUID(sqltypes.Uuid):
    """Firebird UUID storage.

    Firebird has no dedicated ``UUID`` SQL type. A UUID is stored in its
    canonical 16-byte form -- ``BINARY(16)`` on Firebird 4.0+ and
    ``CHAR(16) CHARACTER SET OCTETS`` on Firebird 3.0 (the same physical
    container; see ``FBTypeCompiler.visit_uuid``). ``GEN_UUID()`` produces
    these 16-byte values server-side.

    Only the ``native_uuid=True`` form (the default) uses the 16-byte
    storage; ``Uuid(native_uuid=False)`` keeps SQLAlchemy's backend-agnostic
    ``CHAR(32)`` hex-string behaviour, so both round-trip correctly.
    """

    render_bind_cast = True
    cache_ok = True

    def __init__(self, as_uuid: bool = True, native_uuid: bool = True):
        super().__init__(as_uuid=as_uuid, native_uuid=native_uuid)

    def bind_processor(self, dialect: Dialect):
        if not self.native_uuid:
            # CHAR(32) hex-string storage -- defer to the generic Uuid.
            return super().bind_processor(dialect)

        if self.as_uuid:

            def process(value: Optional[_uuid.UUID]):
                return value.bytes if value is not None else None

        else:

            def process(value: Optional[str]):
                return _uuid.UUID(value).bytes if value is not None else None

        return process

    def result_processor(self, dialect: Dialect, coltype: Any):
        if not self.native_uuid:
            return super().result_processor(dialect, coltype)

        if self.as_uuid:

            def process(value: Any) -> Optional[_uuid.UUID]:
                return (
                    _uuid.UUID(bytes=bytes(value))
                    if value is not None
                    else None
                )

        else:

            def process(value: Any) -> Optional[str]:
                return (
                    str(_uuid.UUID(bytes=bytes(value)))
                    if value is not None
                    else None
                )

        return process

    def literal_processor(self, dialect: Dialect):
        if not self.native_uuid:
            return super().literal_processor(dialect)

        if self.as_uuid:

            def process(value: _uuid.UUID) -> str:
                return "x'%s'" % value.hex

        else:

            def process(value: str) -> str:
                return "x'%s'" % _uuid.UUID(value).hex

        return process


class FBBOOLEAN(sqltypes.BOOLEAN):
    render_bind_cast = True


class FBBLOB(sqltypes.BLOB):
    __visit_name__ = "BLOB"  # BLOB SUB_TYPE 0 (BINARY)
    render_bind_cast = True

    def __init__(self, segment_size=None):
        super().__init__()
        self.subtype = 0
        self.segment_size = segment_size

    def bind_processor(self, dialect):
        def process(value):
            return bytes(value)

        return process


class FBTEXT(sqltypes.TEXT):
    __visit_name__ = "BLOB"  # BLOB SUB_TYPE 1 (TEXT)
    render_bind_cast = True

    def __init__(self, segment_size=None, charset=None, collation=None):
        super().__init__()
        self.subtype = 1
        self.segment_size = segment_size
        self.charset = charset
        self.collation = collation


class _FBNumericInterval(FBNUMERIC):
    # NUMERIC(18,9) -- Used for _FBInterval storage
    def __init__(self, **kwargs: Any):
        kwargs["precision"] = 18
        kwargs["scale"] = 9
        super().__init__(**kwargs)


class _FBInterval(sqltypes.Interval):
    """A type for ``datetime.timedelta()`` objects.

    Value is stored as number of days.
    """

    # ToDo: Fix operations with TIME datatype (operand must be in seconds, not in days)
    #   https://firebirdsql.org/file/documentation/html/en/refdocs/fblangref50/firebird-50-language-reference.html#fblangref50-datatypes-datetimeops

    impl = _FBNumericInterval
    cache_ok = True

    def __init__(self):
        super().__init__(native=False)

    def bind_processor(self, dialect: Dialect):
        impl_processor = self.impl_instance.bind_processor(dialect)
        if impl_processor:
            fixed_impl_processor = impl_processor

            def process(value: Optional[dt.timedelta]):
                dt_value = (
                    value.total_seconds() / 86400
                    if value is not None
                    else None
                )
                return fixed_impl_processor(dt_value)

        else:

            def process(value: Optional[dt.timedelta]):
                return (
                    value.total_seconds() / 86400
                    if value is not None
                    else None
                )

        return process

    def result_processor(self, dialect: Dialect, coltype: Any):
        impl_processor = self.impl_instance.result_processor(dialect, coltype)
        if impl_processor:
            fixed_impl_processor = impl_processor

            def process(value: Any) -> Optional[dt.timedelta]:
                dt_value = fixed_impl_processor(value)
                if dt_value is None:
                    return None
                return dt.timedelta(days=dt_value)

        else:

            def process(value: Any) -> Optional[dt.timedelta]:
                return dt.timedelta(days=value) if value is not None else None

        return process

# sqlalchemy-firebird

An external [SQLAlchemy](https://www.sqlalchemy.org) dialect for the [Firebird](https://firebirdsql.org/en/start/) database server.

This package targets **SQLAlchemy 2.0+** and **Firebird 3.0+**, using the modern [`firebird-driver`](https://pypi.org/project/firebird-driver/) Python DB-API 2.0 driver.

## Installation

```
pip install sqlalchemy-firebird
```

This installs SQLAlchemy 2.0+ and `firebird-driver` automatically. Python 3.11 or newer is required.

## Connection strings

A SQLAlchemy URL has the shape `dialect+driver://username:password@host:port/database`. For Firebird:

```
firebird+firebird://<username>:<password>@<host>:<port>/<database_path>[?charset=UTF8&key=value&...]
```

Useful query parameters:

- `charset` — character set used by the database file (Firebird default is `UTF8`).
- `fb_client_library` — full path to the Firebird client library (`libfbclient.so` on Linux, `fbclient.dll` on Windows). Only needed when using an embedded server or a non-default client install.

### Examples

Local server, default port:

```
[Linux]
firebird+firebird://sysdba:masterkey@localhost///home/me/databases/my_project.fdb

[Windows]
firebird+firebird://sysdba:masterkey@localhost/c:/databases/my_project.fdb
```

Remote server on port 3040 with explicit charset and client library:

```
[Linux]
firebird+firebird://sysdba:masterkey@example.com:3040///srv/databases/my_project.fdb?charset=UTF8&fb_client_library=/opt/firebird/lib/libfbclient.so

[Windows]
firebird+firebird://sysdba:masterkey@example.com:3040/c:/databases/my_project.fdb?charset=UTF8&fb_client_library=c:/firebird/fbclient.dll
```

Embedded server:

```
[Linux]
firebird+firebird://sysdba@///home/me/databases/my_project.fdb?charset=UTF8&fb_client_library=/opt/firebird/lib/libfbclient.so

[Windows]
firebird+firebird://sysdba@/c:/databases/my_project.fdb?charset=UTF8&fb_client_library=c:/firebird/fbclient.dll
```

## Usage

```python
from sqlalchemy import create_engine

db_uri = "firebird+firebird://sysdba@/c:/databases/my_project.fdb?charset=UTF8&fb_client_library=c:/firebird/fbclient.dll"
engine = create_engine(db_uri, echo=True)

# Force the engine to connect, surfacing any URL or driver issues immediately.
with engine.begin():
    pass
```

## Code of Conduct

This project follows the SQLAlchemy [Code of Conduct](http://www.sqlalchemy.org/codeofconduct.html).

## License

Distributed under the [MIT license](http://www.opensource.org/licenses/mit-license.php).

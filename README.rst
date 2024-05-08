sqlalchemy-firebird
###################

An external SQLAlchemy dialect for Firebird
===========================================
.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/psf/black
.. image:: https://github.com/pauldex/sqlalchemy-firebird/workflows/sqlalchemy-firebird/badge.svg
    :target: https://github.com/pauldex/sqlalchemy-firebird

----

Those who want to use the open source `Firebird <https://firebirdsql.org/en/start/>`_ database server with `Python <https://www.python.org>`_ using `SQLAlchemy <https://www.sqlalchemy.org>`_ need to provide a dialect that SQLAlchemy can use to communicate to the database, because Firebird is not among the included dialects.

This package provides a Firebird dialect for SQLAlchemy using the Python Database API 2.0 compliant support provided from  either `firebird-driver <https://firebird-driver.readthedocs.io/en/latest>`_ or `fdb <https://fdb.readthedocs.io/en/latest>`_.

----

**Installation**

The pip command to install the sqlalchemy-firebird package is::

    pip install sqlalchemy-firebird

If you are using Python 3.8+, installing sqlalchemy-firebird will automatically install SQLAlchemy 2.0+ and firebird-driver.  This configuration can be used to access Firebird server versions 3 and up.  If you need to access a Firebird version 2.5 server, just install fdb using pip::

    pip install fdb

If you are using a version of Python less than 3.8, SQLAlchemy 1.4+ and fdb are automatically installed, which can only be used for Firebird server versions 2.5.9 and 3.0+.

----

**Getting Started**

The first thing you need when connecting your application to the database using SQLAlchemy is an engine object, obtained by calling *create_engine* with the appropriate parameters.  This can be a connection string (also known as a database uniform resource identifier/locator, *dburi* or *dburl* for short), or the URL object returned by calling *create* from sqlalchemy.engine.URL.

The following information is needed to make the connection string:

- <driver_name> - which driver to use; 'firebird' to use firebird-driver, or 'fdb' to use the fdb driver
- <username> - Firebird default is 'sysdba'
- <password> - Firebird default is 'masterkey'
- <host> - the location of the database server
- <port> - Firebird default is '3050'
- <database_path> - location of the database file
- <charset> - character set used by the database file, Firebird default is UTF8
- <client_library_path> - path to the firebird client library file.  Linux needs 'libfbclient.so', Windows uses fblient.dll.  This is only needed when using the embedded server or a remotely installed server.

Connection Strings

A typical connection string for SQLAlchemy is *dialect+driver://username:password@host:port/database*.

The template for a Firebird connection string looks like this (using the information listed above):
::

    firebird+<driver_name>://<username>:<password>@<host>:<port>/<database_path>[?charset=UTF8&key=value&key=value...]

Note the only differences between the Linux and Windows versions of the following example configuration strings is that the Linux paths begin with '//home/testuser' while the Windows paths begin with 'c:/':


- The simplest configuration string is for the Firebird server installed locally using the default port.

::

    [Linux]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba:masterkey@localhost///home/testuser/projects/databases/my_project.fdb
    # Use the firebird-driver driver (Python 3.8+, Firebird server 3.0 or greater)
    firebird+firebird://sysdba:masterkey@localhost///home/testuser/projects/databases/my_project.fdb

    [Windows]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba:masterkey@localhost/c:\\projects\\databases\\my_project.fdb
    # Use the firebird-driver driver (Python 3.8+, Firebird server 3.0 or greater)
    firebird+firebird://sysdba:masterkey@localhost/c:\\projects\\databases\\my_project.fdb

- Firebird server installed remotely using port 3040 and specifying the character set to use

::

    [Linux]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba:masterkey@localhost:3040///home/testuser/databases/my_project.fdb?charset=UTF8&fb_library_name=//home/testuser/dbclient/lib/libfbclient.so
    # Use the firebird-driver driver (Python 3.8+)
    firebird+firebird://sysdba:masterkey@localhost:3040///home/testuser/databases/my_project.fdb?charset=UTF8&fb_client_library=//home/testuser/dbclient/lib/libfbclient.so

    [Windows]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba:masterkey@localhost:3040/c:\\projects\\databases\\my_project.fdb?charset=UTF8&fb_library_name=c:\\projects\\dbclient\\fbclient.dll
    # Use the firebird-driver driver (Python 3.8+)
    firebird+firebird://sysdba:masterkey@localhost:3040/c:\\projects\\databases\\my_project.fdb?charset=UTF8&fb_client_library=c:\\projects\\dbclient\\fbclient.dll

- Firebird embedded server specifying the character set to use

::

    [Linux]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba@///home/testuser/databases/my_project.fdb?charset=UTF8&fb_library_name=//home/testuser/dbserver/lib/libfbclient.so
    # Use the firebird-driver driver (Python 3.8+)
    firebird+firebird://sysdba@///home/testuser/databases/my_project.fdb?charset=UTF8&fb_client_library=//home/testuser/dbserver/lib/libfbclient.so

    [Windows]
    # Use the fdb driver (Python 3.6/3.7, or Firebird server 2.5.9)
    firebird+fdb://sysdba@/c:\\projects\\databases\\my_project.fdb?charset=UTF8&fb_library_name=c:\\projects\\dbserver\\fbclient.dll
    # Use the firebird-driver driver (Python 3.8+)
    firebird+firebird://sysdba@/c:\\projects\\databases\\my_project.fdb?charset=UTF8&fb_client_library=c:\\projects\\dbserver\\fbclient.dll


----

**How to use**

For example, to connect to an embedded Firebird server using firebird-driver on Windows:

::

    db_uri = "firebird+firebird://sysdba@/c:\\projects\\databases\\my_project.fdb?charset=UTF8&fb_client_library=c:\\projects\\databases\\fb40_svr\\fbclient.dll"
    from sqlalchemy import create_engine
    engine = create_engine(db_uri, echo=True)
    
    # force the engine to connect, revealing any problems with the connection string
    with engine.begin():
        pass

Connecting to different types of Firebird servers, databases, or drivers is done simply by changing the db_uri string
used in the call to create_engine.

----

**Code of Conduct**

As with SQLAlchemy, sqlalchemy-firebird places great emphasis on polite, thoughtful, and
constructive communication between users and developers.
We use the SQLAlchemy `Code of Conduct <http://www.sqlalchemy.org/codeofconduct.html>`_.

----

**License**

sqlalchemy-firebird is distributed under the `MIT license
<http://www.opensource.org/licenses/mit-license.php>`_.

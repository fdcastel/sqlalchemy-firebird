# Development notes

Targets Python 3.9+ on Windows/Linux/Mac with `firebird-driver` and SQLAlchemy 2.0+, against Firebird 3.0 or newer.


# Windows environment

## Install Python

You may install Python with [Chocolatey](https://chocolatey.org/install):

```powershell
choco install python -y
```


## Install Visual Studio Code

We strongly recommend Visual Studio Code for development. You may install it with:

```powershell
choco install vscode -y
```


## Initial checkout

This project uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency management. Install it with:

```powershell
winget install --id=astral-sh.uv -e
```

Clone this repository into a local folder on your computer and, from the root folder, run

```powershell
uv sync
```

`uv sync` automatically creates `.venv`, installs the project, and installs the `dev` dependency group (pytest, ruff) from `uv.lock`.

Open the project folder with VSCode. It should detect the virtual environment automatically and activate it. Please refer to [Visual Studio Code documentation on Python](https://code.visualstudio.com/docs/languages/python) for more information.

To activate the virtual environment on a command prompt instance (cmd or powershell) use:

```powershell
.venv/Scripts/activate
```


# Linux environment

## Initial checkout

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), clone this repository into a local folder, and from the root folder run:

```bash
uv sync
```

`uv sync` automatically creates `.venv`, installs the project, and installs the `dev` dependency group (pytest, ruff) from `uv.lock`.

To activate the virtual environment use:

```bash
. .venv/bin/activate
```


# Tests

## Preparing the tests infrastructure

With the virtual environment activated, run the following script

```
rebuild-test-databases
```

This script will 

- Create a `sqlalchemy-firebird-tests` in your temp folder containing the binaries for each supported Firebird version;
- Create databases for each Firebird version; and
- Add a `[db]` section into your `setup.cfg` containing one entry for each of the databases created.

You may run this script whenever you need a clean database for your tests. It won't download the files again if they already exist.


## Running the tests

Run the following Powershell script

```powershell
.\run-all-tests.ps1
```

This will start 5 different processes, each one running a different combination of driver/Firebird version supported.

To run only the tests for a specific database, use

```powershell
.\run-tests.ps1 -Database 'firebird_fb50'
```


## Debugging the tests

SQLAlchemy has a complex test infrastructure which unfortunately is not completely functional from VSCode test runner.

To run a specific test under VSCode debugger this repository already provides a `.vscode/launch.json` file preconfigured as a sample.

E.g. to run the test `test_get_table_names` with `firebird-driver` and Firebird 5.0 you must set `pytest` arguments as:

```json
"args": ["./test/test_suite.py::NormalizedNameTest::test_get_table_names", "--db", "firebird_fb50"],
```

Now run the code (with `F5`) and the debugger should work as expected (e.g. set a breakpoint and it should stop).


## Debugging SQLAlchemy code

Sooner or later you probably will need to debug SQLAlchemy code. Fortunately, this is easy as

```bash
# [From your 'sqlalchemy-firebird' root folder, inside virtual environment]
pip install -e $path_to_your_sqlalchemy_local_folder
```

The `launch.json` file already has the required `"justMyCode": false` configuration which allows you to step into SQLAlchemy source files during debugging.

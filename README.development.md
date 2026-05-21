# Development notes

Targets Python 3.11+ on Windows/Linux/Mac with `firebird-driver` and SQLAlchemy 2.0+, against Firebird 3.0 or newer.


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

The test runner provisions a self-contained Firebird tree on demand using the [PSFirebird](https://github.com/fdcastel/PSFirebird) PowerShell module. PSFirebird downloads the requested Firebird version into a folder under your system temp directory, creates a fresh database file there, and never touches `PATH` or the registry.

The first time `run-tests.ps1` runs it installs PSFirebird from the PowerShell Gallery if it is not already present.


## Running the tests

Run the test suite against a specific Firebird version with:

```powershell
.\run-tests.ps1 -FirebirdVersion 5.0.4
```

Supply a custom environment folder or extra pytest arguments as needed:

```powershell
.\run-tests.ps1 -FirebirdVersion 4.0.7 -EnvironmentPath C:\fb-test\fb40 -PytestArgs '-k', 'test_get_table_names'
```

CI runs the same provisioning flow on **Windows and Linux**, across Firebird 3.0.x, 4.0.x and 5.0.x (full sweep on the latest Python) and brackets Python at the supported floor (3.11) and the latest (3.14). See `.github/workflows/test.yml` for the matrix.


## Debugging the tests

SQLAlchemy has a complex test infrastructure which unfortunately is not completely functional from VSCode test runner.

To run a specific test under VSCode debugger this repository already provides a `.vscode/launch.json` file preconfigured as a sample.

E.g. to run the test `test_get_table_names` against a previously-provisioned Firebird 5.0 environment you must set `pytest` arguments as:

```json
"args": ["./test/test_suite.py::NormalizedNameTest::test_get_table_names", "--dburi", "firebird+firebird://sysdba@/<full-path-to-test.fdb>?charset=UTF8&fb_client_library=<full-path-to-fbclient.dll>"],
```

Now run the code (with `F5`) and the debugger should work as expected (e.g. set a breakpoint and it should stop).


## Debugging SQLAlchemy code

Sooner or later you probably will need to debug SQLAlchemy code. Fortunately, this is easy as

```bash
# [From your 'sqlalchemy-firebird' root folder, inside virtual environment]
pip install -e $path_to_your_sqlalchemy_local_folder
```

The `launch.json` file already has the required `"justMyCode": false` configuration which allows you to step into SQLAlchemy source files during debugging.


# Releasing

Releases are produced by `.github/workflows/release.yml`, which fires on any `v*` tag pushed to GitHub. The package version is derived from the tag via `setuptools-scm` — there is no `__version__` to bump by hand.

All release tags **must follow [PEP 440](https://peps.python.org/pep-0440/)** prefixed with `v`. This is the only supported format:

| Kind | Example tag | Resulting version | GitHub Release marked as |
|---|---|---|---|
| Final | `v2.2.0` | `2.2.0` | stable |
| Alpha | `v2.2.0a1` | `2.2.0a1` | pre-release |
| Beta | `v2.2.0b1` | `2.2.0b1` | pre-release |
| Release candidate | `v2.2.0rc1` | `2.2.0rc1` | pre-release |
| Dev | `v2.2.0.dev1` | `2.2.0.dev1` | pre-release |
| Post-release | `v2.2.0.post1` | `2.2.0.post1` | stable |

**Hyphenated SemVer-style tags like `v2.2.0-beta1` are not supported** and will be rejected by the release workflow at the validation step. They are not valid PEP 440 segments and would produce a wheel filename that differs from the tag. Use `v2.2.0b1` for a beta.

Cutting a release:

```bash
git tag v2.2.0
git push origin v2.2.0
```

The workflow runs three jobs:

1. **build** — validates the tag, builds the sdist + wheel with `uv build`, detects whether the tag is a pre-release, and uploads the artifacts.
2. **github-release** — publishes a GitHub Release with both artifacts attached.
3. **publish-pypi** — uploads the artifacts to [PyPI](https://pypi.org/project/sqlalchemy-firebird/). **This runs for final versions only**; pre-releases (alpha/beta/rc/dev) build and create a GitHub Release but are *not* published to PyPI.

PyPI uploads use [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OpenID Connect) — there are no PyPI tokens stored as secrets. Two one-time prerequisites must be in place for the `publish-pypi` job to succeed:

- **PyPI:** a Trusted Publisher registered for this project at `https://pypi.org/manage/project/sqlalchemy-firebird/settings/publishing/` with Owner `fdcastel`, Repository `sqlalchemy-firebird`, Workflow `release.yml`, and Environment `pypi`.
- **GitHub:** a repository [Environment](https://github.com/fdcastel/sqlalchemy-firebird/settings/environments) named `pypi` (the name must match the Trusted Publisher and the `environment:` in `release.yml`).

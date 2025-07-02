from configparser import ConfigParser
from glob import glob
from os import environ
from os import listdir
from os import makedirs
from os import remove
from os import rename
from os import name as os_name
from os.path import basename
from os.path import isdir
from os.path import isfile
from os.path import join
from shutil import copy, rmtree
from subprocess import run
from sys import argv
from tempfile import gettempdir
from urllib.request import urlretrieve

#
# Globals
#

if os_name == "nt":
    FB50_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v5.0.2/Firebird-5.0.2.1613-0-windows-x64.zip"
    FB40_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v4.0.5/Firebird-4.0.5.3140-0-x64.zip"
    FB30_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v3.0.12/Firebird-3.0.12.33787-0-x64.zip"
    FB25_URL = "https://github.com/FirebirdSQL/firebird/releases/download/R2_5_9/Firebird-2.5.9.27139-0_x64_embed.zip"
    FB25_EXTRA_URL = "https://github.com/FirebirdSQL/firebird/releases/download/R2_5_9/Firebird-2.5.9.27139-0_x64.zip"
else:
    FB50_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v5.0.2/Firebird-5.0.2.1613-0-linux-arm64.tar.gz"
    FB40_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v4.0.5/Firebird-4.0.5.3140-0.amd64.tar.gz"
    FB30_URL = "https://github.com/FirebirdSQL/firebird/releases/download/v3.0.12/Firebird-3.0.12.33787-0.amd64.tar.gz"
    FB25_URL = "https://github.com/FirebirdSQL/firebird/releases/download/R2_5_9/FirebirdCS-2.5.9.27139-0.amd64.tar.gz"

TEMP_PATH = gettempdir()

# Disable black formatter
# fmt: off

#
# Functions
#


def log(message):
    print(message.replace(TEMP_PATH, "$(tmp)"))


def download_firebird(url, root_folder, ipc_name=None):
    source_package = join(root_folder, basename(url))

    extension_length = 3 if os_name == "nt" else 7
    base_name = source_package[:-extension_length]

    target_folder = join(root_folder, base_name)

    log(f"Downloading '{source_package}'...")
    urlretrieve(url, source_package)

    log(f"  Extracting to '{target_folder}'...")
    if os_name == "nt":
        import zipfile

        with zipfile.ZipFile(source_package, "r") as f:
            f.extractall(target_folder)
    else:
        import tarfile

        with tarfile.open(source_package, "r:gz") as f:
            f.extractall(path=root_folder)

        buildroot_package = join(target_folder, "buildroot.tar.gz")
        with tarfile.open(buildroot_package, "r:gz") as f:
            f.extractall(path=target_folder)

    log(f"  Deleting '{source_package}'...")
    remove(source_package)

    # Windows-only: Set unique "IpcName" for each instance
    if os_name == "nt" and ipc_name is not None:
        conf_file = join(target_folder, "firebird.conf")
        log(f"  Patching {conf_file}....")

        with open(conf_file, "r") as f:
            lines = f.read()
        lines = lines.replace("#IpcName = FIREBIRD", f"IpcName = {ipc_name}")
        with open(conf_file, "w") as f:
            f.write(lines)

    return base_name


#
# Main scripts entrypoints
#


def prepare_test_environment(force=True):
    root_folder = join(gettempdir(), "sqlalchemy-firebird-tests")

    if isdir(root_folder) and listdir(root_folder) and not force:
        # Folder already exists and is not empty. Nothing to do.
        log(f"Folder '{root_folder}' already exists.")
        return root_folder

    log(f"Creating {root_folder}...")
    rmtree(root_folder, ignore_errors=True)
    makedirs(root_folder)

    fb50_basename = download_firebird(FB50_URL, root_folder, "FIREBIRD50")
    fb40_basename = download_firebird(FB40_URL, root_folder, "FIREBIRD40")
    fb30_basename = download_firebird(FB30_URL, root_folder, "FIREBIRD30")
    fb25_basename = download_firebird(FB25_URL, root_folder, "FIREBIRD25")

    fb25_root_path = join(root_folder, fb25_basename)

    # Extra steps for Firebird 2.5
    if os_name == "nt":
        # Download non-embedded version to copy isql.exe (which does not exists in the embedded version)
        fb25_extra_basename = download_firebird(FB25_EXTRA_URL, root_folder)
        fb25_extra_path = join(root_folder, fb25_extra_basename)
        fb25_extra_bin_path = join(fb25_extra_path, "bin")
        log(f"  Copy {fb25_extra_basename}/bin/isql.exe...")
        copy(f"{fb25_extra_bin_path}/isql.exe", fb25_root_path)

        # Rename fbembed.dll to fbclient.dll
        log(f"  Renaming '{fb25_extra_basename}/fbembed.dll' to 'fbclient.dll'...")
        rename(f"{fb25_root_path}/fbembed.dll", f"{fb25_root_path}/fbclient.dll")

        log(f"  Deleting {fb25_extra_basename}...")
        rmtree(fb25_extra_path)
    else:
        # On Linux, rename "FirebirdCS" to "Firebird" to keep the same pattern
        rename(fb25_root_path, join(root_folder, "Firebird-2.5.9.27139-0.amd64"))

    log("Test environment ready.")
    return root_folder


def rebuild_test_databases():
    root_folder = prepare_test_environment(force=False)

    log("Rebuilding databases...")

    root_path_for = {
        "fb50": glob(f"{root_folder}/Firebird-5*")[0],
        "fb40": glob(f"{root_folder}/Firebird-4*")[0],
        "fb30": glob(f"{root_folder}/Firebird-3*")[0],
        "fb25": glob(f"{root_folder}/Firebird-2*")[0],
    }

    if os_name != "nt":
        root_path_for["fb50"] = join(root_path_for["fb50"], "opt", "firebird")
        root_path_for["fb40"] = join(root_path_for["fb40"], "opt", "firebird")
        root_path_for["fb30"] = join(root_path_for["fb30"], "opt", "firebird")
        root_path_for["fb25"] = join(root_path_for["fb25"], "opt", "firebird")

    # If an argument is passed, use it to filter only that database
    filter = argv[1] if len(argv) > 1 else None
    if filter:
        log(f"  Only for '{filter}'")

    config = ConfigParser()
    config.read("setup.cfg")

    if not config.has_section("db"):
        config.add_section("db")

    for driver in ["firebird", "fdb"]:
        for engine in ["fb50", "fb40", "fb30", "fb25"]:
            db_key = f"{driver}_{engine}"

            if (filter is not None) and (filter != db_key):
                continue

            # Create database with isql
            if os_name == "nt":
                isql = join(root_path_for[engine], "isql")
            else:
                isql = join(root_path_for[engine], "bin", "isql")

            database = join(root_folder, f"{driver}.{engine}.fdb")
            log(f"  Creating '{database}'...")

            if isfile(database):
                remove(database)

            # Sets FIREBIRD env var to avoid problems with 'firebird.msg' not found.
            fb_env = environ.copy()
            fb_env["FIREBIRD"] = root_path_for[engine]
            if engine == "fb25" and os_name != "nt":
                # Firebird 2.5 on Linux needs LD_LIBRARY_PATH set to './lib'
                #   https://groups.google.com/g/firebird-support/c/T6Nu6snaBWM/m/yLYqcJj0BAAJ
                fb_env["LD_LIBRARY_PATH"] = join(root_path_for[engine], "lib")

            create_sql = f"CREATE DATABASE '{database}' USER 'SYSDBA' PASSWORD 'masterkey' PAGE_SIZE 8192 DEFAULT CHARACTER SET UTF8;"
            cp = run(
                [isql, "-quiet"],
                capture_output=True,
                input=create_sql,
                text=True,
                env=fb_env
            )
            if cp.returncode != 0:
                raise Exception(cp.stderr)

            # Add [db] section to setup.cfg
            lib_key = "fb_library_name" if driver == "fdb" else "fb_client_library"

            if os_name == "nt":
                lib_value = join(root_path_for[engine], "fbclient.dll")
            else:
                lib_value = join(root_path_for[engine], "lib", "libfbclient.so")

            db_uri = f"firebird+{driver}://SYSDBA@/{database}?charset=UTF8&{lib_key}={lib_value}"

            if driver == "firebird" and engine == "fb50":
                # Set firebird_fb50 also as default
                config.set("db", "default", db_uri)

            config.set("db", db_key, db_uri)

    log(f"  Updating 'setup.cfg'...")
    with open("setup.cfg", "w") as f:
        config.write(f)

    log("Databases created.")

"""Native filesystem paths for deep SDK trees, without changing project references.

Use only at I/O boundaries. Callers must validate transaction ownership BEFORE
deletion; adding a Windows extended prefix grants no additional scope.
"""
import ntpath
import os
from pathlib import Path
import shutil


def extended_windows_path(value):
    path = ntpath.abspath(os.fspath(value))
    if path.startswith('\\\\?\\'):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + path


def filesystem_path(value):
    return Path(extended_windows_path(value)) if os.name == 'nt' else Path(value)


def copy_tree(source, destination, **kwargs):
    return shutil.copytree(str(filesystem_path(source)), str(filesystem_path(destination)), **kwargs)


def copy_file(source, destination):
    return shutil.copy2(str(filesystem_path(source)), str(filesystem_path(destination)))


def remove_tree(path):
    # Scope checks belong to the transaction caller, using normal logical paths.
    return shutil.rmtree(str(filesystem_path(path)))

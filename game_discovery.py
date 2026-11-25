"""Locate installed Breakpoint archives without depending on the purchase launcher."""
import json
import os
from pathlib import Path
import re
import winreg

from game_catalog import DEFAULT_GAME


def is_game_folder(folder):
    return folder is not None and all((folder / name).is_file() for name in
                                      ('GRB.exe', 'DataPC.forge', 'DataPC_patch_01.forge', 'oo2core_7_win64.dll'))


def registry_value(hive, path, name):
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            if isinstance(value, str) and value:
                return Path(os.path.expandvars(value))
    except OSError:
        return None


def steam_folders():
    steam = registry_value(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath')
    root = steam or DEFAULT_GAME.parents[2]
    yield root / 'steamapps/common/Ghost Recon Breakpoint'
    try:
        libraries = (root / 'steamapps/libraryfolders.vdf').read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return
    for location in re.findall(r'"path"\s+"([^"]+)"', libraries):
        library = Path(location.replace('\\\\', '\\'))
        yield library / 'steamapps/common/Ghost Recon Breakpoint'


def ubisoft_folders():
    for root in (r'SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs', r'SOFTWARE\Ubisoft\Launcher\Installs'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as installs:
                keys = [winreg.EnumKey(installs, i) for i in range(winreg.QueryInfoKey(installs)[0])]
        except OSError:
            continue
        for name in keys:
            path = registry_value(winreg.HKEY_LOCAL_MACHINE, root + '\\' + name, 'InstallDir')
            if path:
                yield path


def epic_folders():
    launcher = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'Epic/EpicGamesLauncher/Data/Manifests'
    for manifest in launcher.glob('*.item'):
        try:
            location = json.loads(manifest.read_text(encoding='utf-8-sig')).get('InstallLocation')
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(location, str) and location:
            yield Path(location)


def installed_games():
    program_files = [Path(os.environ[name]) for name in ('ProgramFiles', 'ProgramFiles(x86)') if name in os.environ]
    defaults = (folder for base in program_files
                for parent in ('Ubisoft/Ubisoft Game Launcher/games', 'Epic Games')
                for folder in (base / parent).glob('*') if folder.is_dir())
    candidates = (DEFAULT_GAME, *steam_folders(), *ubisoft_folders(), *epic_folders(), *defaults)
    found = {}
    for folder in candidates:
        if is_game_folder(folder):
            path = folder.resolve()
            found.setdefault(os.path.normcase(path), path)
    return list(found.values())

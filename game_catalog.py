"""Read the supported catalog database directly from a local Breakpoint installation."""
import ctypes
from pathlib import Path
import struct

from build_weapon_catalog import build

DEFAULT_GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Ghost Recon Breakpoint')
DATABASE = 0x104634F921
ENTRY = struct.Struct('<QQI')
BLOCK = struct.Struct('<QhBIi')
CHUNK = struct.Struct('<II')


def read_exact(file, size):
    data = file.read(size)
    if len(data) != size:
        raise ValueError('Truncated Forge file.')
    return data


def archive_entries(file):
    file_size = file.seek(0, 2)
    file.seek(0)
    if read_exact(file, 13) != b'scimitar\x00\x1b\x00\x00\x00':
        raise ValueError('Unsupported Forge header.')
    directory, = struct.unpack('<Q', read_exact(file, 8))
    file.seek(directory + 44)
    count, _, table = struct.unpack('<IIQ', read_exact(file, 16))
    if not 0 < count <= 100000 or table + count * ENTRY.size > file_size:
        raise ValueError('Invalid Forge directory.')
    file.seek(table)
    entries = {}
    for offset, resource, size in ENTRY.iter_unpack(read_exact(file, count * ENTRY.size)):
        if resource in entries or offset + size > file_size:
            raise ValueError('Invalid Forge entry.')
        entries[resource] = (offset, size)
    return entries


def oodle(path):
    decode = ctypes.CDLL(str(path)).OodleLZ_Decompress
    decode.restype = ctypes.c_int64
    decode.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64,
                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                       ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_int64, ctypes.c_int]
    return decode


def decode_block(file, decode, end):
    magic, version, algorithm, flags, count = BLOCK.unpack(read_exact(file, BLOCK.size))
    if (magic, version, algorithm, flags) != (0x1004FA9957FBAA33, 3, 3, 0x80008000) or not 0 < count <= 8192:
        raise ValueError('Unsupported catalog compression block.')
    sizes = list(CHUNK.iter_unpack(read_exact(file, count * CHUNK.size)))
    if any(not 0 < stored <= raw <= 32768 for raw, stored in sizes):
        raise ValueError('Invalid catalog chunk size.')
    if file.tell() + sum(4 + stored for _, stored in sizes) > end:
        raise ValueError('Compressed catalog exceeds the Forge entry.')
    output = bytearray()
    for raw, stored in sizes:
        read_exact(file, 4)
        packed = read_exact(file, stored)
        if raw == stored:
            output.extend(packed)
            continue
        unpacked = ctypes.create_string_buffer(raw)
        if decode(packed, stored, unpacked, raw, 0, 0, 0, None, 0, None, None, None, 0, 3) != raw:
            raise ValueError('Catalog decompression failed.')
        output.extend(unpacked.raw)
    return output


def read_database(path, decode):
    with path.open('rb') as file:
        offset, size = archive_entries(file)[DATABASE]
        if not BLOCK.size < size <= 160 * 1024 * 1024:
            raise ValueError('Unsupported database entry size.')
        file.seek(offset)
        return decode_block(file, decode, offset + size), decode_block(file, decode, offset + size)


def refresh(root, labels):
    root = root.resolve(strict=True)
    if not (root / 'GRB.exe').is_file():
        raise ValueError('Choose the Breakpoint folder containing GRB.exe.')
    stock = root / 'DataPC_patch_01.forge'
    mod_archives = sorted((root / 'mods').glob('*/DataPC_patch_*.forge'))
    inventory, database_overrides = [], []
    for path in mod_archives:
        with path.open('rb') as file:
            entries = archive_entries(file)
        contains_db = DATABASE in entries
        if contains_db:
            database_overrides.append(path)
        inventory.append(dict(path=path.relative_to(root).as_posix(), entries=len(entries), database=contains_db))
    if len(database_overrides) > 1:
        raise ValueError('Several mods override the catalog database. Their effective order is unverified.')
    source = database_overrides[0] if database_overrides else stock
    decode = oodle(root / 'oo2core_7_win64.dll')
    catalog = build(*read_database(source, decode))
    old_weapons = {w['id']:w for w in labels['weapons'].values()}
    for weapon in catalog['weapons'].values():
        old = old_weapons.get(weapon['id'], {})
        if old.get('record') == weapon['record'] and 'displayName' in old:
            weapon['displayName'] = old['displayName']
    catalog['attachmentLabels'] = {
        rid: labels['attachmentLabels'][rid] for rid, name in catalog['attachments'].items()
        if labels['attachments'].get(rid) == name and rid in labels.get('attachmentLabels', {})
    }
    stats = dict(addedWeapons=0, changedAttachmentLists=0, addedAttachments=0)
    if database_overrides:
        base = build(*read_database(stock, decode))
        base_weapons = {weapon['id']:weapon for weapon in base['weapons'].values()}
        stats = dict(
            addedWeapons=sum(w['id'] not in base_weapons for w in catalog['weapons'].values()),
            changedAttachmentLists=sum(w['id'] in base_weapons and w['groups'] != base_weapons[w['id']]['groups']
                                       for w in catalog['weapons'].values()),
            addedAttachments=len(catalog['attachments'].keys() - base['attachments'].keys()),
        )
    catalog.update(source=source.relative_to(root).as_posix(), modArchives=inventory, modChanges=stats)
    return catalog

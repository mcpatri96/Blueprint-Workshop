"""Build S0A weapon-variant packages using the game's own blueprint records."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
import zlib

from build_weapon_catalog import directory_records, weapon_groups, ALIASES
from game_catalog import DEFAULT_GAME, DATABASE, archive_entries, decode_block, oodle, read_database

MOD = 'blueprintworkshop.weapon-variants'
APP_NAME = 'Blueprint Workshop'
FORGE = 'DataPC_patch_01.forge'
NESTED = 'forge/' + FORGE + '/Extracted/1_-_DBContainerEntry_0X104634F921.data/'
KEYS = ('weapon', 'ui', 'settings', 'unit', 'store', 'recipe', 'unlock', 'blueprint_ui')
TYPES = ('GR_WeaponDBEntry', 'DBWeaponUIConfig', 'DBWeaponSmith_AttachmentPerWeaponSetting',
         'DBRecipeElementUnit_Weapon', 'StoreObjectInfo', 'DBShopTransactionRecipe', 'DBUnlockable',
         'DBUnlockableUIConfig')
ROOT_KEYS = ('weapon', 'ui', 'unit', 'recipe', 'unlock', 'blueprint_ui')
LINKS = {'weapon': ('ui',), 'settings': ('weapon',), 'unit': ('weapon',),
         'store': ('unlock',), 'recipe': ('unit',), 'unlock': ('recipe', 'blueprint_ui')}
U32 = struct.Struct('<I')
U64 = struct.Struct('<Q')
ENTRY = struct.Struct('<QIH')
BASE_ID = 0x4C494C4D34001001
BASE_TEXT = 0x7314A501
MAX_SLOTS = 512
M4A1 = 0x324A72D55A
LOADOUT, SMITH, STORE = 0x3AEDDAA7CB, 0x3E86FFEB19, 0x751C834EBF


class RegistryImportRequired(ValueError):
    """The installation has variant IDs that need their original saved list."""


def crc(value):
    return zlib.crc32(value.encode('ascii'))


def ids(slot):
    return dict(zip(KEYS, range(BASE_ID + 32 * slot, BASE_ID + 32 * slot + len(KEYS))))


def decode_refs(body, offset, prefix):
    count, = U32.unpack_from(body, offset)
    start = offset + U32.size
    size = len(prefix) + U64.size
    end = start + size * count
    if end > len(body):
        raise ValueError('The game list ends early.')
    rows = [body[pos:pos + size] for pos in range(start, end, size)]
    if any(row[:len(prefix)] != prefix for row in rows):
        raise ValueError('The game list has an unfamiliar reference type.')
    return rows, end


def update_refs(body, offset, prefix, remove, additions):
    rows, end = decode_refs(body, offset, prefix)
    rows = [row for row in rows if U64.unpack(row[len(prefix):])[0] not in remove]
    # Reverse insertion keeps the names in the order selected by the player.
    for donor, new in reversed(additions):
        anchor = prefix + U64.pack(donor)
        if rows.count(anchor) != 1:
            raise ValueError('The source weapon is missing from one of the game lists.')
        rows.insert(rows.index(anchor) + 1, prefix + U64.pack(new))
    return body[:offset] + U32.pack(len(rows)) + b''.join(rows) + body[end:]


def rename(body, text, text_key):
    # Verify that the first UIString is a counted name before replacing it.
    start = body.index(U32.pack(crc('UIString'))) + 4
    old_length, = U32.unpack_from(body, start + 4)
    end = start + 8 + old_length * 2
    if not 0 < old_length < 512 or body[end:end + 2] != bytes(2):
        raise ValueError('This blueprint uses an unfamiliar display-name record.')
    body[start + 8:end].decode('utf-16-le')
    name = text.encode('utf-16-le')
    return body[:start] + struct.pack('<II', text_key, len(name) // 2) + name + bytes(2) + body[end + 2:]


@dataclass(frozen=True)
class Weapon:
    label: str
    records: dict
    originals: dict
    book: int


class Game:
    def __init__(self, root):
        self.root = Path(root)
        decoder = oodle(self.root / 'oo2core_7_win64.dll')
        merged = self.root / 'mods/_merged/DataPC_patch_99.forge'
        archive = merged if merged.is_file() else self.root / FORGE
        directory, self.data = read_database(archive, decoder)
        self.records = directory_records(directory, self.data)
        self.index = {rid: index for index, (rid, _, _) in enumerate(ENTRY.iter_unpack(directory[2:]))}
        self.owner = False
        overlay = self.root / '.grb_addons/overlay.json'
        if overlay.is_file():
            self.owner = any(r.get('id') == MOD for r in json.loads(overlay.read_bytes())['items'])
        self.managed = self._recognized_variants()
        self.weapons = self._discover(decoder)

    def body(self, rid):
        _, _, start, end = self.records[rid]
        return bytes(self.data[start:end])

    def _recognized_variants(self):
        managed = set()
        for slot in range(MAX_SLOTS):
            for key, rid in ids(slot).items():
                if rid not in self.records:
                    continue
                name = self.records[rid][0]
                valid_name = name == f'ILWV{slot}_{key}' or (slot == 0 and name == 'IL_M4_Stealth_' + key)
                kind = crc(TYPES[KEYS.index(key)])
                if not valid_name or self.records[rid][1] != kind:
                    raise ValueError(f'Game record {rid:X} conflicts with the ID reserved for a weapon variant.')
                if not self.owner:
                    raise ValueError(
                        'This installation still has blueprints from an earlier Workshop package. '
                        'Keep your variants folder. With Breakpoint closed, uninstall the earlier package in S0A, '
                        'then reopen Blueprint Workshop to build the replacement package.'
                    )
                managed.add(rid)
        return managed

    def _discover(self, decoder):
        models = {}
        settings = defaultdict(list)
        shops = {}
        books = {}
        smith_refs, _ = decode_refs(self.body(SMITH), 13, bytes((1, 0)))
        active_settings = {U64.unpack_from(row, 2)[0] for row in smith_refs}
        for rid, (name, kind, _, _) in self.records.items():
            if kind == crc(TYPES[0]) and rid not in self.managed:
                models[rid] = name
            if kind == crc(TYPES[2]) and rid in active_settings and rid not in self.managed:
                body = self.body(rid)
                if len(body) == 146:
                    settings[U64.unpack_from(body, 15)[0]].append(rid)
            if kind == crc(TYPES[4]) and name.startswith('StoreObjectInfo_ShopTransactionRecipe'):
                shops[name.removeprefix('StoreObjectInfo_')] = rid
            if kind == crc('DBCraftRecipesBook') and name.startswith('DBTransactionRecipesBook_WeaponOnDemand'):
                refs, _ = decode_refs(self.body(rid), 13, bytes(1))
                books[rid] = [U64.unpack(row[1:])[0] for row in refs]
        self.books = list(books)
        store_refs, _ = decode_refs(self.body(STORE), 5514, bytes((1, 0)))
        store_list = {U64.unpack(row[2:])[0] for row in store_refs}
        store_anchor = shops['ShopTransactionRecipeWODASR_M4A1']
        visible, _ = decode_refs(self.body(LOADOUT), 30, bytes(1))
        visible = {U64.unpack(row[1:])[0] for row in visible}
        weapons = defaultdict(list)
        with (self.root / 'DataPC.forge').open('rb') as world:
            external = archive_entries(world)

            def external_page(rid, kind):
                off, size = external[rid]
                world.seek(off)
                header = decode_block(world, decoder, off + size)
                data = bytes(decode_block(world, decoder, off + size))
                name_size, = U32.unpack_from(data, 8)
                expected = struct.pack('<HQIH', 1, rid, len(data), 0)
                if header != expected or data[12 + name_size:13 + name_size] != bytes(1):
                    raise ValueError('An external blueprint has an unfamiliar header.')
                body = data[13 + name_size:]
                if not body.startswith(struct.pack('<QI', rid, crc(kind))):
                    raise ValueError('An external blueprint has an unfamiliar type.')
                return data[12:12 + name_size].decode('ascii'), body

            world.seek(13)
            directory_node, = U64.unpack(world.read(8))
            world.seek(directory_node + 76)
            attr_offset, = U64.unpack(world.read(8))
            world.seek(attr_offset)
            attributes = world.read(192 * len(external))
            if len(attributes) != 192 * len(external):
                raise ValueError('The game archive is missing its record attributes.')
            candidates = {recipe for recipes in books.values() for recipe in recipes}
            unlocks = defaultdict(list)
            for index, rid in enumerate(external):
                if U32.unpack_from(attributes, 192 * index + 16)[0] != crc(TYPES[6]):
                    continue
                try:
                    _, body = external_page(rid, TYPES[6])
                except (KeyError, ValueError, struct.error):
                    continue
                if len(body) == 69 and U64.unpack_from(body, 45)[0] in candidates:
                    unlocks[U64.unpack_from(body, 45)[0]].append((rid, body))

            for book, recipes in books.items():
                for recipe in recipes:
                    if recipe not in external:
                        continue
                    try:
                        recipe_name, recipe_body = external_page(recipe, TYPES[5])
                        if len(recipe_body) != 104 or U32.unpack_from(recipe_body, 80)[0] != 1:
                            continue
                        unit, = U64.unpack_from(recipe_body, 72)
                        unit_body = self.body(unit)
                        # Unique master plans carry additional quality and weapon bonuses
                        # after the same model reference. Copy the entire recipe unit.
                        if len(unit_body) not in (32, 82, 91, 100, 109) or self.records[unit][1] != crc(TYPES[3]):
                            continue
                        model, = U64.unpack_from(unit_body, 23)
                        if model not in models or model not in visible or len(settings[model]) != 1:
                            continue
                        store = shops.get(recipe_name.removeprefix('DB').removesuffix(' copy'))
                        if store not in store_list:
                            store = store_anchor
                        store_body = self.body(store)
                        template_unlock, = U64.unpack_from(store_body, len(store_body) - 28)
                        matches = unlocks[recipe]
                        exact = [pair for pair in matches if pair[0] == template_unlock]
                        if len(exact or matches) != 1:
                            continue
                        unlock, unlock_body = (exact or matches)[0]
                        if store_body.count(U64.pack(template_unlock)) != 1:
                            continue
                        store_body = store_body.replace(U64.pack(template_unlock), U64.pack(unlock))
                        blueprint_ui, = U64.unpack_from(unlock_body, 16)
                        _, blueprint_text = external_page(blueprint_ui, TYPES[7])
                        model_body = self.body(model)
                        weapon_ui = self._linked_ui(model_body)
                        originals = dict(zip(KEYS, (model_body, self.body(weapon_ui), self.body(settings[model][0]),
                                                   unit_body, store_body, recipe_body, unlock_body, blueprint_text)))
                        original_ids = {key: U64.unpack_from(body)[0] for key, body in originals.items()}
                        if any(originals[src].count(U64.pack(original_ids[dst])) != 1
                               for src, links in LINKS.items() for dst in links):
                            continue
                        if not weapon_groups(model_body, self.records):
                            continue
                        body = originals['ui']
                        start = body.index(U32.pack(crc('UIString'))) + 4
                        units, = U32.unpack_from(body, start + 4)
                        name = (body[start + 8:start + 8 + 2 * units].decode('utf-16-le')
                                if 0 < units < 120 else models[model])
                        label = ALIASES.get(models[model], name or models[model])
                        weapons[model].append(Weapon(label, original_ids, originals, book))
                    except (KeyError, ValueError, struct.error, UnicodeError):
                        # Only expose weapons that matched the complete observed registration path.
                        continue
        return {rid: entries[0] for rid, entries in weapons.items() if len(entries) == 1}

    def _linked_ui(self, body):
        candidates = set()
        for offset in range(12, len(body) - U64.size + 1):
            key, = U64.unpack_from(body, offset)
            if key in self.records and self.records[key][1] == crc(TYPES[1]):
                candidates.add(key)
        if len(candidates) != 1:
            raise ValueError('The weapon does not identify one display record.')
        return candidates.pop()

    def assert_available(self, roster):
        requested = {rid for row in roster for rid in ids(row['slot']).values()}
        if (requested - self.managed) & self.records.keys():
            raise ValueError('A new ID overlaps an installed weapon record.')
        for path in [*self.root.glob('DataPC*.forge'), *(self.root/'mods').glob('*/*.forge')]:
            with path.open('rb') as file:
                if file.read(13) != b'scimitar\0\x1b\0\0\0':
                    raise ValueError('Unsupported game archive: ' + path.name)
                file.seek(U64.unpack(file.read(8))[0] + 44)
                count, _, table = struct.unpack('<IIQ', file.read(16))
                if not 0 < count <= 1000000 or table + 20 * count > path.stat().st_size:
                    raise ValueError('Invalid archive directory: ' + path.name)
                file.seek(table)
                entries = struct.iter_unpack('<QQI', file.read(20 * count))
                if any(rid in requested for _, rid, _ in entries):
                    raise ValueError('A variant ID is already in use in ' + path.name)

    def build(self, registry, output):
        roster = registry['variants']
        self.assert_available(roster)
        additions = defaultdict(list)
        parents = {LOADOUT: (30, bytes(1), 'weapon'), SMITH: (13, bytes((1, 0)), 'settings'),
                   STORE: (5514, bytes((1, 0)), 'store')}
        parents.update({rid: (13, bytes(1), 'recipe') for rid in self.books})
        files = {}
        root_refs = []
        text_ops = []
        for row in roster:
            slot, name = row['slot'], row['name']
            weapon = self.weapons.get(row['weapon'])
            if not weapon:
                raise ValueError(f'{name} uses a weapon without a supported blueprint. Restore its mod or remove the entry.')
            targets = ids(slot)
            text_key = BASE_TEXT + slot
            for ordinal, (key, kind) in enumerate(zip(KEYS, TYPES)):
                body = U64.pack(targets[key]) + weapon.originals[key][8:]
                for target in LINKS.get(key, ()):
                    old = U64.pack(weapon.records[target])
                    if body.count(old) != 1:
                        raise ValueError(f'{name} has an ambiguous game record.')
                    body = body.replace(old, U64.pack(targets[target]))
                if key in ('ui', 'store', 'blueprint_ui'):
                    body = rename(body, name, text_key)
                label = f'ILWV{slot}_{key}'
                path = NESTED + f'{90001 + slot * len(KEYS) + ordinal}_-_{label}.{kind}'
                files[path] = bytes(1) + body
            for parent in (LOADOUT, SMITH, STORE, weapon.book):
                key = parents[parent][2]
                additions[parent].append((weapon.records[key], targets[key]))
            root_refs.extend({'id': f'{targets[key]:016X}'} for key in ROOT_KEYS)
            text_ops.append(dict(op='string_add', item=name, forge=FORGE, entry='000000A67D037EDA',
                                 key=f'{text_key:08X}', text=name))
        for parent, (offset, prefix, _) in parents.items():
            original = self.body(parent)
            body = update_refs(original, offset, prefix, self.managed, additions[parent])
            if body != original or additions[parent]:
                label, kind, _, _ = self.records[parent]
                type_name = {crc(x): x for x in ('LoadoutDBEntry', 'DBWeaponSmith', 'DBCraftRecipesBook', 'StoreDBEntry')}[kind]
                files[NESTED + f'{self.index[parent]}_-_{label}.{type_name}'] = bytes(1) + body
        operations = text_ops
        if root_refs:
            operations = [dict(op='list_add', item='Weapon Variants', forge=FORGE, entry=f'{DATABASE:016X}',
                               record=f'{DATABASE:016X}', list='root_refs', at={'after': f'{M4A1:016X}'},
                               items=root_refs), *text_ops]
        manifest = dict(formatVersion=1, id=MOD, name=APP_NAME, version=f"0.2.{registry['revision']}",
                        author='Blueprint Workshop', description='Independent weapon blueprints.',
                        records='records/records.json')
        files.update({'grbmod.json': json_bytes(manifest),
                      'records/records.json': json_bytes(dict(formatVersion=1, owner=MOD, ops=operations))})
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as temporary:
            temp_path = Path(temporary.name)
        try:
            with ZipFile(temp_path, 'w', ZIP_DEFLATED) as archive:
                for path, data in files.items():
                    archive.writestr(path, data)
            with ZipFile(temp_path) as archive:
                if archive.testzip() is not None or any(archive.read(path) != data for path, data in files.items()):
                    raise ValueError('The generated package did not pass readback.')
            os.replace(temp_path, output)
        finally:
            temp_path.unlink(missing_ok=True)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def load_registry(path, game):
    if path.exists():
        state = json.loads(path.read_bytes())
        if not isinstance(state, dict) or state.get('schema') != 1:
            raise ValueError('The saved variant list uses an unsupported format.')
        rows, next_slot, revision = state.get('variants'), state.get('next_slot'), state.get('revision')
        if (not isinstance(rows, list) or type(next_slot) is not int or not 0 <= next_slot <= MAX_SLOTS
                or type(revision) is not int or revision < 0):
            raise ValueError('The saved variant list is incomplete or invalid.')
        if any(not isinstance(row, dict) or type(row.get('slot')) is not int
               or type(row.get('weapon')) is not int or not isinstance(row.get('name'), str) for row in rows):
            raise ValueError('The saved variant list contains an invalid entry.')
        slots = [row['slot'] for row in rows]
        if len(slots) != len(set(slots)) or any(not 0 <= slot < next_slot for slot in slots):
            raise ValueError('The saved variant list has invalid IDs.')
        used = {slot for slot in range(MAX_SLOTS) if ids(slot)['weapon'] in game.managed}
        if used and max(used) >= state['next_slot']:
            raise RegistryImportRequired('The game has variants newer than this saved list.')
        return state
    if game.managed:
        raise RegistryImportRequired('This installation already contains Workshop variants.')
    return dict(schema=1, revision=0, next_slot=1, variants=[])


def check_name(name, registry, current=None):
    name = name.strip()
    if not name or len(name.encode('utf-16-le')) > 120 or re.search(r'[\x00-\x1f\x7f]', name):
        raise ValueError('Use a display name of 1 to 60 characters.')
    if any(row['slot'] != current and row['name'].casefold() == name.casefold() for row in registry['variants']):
        raise ValueError('That display name is already used.')
    return name


def save_registry(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.json', delete=False) as temporary:
        temporary.write(json_bytes(state))
        temp = Path(temporary.name)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path)
    parser.add_argument('--list', action='store_true')
    options = parser.parse_args()
    if options.list:
        game = Game(options.game or DEFAULT_GAME)
        for rid, weapon in sorted(game.weapons.items(), key=lambda pair: pair[1].label.casefold()):
            print(f'{rid:X}  {weapon.label}'.encode('ascii', 'backslashreplace').decode())
    else:
        from variant_tool import show
        show(options.game)


if __name__ == '__main__':
    main()

"""Build menu choices from decoded GRB database directory and payload copies."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

WEAPON = 0x530ACADB
VISUAL_PRESET = 0xc18c0c8e
ENTRY = struct.Struct('<QIH')
MARKER = bytes.fromhex('0000f800000000e1722146')
TYPES = {0xb7358965:'Barrel', 0x801dedc8:'Magazine', 0xaa0ad158:'Muzzle',
         0x015aa4e7:'Rail', 0xddba55bd:'Optic', 0x56806e23:'Stock',
         0xd6347060:'Trigger', 0x1ec2caa2:'Underbarrel'}
ALIASES = {'ASR_M4A1':'M4A1', 'ASR_M4A1_SCT':'M4A1 Scout', 'SNR_TAC50':'TAC50',
           'YE1E2_HDG_MK23':'MK23', 'HDG_P320':'P320', 'SMG_UMP':'UMP',
           'SNR_L115A3':'L115A3', 'HDG_M1911':'1911', 'SNR_MSR':'MSR', 'HDG_F40':'F40',
           'Unique_ASR_Omen':'Silver Stake', 'Unique_ASR_Omen_Tactical':'Silver Stake Tactical',
           'SNR_Victrix_SCT':'Scorpio Scout', 'HDG_HS2000':'C-SFP', 'Y1E3_SMG_AAC':'Honey Badger'}


def directory_records(directory, payload):
    count, = struct.unpack_from('<H', directory)
    if len(directory) != 2 + count * ENTRY.size:
        raise ValueError('Unrecognized database directory')
    records, seen = {}, set()
    offset = 0
    for rid, size, _ in ENTRY.iter_unpack(directory[2:]):
        end = offset + size
        if end > len(payload) or size < 12 or rid in seen:
            raise ValueError('Invalid record directory')
        seen.add(rid)
        kind, length, namesize = struct.unpack_from('<III', payload, offset)
        start = offset + 13 + namesize
        if (namesize <= 512 and length >= 12 and start+length == end
                and payload[start-1] == 0 and struct.unpack_from('<QI', payload, start) == (rid, kind)):
            name = payload[offset+12:start-1].decode('utf8', errors='replace')
            records[rid] = (name, kind, start, end)
        offset = end
    if offset != len(payload):
        raise ValueError('Payload length mismatch')
    return records


def weapon_groups(body, records):
    groups = {}
    marker = body.find(MARKER)
    while marker >= 0:
        pos = marker + len(MARKER)
        marker = body.find(MARKER, pos)
        if pos + 4 > len(body):
            continue
        count, = struct.unpack_from('<I', body, pos)
        if not 1 <= count <= 128 or pos+4+9*count > len(body):
            continue
        refs = list(struct.iter_unpack('<BQ', body[pos+4:pos+4+9*count]))
        if any(flag != 0 or rid not in records for flag, rid in refs):
            continue
        kinds = {records[rid][1] for _, rid in refs}
        if len(kinds) == 1 and next(iter(kinds)) in TYPES:
            part = TYPES[next(iter(kinds))]
            if part in groups:
                raise ValueError('Ambiguous duplicate attachment group')
            groups[part] = [str(rid) for _,rid in refs]
    return groups


def localized_label(rid, target_type, records, payload, strings):
    _, _, start, end = records[rid]
    targets = set()
    for offset in range(start+12, end-7):
        ref, = struct.unpack_from('<Q', payload, offset)
        if ref in records and records[ref][1] == target_type:
            targets.add(ref)
    if len(targets) != 1:
        return None
    _, _, start, end = records[targets.pop()]
    if end-start < 29 or payload[start+12:start+25] != bytes.fromhex('01000000f8000000005d04a781'):
        return None
    key, = struct.unpack_from('<I', payload, start+25)
    value = ' '.join(strings.get(str(key), '').split())
    return value if value and len(value) <= 240 and not value.startswith('!!') else None


def build(directory, payload, strings=None):
    records = directory_records(directory, payload)
    weapons = {}
    for rid, (name, kind, start, end) in records.items():
        if kind != WEAPON:
            continue
        groups = weapon_groups(payload[start:end], records)
        if groups:
            label = ALIASES.get(name, name)
            if label in weapons:
                raise ValueError('Ambiguous weapon name: '+label)
            weapons[label] = dict(record=name, id=str(rid), groups=groups)
    used = {rid for w in weapons.values() for ids in w['groups'].values() for rid in ids}
    paints = {n.split('_', 3)[3].replace('_', ' ') for n,k,_,_ in records.values()
              if k == 0x36c9cb38 and n.startswith('StoreObjectInfo_WeaponPaint_PNT') and n.count('_') >= 3}
    result = dict(source='Installed _merged/DataPC_patch_99.forge', payloadSha256=hashlib.sha256(payload).hexdigest(),
                weapons=weapons, attachments={rid:records[int(rid)][0] for rid in sorted(used)},
                finishPresets={str(rid):name for rid,(name,kind,_,_) in records.items() if kind==VISUAL_PRESET and name},
                camouflages=sorted({n[7:] for n,k,_,_ in records.values() if k==0xaeb1d0c4 and n.startswith('W_CAMO_')}),
                paints=sorted(paints, key=str.casefold))
    if strings:
        result['attachmentLabels'] = {rid:text for rid in sorted(used)
                                      if (text := localized_label(int(rid), 0x020be7e2, records, payload, strings))}
        for info in weapons.values():
            text = localized_label(int(info['id']), 0x72ba026a, records, payload, strings)
            if text:
                info['displayName'] = text
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('directory','payload','output'):
        parser.add_argument(arg, type=Path)
    parser.add_argument('--english', type=Path, nargs='+', help='Decoded string ID to English text maps')
    args = parser.parse_args()
    strings, conflicts = {}, set()
    for path in args.english or ():
        for key, value in json.loads(path.read_text(encoding='utf-8-sig')).items():
            if key in strings and strings[key] != value:
                conflicts.add(key)
            strings[key] = value
    for key in conflicts:
        del strings[key]
    catalog = build(args.directory.read_bytes(), args.payload.read_bytes(), strings)
    args.output.write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding='utf8')
    print(len(catalog['weapons']), 'weapon records;', len(catalog['attachments']), 'referenced attachments;',
          len(catalog['camouflages']), 'global camouflage labels')

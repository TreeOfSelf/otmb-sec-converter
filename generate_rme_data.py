#!/usr/bin/env python3
"""
Generate RME data/ folder from CipSoft objects.srv for Tibia 7.7

Creates:
  - RME/data/770/items.otb (binary item database)
  - RME/data/770/items.xml (metadata overrides)
"""

import struct
from pathlib import Path


def parse_objects_srv(objects_srv_path):
    """Parse objects.srv and extract TypeID, Name, Flags, Attributes"""
    items = {}
    
    with open(objects_srv_path, 'r', encoding='latin-1', errors='ignore') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Look for TypeID line
        if line.startswith('TypeID') and '=' in line:
            try:
                type_id = int(line.split('=')[1].split('#')[0].strip())
            except ValueError:
                i += 1
                continue
            
            # Parse item block (next ~20 lines)
            item_data = {'type_id': type_id, 'name': '', 'flags': [], 'attributes': {}}
            
            for j in range(i+1, min(i+30, len(lines))):
                item_line = lines[j].strip()
                
                # Stop at next TypeID
                if item_line.startswith('TypeID'):
                    break
                
                # Parse Name
                if item_line.startswith('Name') and '=' in item_line:
                    name = item_line.split('=', 1)[1].strip().strip('"')
                    item_data['name'] = name
                
                # Parse Flags
                if item_line.startswith('Flags') and '=' in item_line:
                    flags_str = item_line.split('=', 1)[1].strip()
                    if '{' in flags_str and '}' in flags_str:
                        flags_str = flags_str.split('{')[1].split('}')[0]
                        item_data['flags'] = [f.strip() for f in flags_str.split(',') if f.strip()]
                
                # Parse Attributes (Weight, Capacity, etc.)
                if item_line.startswith('Attributes') and '=' in item_line:
                    attrs_str = item_line.split('=', 1)[1].strip()
                    if '{' in attrs_str and '}' in attrs_str:
                        attrs_str = attrs_str.split('{')[1].split('}')[0]
                        for attr in attrs_str.split(','):
                            if '=' in attr:
                                key, val = attr.split('=', 1)
                                item_data['attributes'][key.strip()] = val.strip()
            
            items[type_id] = item_data
        
        i += 1
    
    print(f"Parsed {len(items)} items from objects.srv")
    return items


def write_otb_byte(data, b):
    """Write byte with escape handling"""
    ESC = 0xFD
    INIT = 0xFE
    TERM = 0xFF
    
    if b in (ESC, INIT, TERM):
        data.append(ESC)
    data.append(b)


def write_otb_u16(data, val):
    """Write uint16 LE with escape"""
    write_otb_byte(data, val & 0xFF)
    write_otb_byte(data, (val >> 8) & 0xFF)


def write_otb_u32(data, val):
    """Write uint32 LE with escape"""
    write_otb_byte(data, val & 0xFF)
    write_otb_byte(data, (val >> 8) & 0xFF)
    write_otb_byte(data, (val >> 16) & 0xFF)
    write_otb_byte(data, (val >> 24) & 0xFF)


def write_otb_string(data, s):
    """Write string with length prefix"""
    encoded = s.encode('latin-1')
    write_otb_u16(data, len(encoded))
    for b in encoded:
        write_otb_byte(data, b)


def generate_items_otb(items, output_path):
    """Generate items.otb binary file from parsed objects.srv"""
    data = bytearray()
    
    # Header layout matching the 7.60-era OTB files
    # (this is the layout that passes version checks in the target RME build)
    data.extend([
        0x00, 0x00, 0x00, 0x00,  # Magic
        0xFE, 0x00,              # Root node start + type
        0x00,                    # Type byte
        0x00, 0x00, 0x00, 0x01,  # Flags/marker block used by this RME parser
        0x8C, 0x00,              # Version data length (140 bytes)
    ])
    
    # Bytes 13-152: Version data (4+4+4+128 = 140 bytes)
    # Major version (OTB format version 1, matching official 7.70)
    data.extend([0x01, 0x00, 0x00, 0x00])
    # Minor version (OTB ID = 100 = 0x64)
    data.extend([0x64, 0x00, 0x00, 0x00])
    # Build number (1)
    data.extend([0x01, 0x00, 0x00, 0x00])
    
    # CSD Version string (128 bytes, null-padded)
    version_str = "OTB 1.0.0-7.70-cipsoft"
    version_bytes = version_str.encode('latin-1')
    data.extend(version_bytes)
    # Pad to 128 bytes
    data.extend([0x00] * (128 - len(version_bytes)))
    
    # Write items (only include items with non-empty names - IDs 0-99 are containers/internal)
    for type_id in sorted(items.keys()):
        item = items[type_id]
        
        # Skip items with empty names (internal server containers, etc.)
        if not item['name']:
            continue
        
        # Item node
        # IMPORTANT: In this node format, the byte after NODE_START (0xFE) is the
        # item "node type", and RME's loader reads that same byte as item group.
        # So we must write FE + <group> directly (no extra type byte).
        data.append(0xFE)  # Node start
        
        # Item group/type (this becomes the node type byte)
        # Based on RME items.h enum ItemGroup_t:
        # 0=NONE, 1=GROUND, 2=CONTAINER, 3=WEAPON, 4=AMMUNITION, 5=ARMOR, 6=RUNE, 7=TELEPORT,
        # 8=MAGICFIELD, 9=WRITEABLE, 10=KEY, 11=SPLASH, 12=FLUID, 13=DOOR
        flags = item['flags']
        if 'Bank' in flags:  # Bank = ground layer item in CipSoft objects.srv
            item_group = 0x01  # ITEM_GROUP_GROUND
        elif 'Container' in flags:
            item_group = 0x02  # ITEM_GROUP_CONTAINER
        elif 'Splash' in flags or 'FluidContainer' in flags or 'LiquidContainer' in flags:
            item_group = 0x0B  # ITEM_GROUP_SPLASH
        elif 'Rune' in flags:
            item_group = 0x06  # ITEM_GROUP_RUNE
        else:
            item_group = 0x00  # ITEM_GROUP_NONE (normal item)
        
        data.append(item_group)  # Node type / item group byte

        # Flags (u32)
        # RME's OTB v1 loader (`loadFromOtbVer1`) will always attempt to read a u32 flags block
        # right after the group byte. If we don't include it, the loader will consume our
        # first attribute bytes as flags and desync the entire file.
        #
        # We keep this conservative (0) for now; attributes are what we strictly need for RME
        # to map serverid/clientid/name correctly.
        write_otb_u32(data, 0)
        
        # Attributes
        # Format: [attribute_type] [u16 data_length] [data...]
        # NOTE: Attribute TYPE bytes are NOT escaped, but length and data ARE escaped
        
        # 0x10: Server ID
        data.append(0x10)  # Attribute type (raw)
        write_otb_u16(data, 2)  # Data length = 2 bytes
        write_otb_u16(data, type_id)  # Server ID
        
        # 0x11: Client ID
        data.append(0x11)  # Attribute type (raw)
        write_otb_u16(data, 2)  # Data length = 2 bytes
        write_otb_u16(data, type_id)  # Client ID
        
        # 0x12: Name
        if item['name']:
            name_bytes = item['name'].encode('latin-1')
            data.append(0x12)  # Attribute type (raw)
            write_otb_u16(data, len(name_bytes))  # Data length
            # Write name bytes with node-file escaping (length is the *unescaped* length)
            for b in name_bytes:
                write_otb_byte(data, b)
        
        # 0x14: Speed (for ground items)
        if item_group == 0x01:  # ITEM_GROUP_GROUND
            data.append(0x14)  # Attribute type (raw)
            write_otb_u16(data, 2)  # Data length = 2 bytes
            write_otb_u16(data, 150)  # Speed value
        
        # End item node (raw 0xFF)
        data.append(0xFF)
    
    # End root node
    data.append(0xFF)
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(data)
    
    print(f"Generated {output_path} ({len(data)} bytes, {len(items)} items)")


def generate_items_xml(items, output_path):
    """Generate items.xml with ALL items + RME-specific metadata"""
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<items>']
    
    doors = []
    depots = []
    special = []
    
    for type_id in sorted(items.keys()):
        item = items[type_id]
        flags = item['flags']
        name = item['name']
        attrs_dict = item['attributes']
        
        # Skip ID 0 - RME rejects it in items.xml (line 1047: if fromId == 0...)
        if type_id == 0:
            continue
        
        # Parse article
        article = None
        name_clean = name
        if name.startswith('a '):
            article = 'a'
            name_clean = name[2:]
        elif name.startswith('an '):
            article = 'an'
            name_clean = name[3:]
        
        # Detect doors
        is_door = ('KeyDoor' in flags or 'NameDoor' in flags or 
                   ('ChangeUse' in flags and 'door' in name.lower()))
        
        # Detect depots
        is_depot = 'locker' in name.lower()
        
        # Collect attributes to determine if item needs opening/closing tags
        item_attrs = []
        
        # Door attributes
        if is_door:
            item_attrs.append(('type', 'door'))
            if 'Unthrow' in flags or 'Unlay' in flags:
                item_attrs.append(('blockprojectile', '1'))
            doors.append(type_id)
        
        # Depot attributes
        elif is_depot:
            capacity = attrs_dict.get('Capacity', '30')
            item_attrs.append(('type', 'depot'))
            item_attrs.append(('containerSize', capacity))
            depots.append(type_id)
        
        # Container attributes
        if 'Container' in flags and 'Capacity' in attrs_dict:
            item_attrs.append(('containerSize', attrs_dict['Capacity']))
        
        # Weight
        if 'Weight' in attrs_dict:
            item_attrs.append(('weight', attrs_dict['Weight']))
        
        # Build item entry (single line if no attributes, multi-line if has attributes)
        if article:
            base_attrs = f'id="{type_id}" article="{article}" name="{name_clean}"'
        else:
            base_attrs = f'id="{type_id}" name="{name_clean}"'
        
        if item_attrs:
            # Multi-line item with attributes
            xml_lines.append(f'\t<item {base_attrs}>')
            for key, value in item_attrs:
                xml_lines.append(f'\t\t<attribute key="{key}" value="{value}"/>')
            xml_lines.append('\t</item>')
            special.append(type_id)
        else:
            # Single-line item with no attributes
            xml_lines.append(f'\t<item {base_attrs}/>')
    
    xml_lines.append('</items>')
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    print(f"Generated {output_path} ({len(items)} items: {len(doors)} doors, {len(depots)} depots)")


def get_dat_spr_signatures():
    """Extract signatures from Tibia.dat/Tibia.spr"""
    import struct
    
    try:
        dat = Path('assets/Tibia.dat').read_bytes()
        spr = Path('assets/Tibia.spr').read_bytes()
        
        dat_sig = struct.unpack('<I', dat[0:4])[0]
        spr_sig = struct.unpack('<I', spr[0:4])[0]
        
        return f'0x{dat_sig:08X}', f'0x{spr_sig:08X}'
    except:
        return '0x439D5A33', '0x439852BE'  # Fallback


def generate_materials_xml(output_path):
    """Generate minimal materials.xml for RME"""
    xml_content = '''<materials>
	<!-- Metaitems for 7.70 CipSoft -->
	<metaitem id="80"/>
	<metaitem id="81"/>
	<metaitem id="82"/>
	<metaitem id="83"/>
	<metaitem id="84"/>
	<metaitem id="85"/>
	<metaitem id="86"/>
	<metaitem id="87"/>
	<metaitem id="88"/>
	<metaitem id="89"/>
</materials>
'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(xml_content)
    print(f"Generated {output_path}")


def parse_mon_files(mon_dir):
    """Parse all .mon files to extract creature data"""
    import re
    
    creatures = []
    mon_files = sorted(Path(mon_dir).glob('*.mon'))
    
    for mon_file in mon_files:
        try:
            content = mon_file.read_text(encoding='latin-1', errors='ignore')
            
            race_num = None
            name = None
            outfit = None
            
            for line in content.split('\n'):
                line = line.strip()
                
                if line.startswith('RaceNumber'):
                    match = re.search(r'=\s*(\d+)', line)
                    if match:
                        race_num = int(match.group(1))
                
                elif line.startswith('Name'):
                    match = re.search(r'=\s*"([^"]+)"', line)
                    if match:
                        name = match.group(1)
                
                elif line.startswith('Outfit'):
                    match = re.search(r'\((\d+),\s*(\d+)-(\d+)-(\d+)-(\d+)\)', line)
                    if match:
                        outfit = {
                            'looktype': int(match.group(1)),
                            'head': int(match.group(2)),
                            'body': int(match.group(3)),
                            'legs': int(match.group(4)),
                            'feet': int(match.group(5))
                        }
            
            if race_num and name and outfit:
                creatures.append({
                    'race': race_num,
                    'name': name,
                    'outfit': outfit
                })
        
        except Exception:
            continue
    
    return creatures


def generate_creatures_xml(mon_dir, output_path):
    """Generate creatures.xml from .mon files"""
    creatures = parse_mon_files(mon_dir)
    
    # Sort by name
    creatures.sort(key=lambda c: c['name'].lower())
    
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<creatures>',
        '\t<!-- this file is for indexing only -->',
        '\t<!-- for sorting see creature_palette.xml -->'
    ]
    
    for creature in creatures:
        name = creature['name']
        looktype = creature['outfit']['looktype']
        head = creature['outfit']['head']
        body = creature['outfit']['body']
        legs = creature['outfit']['legs']
        feet = creature['outfit']['feet']
        
        # If all colors are 0, don't include them
        if head == 0 and body == 0 and legs == 0 and feet == 0:
            xml_lines.append(f'\t<creature name="{name}" type="monster" looktype="{looktype}"/>')
        else:
            xml_lines.append(
                f'\t<creature name="{name}" type="monster" '
                f'looktype="{looktype}" lookhead="{head}" lookbody="{body}" '
                f'looklegs="{legs}" lookfeet="{feet}"/>'
            )
    
    xml_lines.append('</creatures>')
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    print(f"Generated {output_path} ({len(creatures)} creatures)")


def generate_clients_xml_snippet(output_dir):
    """Generate clients.xml snippet to manually add to RME"""
    dat_sig, spr_sig = get_dat_spr_signatures()
    
    snippet = f'''<!-- Add this to RME/data/clients.xml -->

<!-- In the <otbs> section, add: -->
<otb client="7.70-cipsoft" version="1" id="100"/>

<!-- In the <clients> section, add: -->
<client name="7.70 (CipSoft)" otb="7.70-cipsoft" visible="true" data_directory="770">
    <otbm version="1"/>
    <extensions from="7.6" to="7.6"/>
    <data format="7.55" dat="{dat_sig}" spr="{spr_sig}"/>
</client>
'''
    
    snippet_path = output_dir / 'clients_xml_snippet.txt'
    with open(snippet_path, 'w') as f:
        f.write(snippet)
    
    print(f"Generated {snippet_path}")


def main():
    # Paths
    objects_srv = Path('assets/objects.srv')
    mon_dir = Path('assets/mon')
    output_base = Path('output/rme_config')
    data_dir = output_base / 'data' / '770'
    
    if not objects_srv.exists():
        print(f"Error: {objects_srv} not found")
        return
    
    print(f"Generating RME config from {objects_srv}...")
    print()
    
    # Parse objects.srv
    items = parse_objects_srv(objects_srv)
    
    # Generate files
    generate_items_otb(items, data_dir / 'items.otb')
    generate_items_xml(items, data_dir / 'items.xml')
    generate_materials_xml(data_dir / 'materials.xml')
    
    # Generate creatures.xml if .mon files exist
    if mon_dir.exists():
        generate_creatures_xml(mon_dir, data_dir / 'creatures.xml')
    
    generate_clients_xml_snippet(output_base)
    
    print()
    print(f"✅ Generated RME config in {output_base}/")
    print()
    print("📋 Manual steps:")
    print(f"   1. Copy {data_dir}/ → RME/data/770/")
    print(f"   2. Add snippet from clients_xml_snippet.txt to RME/data/clients.xml")
    print(f"   3. Update sec_to_otbm.py to write OTB ID 100 in header")
    print(f"   4. Copy assets/Tibia.dat + Tibia.spr to RME client data path")


if __name__ == '__main__':
    main()

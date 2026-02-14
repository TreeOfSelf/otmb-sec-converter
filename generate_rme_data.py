#!/usr/bin/env python3
"""
Complete RME data generator for 7.70 CipSoft TypeIDs
Generates all necessary files from CipSoft game data + 760 reference XMLs
"""
import os
import struct
from pathlib import Path
from lxml import etree


# ============================================================================
# OTB/OTBM Constants
# ============================================================================
ITEM_ATTR_SERVERID = 0x10
ITEM_ATTR_CLIENTID = 0x11
ITEM_ATTR_NAME = 0x12
ITEM_ATTR_SPEED = 0x14

ITEM_GROUP_NONE = 0x00
ITEM_GROUP_GROUND = 0x01
ITEM_GROUP_CONTAINER = 0x02
ITEM_GROUP_WEAPON = 0x03
ITEM_GROUP_AMMUNITION = 0x04
ITEM_GROUP_ARMOR = 0x05
ITEM_GROUP_RUNE = 0x06
ITEM_GROUP_TELEPORT = 0x07
ITEM_GROUP_MAGICFIELD = 0x08
ITEM_GROUP_WRITEABLE = 0x09
ITEM_GROUP_KEY = 0x0A
ITEM_GROUP_SPLASH = 0x0B
ITEM_GROUP_FLUID = 0x0C
ITEM_GROUP_DOOR = 0x0D
ITEM_GROUP_DEPRECATED = 0x0E

ESC_CHAR = 0xFD
NODE_START = 0xFE
NODE_END = 0xFF


# ============================================================================
# Step 1: Parse objects.srv for 770 items
# ============================================================================
def parse_objects_srv(objects_srv_path):
    """Parse CipSoft objects.srv to extract item definitions"""
    items = {}
    
    with open(objects_srv_path, 'r', encoding='latin-1', errors='ignore') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        if line.startswith('TypeID') and '=' in line:
            try:
                type_id = int(line.split('=')[1].split('#')[0].strip())
            except:
                i += 1
                continue
            
            name = ""
            flags = []
            
            # Read next lines for Name and Flags
            i += 1
            while i < len(lines):
                line = lines[i].strip()
                
                if line.startswith('TypeID'):
                    i -= 1
                    break
                
                if line.startswith('Name') and '=' in line:
                    name_part = line.split('=', 1)[1].strip()
                    name = name_part.strip('"')
                elif line.startswith('Flags') and '=' in line:
                    flags_str = line.split('=', 1)[1].strip()
                    flags_str = flags_str.strip('{}')
                    flags = [f.strip() for f in flags_str.split(',') if f.strip()]
                
                i += 1
                if not line or line == '':
                    break
            
            items[type_id] = {
                'type_id': type_id,
                'name': name,
                'flags': flags
            }
        else:
            i += 1
    
    return items


# ============================================================================
# Step 3: Generate items.otb with proper binary format
# ============================================================================
def escape_otb_data(data):
    """Escape special bytes in OTB data"""
    result = bytearray()
    for byte in data:
        if byte in [0xFD, 0xFE, 0xFF]:
            result.append(0xFD)
        result.append(byte)
    return bytes(result)


def generate_items_otb(items, output_path):
    """Generate items.otb in RME-compatible format"""
    data = bytearray()
    
    # Root node header
    data.extend([0x00, 0x00, 0x00, 0x00])  # 4-byte null magic
    data.append(0xFE)  # Node start
    data.append(0x00)  # Node type (root)
    data.append(0x00)  # Type byte
    data.extend([0x00, 0x00, 0x00, 0x01])  # Flags (0x01 in 4th byte)
    data.append(0x8C)  # Data length byte 1
    data.append(0x00)  # Data length byte 2 (140 total)
    
    # Version info (u32 fields)
    data.extend(struct.pack('<I', 1))    # MajorVersion = 1
    data.extend(struct.pack('<I', 100))  # MinorVersion = 100 (CipSoft 7.70)
    data.extend(struct.pack('<I', 1))    # BuildNumber = 1
    
    # CSD version string (128 bytes)
    csd = b'OTB 1.0.0-7.70-cipsoft\x00'
    data.extend(csd + b'\x00' * (128 - len(csd)))
    
    # Generate item nodes (exclude items with empty names)
    item_count = 0
    for type_id in sorted(items.keys()):
        item = items[type_id]
        
        # Skip items with empty names
        if not item['name']:
            continue
        
        # Determine item group
        flags = item['flags']
        if 'Bank' in flags:
            item_group = ITEM_GROUP_GROUND
        elif 'Container' in flags:
            item_group = ITEM_GROUP_CONTAINER
        elif 'Splash' in flags:
            item_group = ITEM_GROUP_SPLASH
        elif 'Rune' in flags or 'MagicEffect' in flags:
            item_group = ITEM_GROUP_RUNE
        else:
            item_group = ITEM_GROUP_NONE
        
        # Build item node
        item_data = bytearray()
        
        # Flags (4 bytes, all zeros)
        item_data.extend([0x00, 0x00, 0x00, 0x00])
        
        # ServerID attribute
        item_data.append(ITEM_ATTR_SERVERID)
        item_data.extend(struct.pack('<H', 2))  # length
        item_data.extend(struct.pack('<H', type_id))
        
        # ClientID attribute (same as ServerID in CipSoft mode)
        item_data.append(ITEM_ATTR_CLIENTID)
        item_data.extend(struct.pack('<H', 2))  # length
        item_data.extend(struct.pack('<H', type_id))
        
        # Name attribute
        name_bytes = item['name'].encode('latin-1', errors='ignore')
        item_data.append(ITEM_ATTR_NAME)
        item_data.extend(struct.pack('<H', len(name_bytes)))
        item_data.extend(name_bytes)
        
        # Speed attribute for ground items
        if item_group == ITEM_GROUP_GROUND:
            item_data.append(ITEM_ATTR_SPEED)
            item_data.extend(struct.pack('<H', 2))  # length
            item_data.extend(struct.pack('<H', 150))  # speed value
        
        # Escape and write item node
        escaped = escape_otb_data(item_data)
        data.append(NODE_START)
        data.append(item_group)
        data.extend(escaped)
        data.append(NODE_END)
        
        item_count += 1
    
    # End root node
    data.append(NODE_END)
    
    with open(output_path, 'wb') as f:
        f.write(data)
    
    return item_count


# ============================================================================
# Step 4: Generate items.xml
# ============================================================================
def generate_items_xml(items, output_path):
    """Generate items.xml for RME"""
    root = etree.Element('items')
    
    for type_id in sorted(items.keys()):
        item = items[type_id]
        
        # Skip ID 0 (RME rejects it) and empty names
        if type_id == 0 or not item['name']:
            continue
        
        item_elem = etree.SubElement(root, 'item')
        item_elem.set('id', str(type_id))
        item_elem.set('name', item['name'])
        
        # Add article if name starts with "a " or "an "
        name_lower = item['name'].lower()
        if name_lower.startswith('a '):
            item_elem.set('article', 'a')
            item_elem.set('name', item['name'][2:])
        elif name_lower.startswith('an '):
            item_elem.set('article', 'an')
            item_elem.set('name', item['name'][3:])
        
        # Add type attributes based on flags
        flags = item['flags']
        if 'Key' in flags:
            item_elem.set('type', 'key')
        elif 'Container' in flags:
            item_elem.set('type', 'container')
        elif 'Splash' in flags or 'LiquidContainer' in flags or 'LiquidSource' in flags:
            item_elem.set('type', 'splash')
        elif 'Teleport' in flags:
            item_elem.set('type', 'teleport')
        elif 'Door' in flags or 'Hatch' in flags or 'Gate' in flags:
            item_elem.set('type', 'door')
        elif 'Depot' in flags:
            item_elem.set('type', 'depot')
    
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    return len(root)


# ============================================================================
# Step 5: Parse .mon files and generate creatures.xml
# ============================================================================
def parse_mon_files(mon_dir):
    """Parse .mon files to extract creature definitions using FILENAME as name"""
    creatures = {}
    
    # Convert to Path if needed
    if isinstance(mon_dir, str):
        mon_dir = Path(mon_dir)
    
    if not mon_dir.exists():
        return creatures
    
    for filename in os.listdir(str(mon_dir)):
        if not filename.endswith('.mon'):
            continue
        
        filepath = mon_dir / filename
        
        # Use filename (without .mon) as the creature name
        creature_name = filename.replace('.mon', '')
        
        with open(filepath, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()
        
        race_number = None
        looktype = None
        lookhead = lookbody = looklegs = lookfeet = 0
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('RaceNumber') and '=' in line:
                try:
                    race_number = int(line.split('=')[1].split('#')[0].strip())
                except:
                    pass
            elif line.startswith('Outfit') and '=' in line:
                outfit_str = line.split('=', 1)[1].strip().strip('()')
                try:
                    # Format: (118, 0-0-0-0) or (118,0-0-0-0)
                    parts = outfit_str.split(',', 1)
                    looktype = int(parts[0].strip()) if len(parts) > 0 else None
                    if len(parts) > 1:
                        colors = parts[1].strip().split('-')
                        lookhead = int(colors[0]) if len(colors) > 0 else 0
                        lookbody = int(colors[1]) if len(colors) > 1 else 0
                        looklegs = int(colors[2]) if len(colors) > 2 else 0
                        lookfeet = int(colors[3]) if len(colors) > 3 else 0
                except:
                    pass
        
        if looktype:
            creatures[creature_name] = {
                'name': creature_name,
                'looktype': looktype,
                'lookhead': lookhead,
                'lookbody': lookbody,
                'looklegs': looklegs,
                'lookfeet': lookfeet
            }
    
    return creatures


def generate_creatures_xml(creatures, output_path):
    """
    Generate creatures.xml directly from .mon files (no merging)
    Uses filename as creature name to avoid collisions
    """
    all_creatures = []
    
    # Add all creatures from .mon files
    for creature_name, creature in creatures.items():
        c = {
            'name': creature['name'],
            'type': 'monster',
            'looktype': str(creature['looktype']),
            'lookhead': str(creature['lookhead']) if creature['lookhead'] else None,
            'lookbody': str(creature['lookbody']) if creature['lookbody'] else None,
            'looklegs': str(creature['looklegs']) if creature['looklegs'] else None,
            'lookfeet': str(creature['lookfeet']) if creature['lookfeet'] else None,
        }
        all_creatures.append(c)
    
    # Sort all creatures by name
    all_creatures.sort(key=lambda x: x['name'].lower())
    
    # Build new tree with proper formatting
    root = etree.Element('creatures')
    root.text = '\n\t'  # Newline + tab after opening tag
    
    comment1 = etree.Comment(' this file is for indexing only ')
    comment1.tail = '\n\t'
    root.append(comment1)
    
    comment2 = etree.Comment(' for sorting see creature_palette.xml ')
    comment2.tail = '\n\t'
    root.append(comment2)
    
    for i, c in enumerate(all_creatures):
        creature_elem = etree.Element('creature')
        creature_elem.set('name', c['name'])
        creature_elem.set('type', c['type'])
        
        # Only set looktype if it exists
        if c['looktype']:
            creature_elem.set('looktype', c['looktype'])
        
        # Add optional color attributes
        if c['lookhead']:
            creature_elem.set('lookhead', c['lookhead'])
        if c['lookbody']:
            creature_elem.set('lookbody', c['lookbody'])
        if c['looklegs']:
            creature_elem.set('looklegs', c['looklegs'])
        if c['lookfeet']:
            creature_elem.set('lookfeet', c['lookfeet'])
        
        # Set tail for proper indentation
        if i < len(all_creatures) - 1:
            creature_elem.tail = '\n\t'
        else:
            creature_elem.tail = '\n'  # Last element
        
        root.append(creature_elem)
    
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    
    return len(all_creatures)


def generate_creature_palette_xml(creatures, output_path):
    """Generate creature_palette.xml with single category"""
    root = etree.Element('materialsextension')
    root.text = '\n\t'
    
    # Single tileset called "Creatures"
    tileset = etree.SubElement(root, 'tileset', name='Creatures')
    tileset.text = '\n\t\t'
    tileset.tail = '\n'
    
    # Add all creatures sorted by name
    creature_list = sorted(creatures.items(), key=lambda x: x[1]['name'].lower())
    
    for i, (creature_name, creature) in enumerate(creature_list):
        creature_elem = etree.SubElement(tileset, 'creature', name=creature['name'])
        
        # Set tail for proper formatting
        if i < len(creature_list) - 1:
            creature_elem.tail = '\n\t\t'
        else:
            creature_elem.tail = '\n\t'
    
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)


def generate_raw_palette_xml(items, output_path):
    """Generate raw_palette.xml with all items in one category"""
    root = etree.Element('materialsextension')
    root.text = '\n\t'
    
    # Single tileset called "Items"
    tileset = etree.SubElement(root, 'tileset', name='Items')
    tileset.text = '\n\t\t'
    tileset.tail = '\n'
    
    # Add all items sorted by ID
    item_list = [(type_id, item) for type_id, item in items.items() if item['name']]
    item_list.sort(key=lambda x: x[0])
    
    for i, (type_id, item) in enumerate(item_list):
        item_elem = etree.SubElement(tileset, 'item', id=str(type_id))
        
        # Set tail for proper formatting
        if i < len(item_list) - 1:
            item_elem.tail = '\n\t\t'
        else:
            item_elem.tail = '\n\t'
    
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)


# ============================================================================
# Main generation workflow
# ============================================================================
def main():
    print("=" * 70)
    print("RME Data Generator for 7.70 CipSoft TypeIDs")
    print("=" * 70)
    
    # Paths
    assets_dir = Path('assets')
    objects_srv = assets_dir / 'objects.srv'
    mon_dir = assets_dir / 'mon'
    output_dir = Path('output/rme_config/data/770')
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Parse objects.srv
    print("\n[1/5] Parsing objects.srv...")
    items = parse_objects_srv(objects_srv)
    print(f"  ✓ Parsed {len(items)} items from objects.srv")
    
    # Step 2: Generate items.otb
    print("\n[2/5] Generating items.otb...")
    item_count = generate_items_otb(items, output_dir / 'items.otb')
    print(f"  ✓ Generated items.otb ({item_count} items)")
    
    # Step 3: Generate items.xml
    print("\n[3/5] Generating items.xml...")
    xml_count = generate_items_xml(items, output_dir / 'items.xml')
    print(f"  ✓ Generated items.xml ({xml_count} items)")
    
    # Step 4: Generate creatures.xml from .mon files
    print("\n[4/5] Generating creatures.xml...")
    creatures = parse_mon_files(mon_dir)
    if creatures:
        total = generate_creatures_xml(creatures, output_dir / 'creatures.xml')
        print(f"  ✓ Generated creatures.xml ({total} creatures from .mon files)")
    else:
        print(f"  ⚠ No .mon files found in {mon_dir}")
    
    # Step 5: Generate palette XMLs and materials.xml
    print("\n[5/5] Generating palette XMLs...")
    
    # Generate materials.xml (just includes)
    materials_xml = """<materials>
	<include file="creature_palette.xml"/>
	<include file="raw_palette.xml"/>
</materials>
"""
    with open(output_dir / 'materials.xml', 'w', encoding='utf-8') as f:
        f.write(materials_xml)
    print(f"  ✓ materials.xml")
    
    # Generate creature_palette.xml (single category with all creatures)
    if creatures:
        generate_creature_palette_xml(creatures, output_dir / 'creature_palette.xml')
        print(f"  ✓ creature_palette.xml (1 tileset, {len(creatures)} creatures)")
    
    # Generate raw_palette.xml (all items in one category)
    generate_raw_palette_xml(items, output_dir / 'raw_palette.xml')
    print(f"  ✓ raw_palette.xml (1 tileset, {len(items)} items)")
    
    # Generate clients.xml snippet
    print("\n[6/6] Generating clients.xml snippet...")
    snippet = """<!-- Add this to RME/data/clients.xml -->

<!-- In the <otbs> section, add: -->
<otb client="7.70-cipsoft" version="1" id="100"/>

<!-- In the <clients> section, add: -->
<client name="7.70 (CipSoft)" otb="7.70-cipsoft" visible="true" data_directory="770">
    <otbm version="1"/>
    <extensions from="7.6" to="7.6"/>
    <data format="7.55" dat="0x439D5A33" spr="0x439852BE"/>
</client>
"""
    
    with open('output/rme_config/clients_xml_snippet.txt', 'w') as f:
        f.write(snippet)
    print(f"  ✓ clients_xml_snippet.txt")
    
    print("\n" + "=" * 70)
    print("✅ RME data generation complete!")
    print("=" * 70)
    print(f"\n📁 Output: {output_dir.absolute()}")
    print(f"\n📋 Next steps:")
    print(f"   1. Copy {output_dir}/ to your RME installation")
    print(f"   2. Add clients.xml snippet to RME/data/clients.xml")
    print(f"   3. Copy Tibia.dat + Tibia.spr to RME client data path")
    print()


if __name__ == '__main__':
    main()

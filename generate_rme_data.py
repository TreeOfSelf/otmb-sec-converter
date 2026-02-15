#!/usr/bin/env python3
"""
RME Configuration Generator for 7.70 CipSoft TypeIDs
Generates ONLY RME configuration files (NOT map conversion)

Input:  tibia-game folder
Output: output/rme_config/data/770-cipsoft/ (items.otb, items.xml, creatures.xml, palettes)

For map conversion, use sec_to_otbm.py separately.
"""
import sys
import os
import struct
from pathlib import Path
from lxml import etree
from collections import defaultdict

# ============================================================================
# OTB/OTBM Constants (RME items.h itemflags_t, items.cpp loadFromOtbVer1)
# ============================================================================
ITEM_ATTR_SERVERID = 0x10
ITEM_ATTR_CLIENTID = 0x11
ITEM_ATTR_NAME = 0x12
ITEM_ATTR_SPEED = 0x14
# RME items.h ITEM_ATTR_MAXITEMS = 0x16; items.cpp loadFromOtb: volume (uint16) = number of container slots
ITEM_ATTR_MAXITEMS = 0x16

# RME items.h FLAG_* — OTB item node has uint32 flags after group byte; RME sets stackable from FLAG_STACKABLE
FLAG_STACKABLE = 1 << 7

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

# OTBM Node Types
SECTOR_SIZE = 32
NODE_ESC = 0xFD
NODE_INIT = 0xFE
NODE_TERM = 0xFF

OTBM_MAP_HEADER = 0x00
OTBM_MAP_DATA = 0x02
OTBM_TILE_AREA = 0x04
OTBM_TILE = 0x05
OTBM_ITEM = 0x06
OTBM_ATTR_DESCRIPTION = 0x01


# ============================================================================
# OTBM Writer
# ============================================================================
class OTBMWriter:
    """Handles writing OTBM files with proper escape sequences"""
    
    def __init__(self):
        self.data = bytearray()
    
    def write_byte(self, b):
        """Write a byte with escape handling"""
        if b in (NODE_ESC, NODE_INIT, NODE_TERM):
            self.data.append(NODE_ESC)
        self.data.append(b)
    
    def write_uint16(self, val):
        """Write uint16 (little-endian) with escape handling"""
        self.write_byte(val & 0xFF)
        self.write_byte((val >> 8) & 0xFF)
    
    def write_uint32(self, val):
        """Write uint32 (little-endian) with escape handling"""
        self.write_byte(val & 0xFF)
        self.write_byte((val >> 8) & 0xFF)
        self.write_byte((val >> 16) & 0xFF)
        self.write_byte((val >> 24) & 0xFF)
    
    def write_string(self, s):
        """Write string with length prefix"""
        encoded = s.encode('latin-1')
        self.write_uint16(len(encoded))
        for b in encoded:
            self.write_byte(b)
    
    def start_node(self, node_type):
        """Start a new node"""
        self.data.append(NODE_INIT)
        self.write_byte(node_type)
    
    def end_node(self):
        """End current node"""
        self.data.append(NODE_TERM)
    
    def get_bytes(self):
        """Return the complete byte array"""
        return bytes(self.data)


# ============================================================================
# Step 1: Parse objects.srv for items
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
            disguise_target = None
            capacity = None
            
            # Read next lines for Name, Flags, Attributes
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
                elif line.startswith('Attributes') and '=' in line:
                    attrs_str = line.split('=', 1)[1].strip().strip('{}')
                    for part in attrs_str.split(','):
                        part = part.strip()
                        if '=' in part:
                            k, v = part.split('=', 1)
                            k = k.strip()
                            try:
                                val = int(v.strip())
                            except ValueError:
                                val = None
                            if k == 'DisguiseTarget':
                                disguise_target = val
                            elif k == 'Capacity':
                                capacity = val
                
                i += 1
                if not line or line == '':
                    break
            
            items[type_id] = {
                'type_id': type_id,
                'name': name,
                'flags': flags,
                'disguise_target': disguise_target,
                'capacity': capacity,
            }
        else:
            i += 1
    
    return items


# ============================================================================
# Step 2: Generate items.otb with proper binary format
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
    
    # Generate item nodes (skip items with empty names)
    item_count = 0
    for type_id in sorted(items.keys()):
        item = items[type_id]
        
        # Skip items with empty names
        if not item['name']:
            continue
        
        # Determine item group (Chest = container in CipSoft, e.g. bananapalm 2547)
        flags = item['flags']
        if 'Bank' in flags:
            item_group = ITEM_GROUP_GROUND
        elif 'Container' in flags or 'Chest' in flags:
            item_group = ITEM_GROUP_CONTAINER
        elif 'Splash' in flags:
            item_group = ITEM_GROUP_SPLASH
        elif 'Rune' in flags or 'MagicEffect' in flags:
            item_group = ITEM_GROUP_RUNE
        else:
            item_group = ITEM_GROUP_NONE
        
        # Build item node
        item_data = bytearray()
        
        # Flags (4 bytes, uint32): RME reads FLAG_STACKABLE so getCount() returns subtype and count is editable
        otb_flags = FLAG_STACKABLE if 'Cumulative' in flags else 0
        item_data.extend(struct.pack('<I', otb_flags))
        
        # ServerID attribute (map/server type id)
        item_data.append(ITEM_ATTR_SERVERID)
        item_data.extend(struct.pack('<H', 2))  # length
        item_data.extend(struct.pack('<H', type_id))
        
        # ClientID attribute (sprite id): use DisguiseTarget when set so RME shows correct graphic (e.g. 2547 bananapalm -> 3639)
        client_id = item['disguise_target'] if item.get('disguise_target') is not None else type_id
        item_data.append(ITEM_ATTR_CLIENTID)
        item_data.extend(struct.pack('<H', 2))  # length
        item_data.extend(struct.pack('<H', client_id))
        
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
        
        # Volume (slots) for Container and Chest — RME getVolume() returns g_items[id].volume; 0 = no slots shown
        # Container: use Capacity from objects.srv or default 8. Chest: always 8 slots (no derivation).
        if item_group == ITEM_GROUP_CONTAINER:
            volume = item.get('capacity') if 'Container' in flags else None
            if volume is None:
                volume = 8
            volume = max(1, min(0xFFFF, int(volume)))
            item_data.append(ITEM_ATTR_MAXITEMS)
            item_data.extend(struct.pack('<H', 2))  # length
            item_data.extend(struct.pack('<H', volume))
        
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
# Step 3: Generate items.xml
# ============================================================================
def generate_items_xml(items, output_path):
    """Generate items.xml for RME"""
    root = etree.Element('items')
    
    for type_id in sorted(items.keys()):
        item = items[type_id]
        
        # Skip items with empty names
        if not item['name']:
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
        
        # Add type attributes based on flags (Chest = container in CipSoft)
        flags = item['flags']
        if 'Key' in flags:
            item_elem.set('type', 'key')
        elif 'Container' in flags or 'Chest' in flags:
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
# Step 4: Parse .mon files and generate creatures.xml
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
        
        # Use filename with "mon-" prefix (e.g. demon.mon -> mon-demon)
        creature_name = 'mon-' + filename.replace('.mon', '')
        
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
        
        # Fallback: if no valid looktype (0 or None), use RaceNumber so RME shows something
        effective_looktype = looktype if (looktype is not None and looktype != 0) else race_number
        if effective_looktype is None:
            effective_looktype = 0
        # Include when we have outfit and/or race (so we have a look)
        if looktype is not None or race_number is not None:
            creatures[creature_name] = {
                'name': creature_name,
                'race_number': race_number,
                'looktype': effective_looktype,
                'lookhead': lookhead,
                'lookbody': lookbody,
                'looklegs': looklegs,
                'lookfeet': lookfeet
            }
    
    return creatures


def generate_creatures_xml(creatures, npc_creatures, output_path):
    """Generate creatures.xml directly from .mon files and .npc files"""
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
    
    # Add all NPCs from .npc files
    for npc_name, npc in npc_creatures.items():
        c = {
            'name': npc['name'],
            'type': 'npc',
            'looktype': str(npc['looktype']),
            'lookhead': str(npc['lookhead']) if npc['lookhead'] else None,
            'lookbody': str(npc['lookbody']) if npc['lookbody'] else None,
            'looklegs': str(npc['looklegs']) if npc['looklegs'] else None,
            'lookfeet': str(npc['lookfeet']) if npc['lookfeet'] else None,
        }
        all_creatures.append(c)
    
    # Sort all creatures by name
    all_creatures.sort(key=lambda x: x['name'].lower())
    
    # Build new tree with proper formatting
    root = etree.Element('creatures')
    root.text = '\n\t'
    
    for i, c in enumerate(all_creatures):
        creature_elem = etree.Element('creature')
        creature_elem.set('name', c['name'])
        creature_elem.set('type', c['type'])
        creature_elem.set('looktype', c['looktype'])  # 0 is valid
        if c['lookhead']:
            creature_elem.set('lookhead', c['lookhead'])
        if c['lookbody']:
            creature_elem.set('lookbody', c['lookbody'])
        if c['looklegs']:
            creature_elem.set('looklegs', c['looklegs'])
        if c['lookfeet']:
            creature_elem.set('lookfeet', c['lookfeet'])
        
        if i < len(all_creatures) - 1:
            creature_elem.tail = '\n\t'
        else:
            creature_elem.tail = '\n'
        
        root.append(creature_elem)
    
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    
    return len(all_creatures)


# ============================================================================
# Step 5: Parse .sec files (map data)
# ============================================================================
def parse_sec_file(sec_file):
    """Parse a single .sec file and return tiles with items"""
    tiles = []
    
    with open(sec_file, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            
            if not line or line.startswith('#'):
                continue
            
            if ':' not in line or 'Content=' not in line:
                continue
            
            try:
                coords_part, rest = line.split(':', 1)
                lx, ly = map(int, coords_part.strip().split('-'))
                
                if 'Refresh' in rest:
                    rest = rest.replace('Refresh,', '').replace('Refresh', '')
                
                if 'Content={' not in rest:
                    continue
                
                try:
                    content_part = rest.split('Content={', 1)[1]
                    if '}' not in content_part:
                        continue
                    content_str = content_part.split('}', 1)[0]
                except IndexError:
                    continue
                    
                if not content_str.strip():
                    continue
                
                items = []
                for item_str in content_str.split(','):
                    item_str = item_str.strip()
                    if not item_str:
                        continue
                    
                    parts = item_str.split()
                    
                    try:
                        item_id = int(parts[0])
                    except (ValueError, IndexError):
                        continue
                    
                    item_data = {'id': item_id}
                    
                    for part in parts[1:]:
                        if '=' in part:
                            key, value = part.split('=', 1)
                            try:
                                item_data[key.lower()] = int(value)
                            except ValueError:
                                pass
                    
                    items.append(item_data)
                
                if items:
                    tiles.append((lx, ly, items))
                    
            except (ValueError, IndexError):
                continue
    
    return tiles


def load_all_sectors(sec_dir):
    """Load all .sec files and organize by sector"""
    sec_dir = Path(sec_dir)
    sectors = defaultdict(list)
    
    print(f"Scanning for .sec files in {sec_dir}...")
    sec_files = sorted(sec_dir.glob("*.sec"))
    total_files = len(sec_files)
    print(f"  Found {total_files} sector files.")
    
    parsed = 0
    skipped = 0
    
    for idx, sec_file in enumerate(sec_files, 1):
        if idx % 500 == 0:
            print(f"  Progress: {idx}/{total_files} files ({parsed} parsed, {skipped} skipped)")
        
        try:
            name_parts = sec_file.stem.split('-')
            sx = int(name_parts[0])
            sy = int(name_parts[1])
            z = int(name_parts[2])
            
            tiles = parse_sec_file(sec_file)
            if tiles:
                sectors[(sx, sy, z)].extend(tiles)
                parsed += 1
            else:
                skipped += 1
        except (ValueError, IndexError):
            skipped += 1
            continue
        except Exception:
            skipped += 1
            continue
    
    print(f"  Loaded {len(sectors)} sectors with tiles (parsed: {parsed}, skipped: {skipped}).")
    return sectors


def calculate_offset(sectors):
    """Calculate the offset needed to move map to start at 0,0"""
    min_x = min_y = float('inf')
    
    for (sx, sy, z), tiles in sectors.items():
        for lx, ly, _ in tiles:
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            min_x = min(min_x, abs_x)
            min_y = min(min_y, abs_y)
    
    return int(min_x), int(min_y)


def calculate_bounds(sectors, offset_x=0, offset_y=0):
    """Calculate transformed map bounds after applying offset"""
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for (sx, sy, _z), tiles in sectors.items():
        for lx, ly, _items in tiles:
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            new_x = abs_x - offset_x
            new_y = abs_y - offset_y
            min_x = min(min_x, new_x)
            min_y = min(min_y, new_y)
            max_x = max(max_x, new_x)
            max_y = max(max_y, new_y)

    if min_x == float('inf'):
        return 0, 0, 0, 0
    return int(min_x), int(min_y), int(max_x), int(max_y)


def build_otbm_header(width, height):
    """Build OTBM header for CipSoft 7.7 (OTB ID 100)"""
    writer = OTBMWriter()
    writer.start_node(OTBM_MAP_HEADER)
    writer.write_uint32(1)    # OTBM version
    writer.write_uint16(width)
    writer.write_uint16(height)
    writer.write_uint32(1)    # OTB major version
    writer.write_uint32(100)  # OTB minor version (ID 100 = CipSoft 7.7)
    return writer.get_bytes()


def convert_map_to_otbm(sec_dir, output_file, map_name, apply_offset=True):
    """Convert .sec files to OTBM format"""
    
    print("\n" + "="*70)
    print("GENERATING MAP (OTBM)")
    print("="*70)
    
    sectors = load_all_sectors(sec_dir)
    
    if not sectors:
        print("Error: No valid sectors found!")
        return
    
    offset_x, offset_y = calculate_offset(sectors)
    
    if apply_offset:
        print(f"\nApplying coordinate offset: -{offset_x}, -{offset_y}")
    else:
        print(f"\nNOT applying offset (using original coordinates)")
        offset_x = 0
        offset_y = 0
    
    min_x, min_y, max_x, max_y = calculate_bounds(sectors, offset_x, offset_y)
    width = max_x - min_x + 1
    height = max_y - min_y + 1

    width = max(1, min(65535, width))
    height = max(1, min(65535, height))

    print(f"\nMap bounds: X={min_x}..{max_x} (w={width}), Y={min_y}..{max_y} (h={height})")

    header = build_otbm_header(width, height)
    
    writer = OTBMWriter()
    writer.data.extend(header)
    
    print("\nWriting OTBM structure...")
    
    writer.start_node(OTBM_MAP_DATA)
    writer.write_byte(OTBM_ATTR_DESCRIPTION)
    writer.write_string(map_name)
    
    areas = defaultdict(list)
    for (sx, sy, z), tiles in sectors.items():
        for lx, ly, items in tiles:
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            
            new_x = abs_x - offset_x
            new_y = abs_y - offset_y
            
            area_x = new_x & 0xFF00
            area_y = new_y & 0xFF00
            local_x = new_x & 0xFF
            local_y = new_y & 0xFF
            
            areas[(area_x, area_y, z)].append({
                'x': local_x,
                'y': local_y,
                'items': items
            })
    
    print(f"  Writing {len(areas)} tile areas...")
    
    total_tiles = 0
    total_items = 0
    
    for idx, ((bx, by, z), tiles) in enumerate(sorted(areas.items())):
        writer.start_node(OTBM_TILE_AREA)
        writer.write_uint16(bx)
        writer.write_uint16(by)
        writer.write_byte(z)
        
        for tile in tiles:
            total_tiles += 1
            items = tile['items']
            
            if not items:
                continue
            
            writer.start_node(OTBM_TILE)
            writer.write_byte(tile['x'])
            writer.write_byte(tile['y'])
            
            for item_data in items:
                writer.start_node(OTBM_ITEM)
                writer.write_uint16(item_data['id'])
                writer.end_node()
                total_items += 1
            
            writer.end_node()
        
        writer.end_node()
        
        if idx % 200 == 0:
            print(f"  Progress: {idx}/{len(areas)} areas...")
    
    writer.end_node()
    writer.end_node()
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'wb') as f:
        f.write(b'OTBM')
        f.write(writer.get_bytes())
    
    print(f"\n✓ Map generated: {output_file}")
    print(f"  Tiles: {total_tiles:,}, Items: {total_items:,}")


# ============================================================================
# Step 6: Parse houseareas.dat and houses.dat, generate houses XML
# ============================================================================
def parse_houseareas(houseareas_path):
    """Parse houseareas.dat to get Area → Depot mapping"""
    area_to_depot = {}
    
    with open(houseareas_path, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if line.startswith('Area'):
                try:
                    content = line.split('=', 1)[1].strip()
                    content = content.strip('()')
                    parts = content.split(',')
                    
                    area_id = int(parts[0].strip())
                    depot = int(parts[3].strip())
                    
                    area_to_depot[area_id] = depot
                except (ValueError, IndexError):
                    continue
    
    return area_to_depot


def parse_houses_dat(houses_path):
    """Parse houses.dat file"""
    houses = []
    
    with open(houses_path, 'r', encoding='latin-1', errors='ignore') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        if line.startswith('ID'):
            house = {}
            
            try:
                house['id'] = int(line.split('=')[1].strip())
            except:
                i += 1
                continue
            
            i += 1
            while i < len(lines):
                line = lines[i].strip()
                
                if line.startswith('ID'):
                    i -= 1
                    break
                
                if line.startswith('Name'):
                    house['name'] = line.split('=', 1)[1].strip().strip('"')
                elif line.startswith('RentOffset'):
                    house['rent'] = int(line.split('=')[1].strip())
                elif line.startswith('Area'):
                    house['area'] = int(line.split('=')[1].strip())
                elif line.startswith('GuildHouse'):
                    house['guildhall'] = line.split('=')[1].strip().lower() == 'true'
                elif line.startswith('Exit'):
                    coords = line.split('=')[1].strip().strip('[]')
                    parts = coords.split(',')
                    house['entryx'] = int(parts[0])
                    house['entryy'] = int(parts[1])
                    house['entryz'] = int(parts[2])
                elif line.startswith('Fields'):
                    fields_str = line.split('=')[1].strip()
                    fields_str = fields_str.strip('{}')
                    house['size'] = fields_str.count('[')
                
                i += 1
                if not line:
                    break
            
            houses.append(house)
        else:
            i += 1
    
    return houses


def generate_houses_xml(houses_path, houseareas_path, output_path):
    """Generate map-houses.xml from houses.dat"""
    
    print("\n" + "="*70)
    print("GENERATING HOUSES XML")
    print("="*70)
    
    area_to_depot = parse_houseareas(houseareas_path)
    houses = parse_houses_dat(houses_path)
    
    print(f"Found {len(houses)} houses")
    
    xml_lines = ['<?xml version="1.0"?>']
    xml_lines.append('<houses>')
    
    for house in houses:
        area = house.get('area', 100)
        house_id = house['id'] - (area * 100)
        depot = area_to_depot.get(area, 0)
        town_id = depot + 1
        
        attrs = [
            f'name="{house.get("name", "")}"',
            f'houseid="{house_id}"',
            f'entryx="{house.get("entryx", 0)}"',
            f'entryy="{house.get("entryy", 0)}"',
            f'entryz="{house.get("entryz", 7)}"',
            f'rent="{house.get("rent", 0)}"',
        ]
        
        if house.get('guildhall', False):
            attrs.append('guildhall="true"')
        
        attrs.append(f'townid="{town_id}"')
        attrs.append(f'size="{house.get("size", 0)}"')
        
        xml_lines.append(f'\t<house {" ".join(attrs)} />')
    
    xml_lines.append('</houses>')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    print(f"✓ Houses XML generated: {len(houses)} houses")


# ============================================================================
# Step 7: Build race lookup and generate spawns XML
# ============================================================================
def build_race_lookup(creatures):
    """Build Race → Monster Name lookup from creatures dict"""
    race_to_name = {}
    
    for creature_name, creature in creatures.items():
        race_number = creature.get('race_number')
        if race_number is not None:
            race_to_name[race_number] = creature['name']
    
    return race_to_name


def parse_monsters_db(monsters_db_path):
    """Parse monsters.db file"""
    spawns = []
    
    with open(monsters_db_path, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            
            if not line or line.startswith('#'):
                continue
            
            parts = line.split()
            if len(parts) < 7:
                continue
            
            try:
                spawn = {
                    'race': int(parts[0]),
                    'x': int(parts[1]),
                    'y': int(parts[2]),
                    'z': int(parts[3]),
                    'radius': int(parts[4]),
                    'amount': int(parts[5]),
                    'spawntime': int(parts[6])
                }
                spawns.append(spawn)
            except (ValueError, IndexError):
                continue
    
    return spawns


def parse_npc_files(npc_dir):
    """Parse .npc files to extract NPC spawn data and outfit info (using filename with npc- prefix)"""
    npc_spawns = []
    npc_creatures = {}  # For creatures.xml generation
    
    npc_dir = Path(npc_dir)
    if not npc_dir.exists():
        return npc_spawns, npc_creatures
    
    for npc_file in npc_dir.glob("*.npc"):
        npc_filename = npc_file.stem
        # Use filename with "npc-" prefix (e.g. frans.npc -> npc-frans)
        display_name = 'npc-' + npc_filename
        
        home_x = home_y = home_z = None
        radius = 3
        looktype = None
        lookhead = lookbody = looklegs = lookfeet = 0
        
        with open(npc_file, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                if line.startswith('Name') and '=' in line:
                    pass  # Name field no longer used for creature id; we use filename + npc- prefix
                elif line.startswith('Home') and '=' in line:
                    try:
                        coords = line.split('=')[1].strip().strip('[]')
                        parts = coords.split(',')
                        home_x = int(parts[0])
                        home_y = int(parts[1])
                        home_z = int(parts[2])
                    except:
                        pass
                elif line.startswith('Radius') and '=' in line:
                    try:
                        radius = int(line.split('=')[1].strip())
                    except:
                        pass
                elif line.startswith('Outfit') and '=' in line:
                    outfit_str = line.split('=', 1)[1].strip().strip('()')
                    try:
                        # Format: (looktype, head-body-legs-feet)
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
        
        # Add to spawn list if has position (display_name is npc- + filename)
        if home_x is not None:
            npc_spawns.append({
                'name': display_name,
                'x': home_x,
                'y': home_y,
                'z': home_z,
                'radius': radius
            })
        # Add to creatures dict: every NPC; fallback looktype 130 (wizard) if 0 or missing
        effective_looktype = (looktype if (looktype is not None and looktype != 0) else 130)
        npc_creatures[display_name] = {
            'name': display_name,
            'looktype': effective_looktype,
            'lookhead': lookhead,
            'lookbody': lookbody,
            'looklegs': looklegs,
            'lookfeet': lookfeet
        }
    
    return npc_spawns, npc_creatures


def generate_spawns_xml(monsters_db_path, creatures, npc_dir, output_path):
    """Generate map-spawns.xml from monsters.db and .npc files"""
    
    print("\n" + "="*70)
    print("GENERATING SPAWNS XML")
    print("="*70)
    
    race_to_name = build_race_lookup(creatures)
    print(f"Built race lookup: {len(race_to_name)} monsters")
    
    monster_spawns = parse_monsters_db(monsters_db_path)
    print(f"Found {len(monster_spawns)} monster spawn entries")
    
    npc_spawns, npc_creatures = parse_npc_files(npc_dir)
    print(f"Found {len(npc_spawns)} NPC spawns")
    
    xml_lines = ['<?xml version="1.0"?>']
    xml_lines.append('<spawns>')
    
    # Add monster spawns
    for spawn in monster_spawns:
        race = spawn['race']
        monster_name = race_to_name.get(race)
        
        if not monster_name:
            continue
        
        for i in range(spawn['amount']):
            xml_lines.append(
                f'\t<spawn centerx="{spawn["x"]}" centery="{spawn["y"]}" '
                f'centerz="{spawn["z"]}" radius="{spawn["radius"]}">'
                f'<monster name="{monster_name}" x="0" y="0" z="{spawn["z"]}" '
                f'spawntime="{spawn["spawntime"]}"/></spawn>'
            )
    
    # Add NPC spawns
    for npc in npc_spawns:
        xml_lines.append(
            f'\t<spawn centerx="{npc["x"]}" centery="{npc["y"]}" '
            f'centerz="{npc["z"]}" radius="{npc["radius"]}">'
            f'<npc name="{npc["name"]}" x="0" y="0" z="{npc["z"]}" '
            f'spawntime="60"/></spawn>'
        )
    
    xml_lines.append('</spawns>')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    print(f"✓ Spawns XML generated: {len(monster_spawns)} monsters, {len(npc_spawns)} NPCs")


# ============================================================================
# Main workflow
# ============================================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_rme_data.py <tibia-game-folder>")
        print("\nExample:")
        print("  python3 generate_rme_data.py ./tibia-game")
        print("\nThis will generate:")
        print("  output/rme_config/data/770-cipsoft/  (RME config files)")
        print("  output/rme_config/clients_xml_snippet.txt")
        sys.exit(1)
    
    tibia_game_dir = Path(sys.argv[1])
    
    dat_dir = tibia_game_dir / 'dat'
    mon_dir = tibia_game_dir / 'mon'
    objects_srv = dat_dir / 'objects.srv'
    
    if not tibia_game_dir.exists():
        print(f"Error: {tibia_game_dir} not found!")
        sys.exit(1)
    
    if not objects_srv.exists():
        print(f"Error: {objects_srv} not found!")
        sys.exit(1)
    
    print("=" * 70)
    print("RME Data Generator for 7.70 CipSoft TypeIDs")
    print("=" * 70)
    print(f"\nInput:  {tibia_game_dir.absolute()}")
    
    rme_config_dir = Path('output/rme_config/data/770-cipsoft')
    rme_config_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Output: {rme_config_dir.absolute()}")
    print("=" * 70)
    
    # Step 1: Parse objects.srv
    print("\n[1/4] Parsing objects.srv...")
    items = parse_objects_srv(objects_srv)
    print(f"  ✓ Parsed {len(items)} items")
    
    # Step 2: Generate items.otb
    print("\n[2/4] Generating items.otb...")
    item_count = generate_items_otb(items, rme_config_dir / 'items.otb')
    print(f"  ✓ Generated ({item_count} items)")
    
    # Step 3: Generate items.xml
    print("\n[3/4] Generating items.xml...")
    xml_count = generate_items_xml(items, rme_config_dir / 'items.xml')
    print(f"  ✓ Generated ({xml_count} items)")
    
    # Step 4: Generate creatures.xml
    print("\n[4/4] Generating creatures.xml...")
    creatures = parse_mon_files(mon_dir)
    npc_dir = tibia_game_dir / 'npc'
    _, npc_creatures = parse_npc_files(npc_dir)
    
    if creatures or npc_creatures:
        total = generate_creatures_xml(creatures, npc_creatures, rme_config_dir / 'creatures.xml')
        print(f"  ✓ Generated ({total} creatures: {len(creatures)} monsters, {len(npc_creatures)} NPCs)")
    else:
        print(f"  ⚠ No .mon or .npc files found")
    
    # Write empty materials.xml (RME expects it to exist)
    with open(rme_config_dir / 'materials.xml', 'w', encoding='utf-8') as f:
        f.write('<materials/>\n')
    print(f"  ✓ materials.xml (empty)")
    
    # Generate clients.xml snippet
    snippet = """<!-- Add this to RME/data/clients.xml -->

<!-- In the <otbs> section, add: -->
<otb client="7.70-cipsoft" version="1" id="100"/>

<!-- In the <clients> section, add: -->
<client name="7.70 (CipSoft)" otb="7.70-cipsoft" visible="true" data_directory="770-cipsoft">
    <otbm version="1"/>
    <extensions from="7.6" to="7.6"/>
    <data format="7.55" dat="0x439D5A33" spr="0x439852BE"/>
</client>
"""
    
    with open('output/rme_config/clients_xml_snippet.txt', 'w') as f:
        f.write(snippet)
    print(f"  ✓ clients_xml_snippet.txt")
    
    print("\n" + "=" * 70)
    print("✅ RME Configuration Complete!")
    print("=" * 70)
    print(f"\nOutput: {rme_config_dir.absolute()}")
    print(f"\n📋 Next steps:")
    print(f"   1. Copy {rme_config_dir}/ to RME/data/770-cipsoft/")
    print(f"   2. Add clients_xml_snippet.txt content to RME/data/clients.xml")
    print(f"   3. Copy Tibia.dat + Tibia.spr to RME client data directory")
    print()


if __name__ == '__main__':
    main()
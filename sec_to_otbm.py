#!/usr/bin/env python3
"""
SEC to OTBM Converter + Houses + Spawns Generator
Converts CipSoft .sec map files to OTBM and generates auxiliary XMLs

Usage: python3 sec_to_otbm.py <tibia-game-folder> <output-name>

Example:
  python3 sec_to_otbm.py ./tibia-game myworld

Output:
  output/myworld.otbm
  output/myworld-house.xml
  output/myworld-spawn.xml

Keeps original Tibia coordinates (map centered around 32000,32000).
Map size: 65535x65535 (full OTBM size).
"""
import sys
from pathlib import Path
from collections import defaultdict

# ============================================================================
# OTBM Constants
# ============================================================================
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
OTBM_ATTR_EXT_SPAWN_FILE = 11   # RME: spawn filename in same dir as .otbm
OTBM_ATTR_EXT_HOUSE_FILE = 13   # RME: house filename in same dir as .otbm
OTBM_TOWNS = 12
OTBM_TOWN = 13


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
# Parse moveuse.dat Hometeleporters for temple positions (SetStart x,y,z)
# ============================================================================
def parse_temples_from_moveuse(moveuse_path):
    """
    Parse tibia-game/dat/moveuse.dat section BEGIN "Hometeleporters".
    Lines with SetStart(Obj2,[x,y,z]) and "Home TownName (1)" give the actual temple position.
    Returns dict: town_name -> (x, y, z). Uses first occurrence per town (prefer " (1)" over " (?)").
    """
    path = Path(moveuse_path)
    if not path.exists():
        return {}

    temples = {}  # town_name -> (x, y, z)
    in_section = False

    with open(path, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if line == 'BEGIN "Hometeleporters"':
                in_section = True
                continue
            if in_section:
                if line.startswith('BEGIN ') or line == 'END':
                    break
                # Line must have SetStart(Obj2,[x,y,z]) and "Home ... (1)" or "Home ... (?)"
                if 'SetStart(Obj2,' not in line or '"Home ' not in line:
                    continue
                try:
                    # Coords: SetStart(Obj2,[32369,32241,07]) -> 32369, 32241, 7
                    i = line.index('SetStart(Obj2,[') + len('SetStart(Obj2,[')
                    j = line.index('])', i)
                    coord_str = line[i:j]  # "32369,32241,07"
                    coords = [int(c.strip()) for c in coord_str.split(',')]
                    x, y, z = coords[0], coords[1], coords[2]
                    # Town name: "Home Thais (1)" or "Home Port Hope (1)" -> Thais / Port Hope
                    label_start = line.index('"Home ') + len('"Home ')
                    label_end_1 = line.find(' (1)"', label_start)
                    label_end_2 = line.find(' (?)"', label_start)
                    if label_end_1 != -1:
                        label_end = label_end_1
                    elif label_end_2 != -1:
                        label_end = label_end_2
                    else:
                        continue
                    name = line[label_start:label_end]
                    # Prefer (1) over (?): only overwrite if we don't have this name yet, or if current is (1)
                    if name not in temples or ' (1)"' in line:
                        temples[name] = (x, y, z)
                        temples[name.replace(' ', '')] = (x, y, z)  # "Port Hope" -> "PortHope"
                except (ValueError, IndexError):
                    continue

    return temples


# ============================================================================
# Parse map.dat for towns (Depot = id+name); temple from moveuse or Mark
# ============================================================================
def parse_map_dat(map_dat_path, temple_positions=None):
    """
    Parse tibia-game/dat/map.dat for Depots (town id + name).
    Temple position: use temple_positions[name] if provided (from moveuse Hometeleporters),
    else fall back to Mark lines in map.dat.
    Returns list of dicts: [{'id': town_id, 'name': str, 'x': int, 'y': int, 'z': int}, ...]
    town_id = depot_id + 1 (Thais depot 0 -> town 1).
    """
    path = Path(map_dat_path)
    if not path.exists():
        return []

    depots = []   # (depot_id, name)
    marks = {}    # name -> (x, y, z); fallback from map.dat Mark lines

    with open(path, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('Depot'):
                # Depot = (0,"Thais",1000)
                try:
                    rest = line.split('=', 1)[1].strip().strip('()')
                    parts = [p.strip() for p in rest.split(',', 2)]
                    depot_id = int(parts[0])
                    name = parts[1].strip('"')
                    depots.append((depot_id, name))
                except (ValueError, IndexError):
                    continue

            elif line.startswith('Mark'):
                # Mark = ("Thais",[32369,32215,7])  (fallback if no moveuse temples)
                try:
                    rest = line.split('=', 1)[1].strip()
                    name_start = rest.index('"') + 1
                    name_end = rest.index('"', name_start)
                    name = rest[name_start:name_end]
                    bracket_start = rest.index('[')
                    bracket_end = rest.index(']', bracket_start)
                    rest = rest[bracket_start + 1 : bracket_end]
                    coords = [int(c.strip()) for c in rest.split(',')]
                    x, y, z = coords[0], coords[1], coords[2]
                    marks[name] = (x, y, z)
                    marks[name.replace(' ', '')] = (x, y, z)
                except (ValueError, IndexError):
                    continue

    towns = []
    for depot_id, name in depots:
        # Prefer temple from moveuse.dat Hometeleporters (SetStart), else map.dat Mark
        pos = None
        if temple_positions:
            pos = temple_positions.get(name) or temple_positions.get(name.replace(' ', ''))
        if pos is None:
            pos = marks.get(name) or marks.get(name.replace(' ', ''))
        if pos is None:
            continue
        x, y, z = pos
        town_id = depot_id + 1  # Depot 0 -> Town 1 (Thais)
        towns.append({
            'id': town_id,
            'name': name,
            'x': x, 'y': y, 'z': z
        })

    return towns


# ============================================================================
# Map walkability checking
# ============================================================================
def load_walkable_tiles_from_sectors(sectors):
    """
    Build a set of walkable tile positions from the sector data.
    A tile is walkable if it has ground items (items that can be walked on).
    This is a simplified check - in reality we'd need item type data.
    For now, we assume any tile with items is potentially walkable.
    """
    walkable_tiles = set()
    
    for (sx, sy, z), tiles in sectors.items():
        for lx, ly, items in tiles:
            if items:  # Has items, likely has ground
                abs_x = sx * SECTOR_SIZE + lx
                abs_y = sy * SECTOR_SIZE + ly
                walkable_tiles.add((abs_x, abs_y, z))
    
    return walkable_tiles


# ============================================================================
# Parse .sec files
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
    
    print(f"\nScanning for .sec files in {sec_dir}...")
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


def convert_map_to_otbm(sectors, output_file, map_name, towns=None):
    """Convert .sec files to OTBM format"""
    
    print("\n" + "="*70)
    print("CONVERTING MAP TO OTBM")
    print("="*70)
    
    if towns is None:
        towns = []
    
    if not sectors:
        print("Error: No valid sectors found!")
        return
    
    print(f"\n✓ Using original Tibia coordinates")
    min_x, min_y, max_x, max_y = calculate_bounds(sectors, 0, 0)
    # Use full Tibia map size
    width = 65535
    height = 65535
    print(f"Map canvas: {width}x{height} (full OTBM size)")
    print(f"Actual tile coverage: X={min_x}..{max_x}, Y={min_y}..{max_y}")

    header = build_otbm_header(width, height)
    
    writer = OTBMWriter()
    writer.data.extend(header)
    
    print("\nWriting OTBM structure...")
    
    writer.start_node(OTBM_MAP_DATA)
    writer.write_byte(OTBM_ATTR_DESCRIPTION)
    writer.write_string(map_name)
    # RME looks for these filenames in the same directory as the .otbm
    writer.write_byte(OTBM_ATTR_EXT_SPAWN_FILE)
    writer.write_string(f"{map_name}-spawn.xml")
    writer.write_byte(OTBM_ATTR_EXT_HOUSE_FILE)
    writer.write_string(f"{map_name}-house.xml")

    # RME: OTBM_TOWNS with OTBM_TOWN children (id, name, temple x,y,z)
    # Must come BEFORE tile areas
    if towns:
        writer.start_node(OTBM_TOWNS)
        for t in towns:
            writer.start_node(OTBM_TOWN)
            writer.write_uint32(t['id'])
            writer.write_string(t['name'])
            writer.write_uint16(t['x'])
            writer.write_uint16(t['y'])
            writer.write_byte(t['z'])
            writer.end_node()
        writer.end_node()
        print(f"  ✓ Wrote {len(towns)} towns (from map.dat)")

    areas = defaultdict(list)
    for (sx, sy, z), tiles in sectors.items():
        for lx, ly, items in tiles:
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            
            # No offset - use original coordinates
            new_x = abs_x
            new_y = abs_y
            
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
    
    # FIXED: Only close MAP_DATA once (removed extra end_node)
    writer.end_node()  # Close MAP_DATA
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'wb') as f:
        f.write(b'OTBM')
        f.write(writer.get_bytes())
    
    print(f"\n✓ Map generated: {output_file}")
    print(f"  Tiles: {total_tiles:,}, Items: {total_items:,}")


# ============================================================================
# Parse houseareas.dat and houses.dat
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
                    # Area = (590,"Ankrahmun, Border",40,7)
                    # Extract the tuple part
                    content = line.split('=', 1)[1].strip()
                    content = content.strip('()')
                    
                    # Split by comma, but need to handle quoted strings
                    # Format: AreaID,"Name (may have commas)",Price,Depot
                    
                    # First, get the area ID (before first comma)
                    first_comma = content.index(',')
                    area_id = int(content[:first_comma].strip())
                    
                    # The rest after area ID
                    rest = content[first_comma+1:].strip()
                    
                    # Find the quoted name (starts with ")
                    if rest.startswith('"'):
                        # Find closing quote
                        end_quote = rest.index('"', 1)
                        # After the closing quote, we have ,Price,Depot
                        after_name = rest[end_quote+1:].strip(',').strip()
                        parts = after_name.split(',')
                        # parts[0] = Price, parts[1] = Depot
                        depot = int(parts[1].strip())
                    else:
                        # No quoted name, simpler parsing
                        parts = rest.split(',')
                        depot = int(parts[-1].strip())
                    
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
    """Generate map-house.xml from houses.dat"""
    
    print("\n" + "="*70)
    print("GENERATING HOUSES XML")
    print("="*70)
    
    area_to_depot = parse_houseareas(houseareas_path)
    houses = parse_houses_dat(houses_path)
    
    print(f"Found {len(houses)} houses")
    
    xml_lines = ['<?xml version="1.0"?>']
    xml_lines.append('<houses>')
    
    for house in houses:
        # Use ID directly from houses.dat
        house_id = house['id']
        
        # Look up area to get depot, then townid = depot + 1
        area = house.get('area', 100)
        depot = area_to_depot.get(area, 0)
        town_id = depot + 1  # Depot 0 → Town 1 (Thais), Depot 1 → Town 2 (Carlin), etc.
        
        # Use original coordinates
        entryx = house.get('entryx', 0)
        entryy = house.get('entryy', 0)
        
        attrs = [
            f'name="{house.get("name", "")}"',
            f'houseid="{house_id}"',
            f'entryx="{entryx}"',
            f'entryy="{entryy}"',
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
# Parse .mon files and build race lookup
# ============================================================================
def build_race_lookup(mon_dir):
    """Build Race → Monster Name lookup from .mon files (using filename, not Name field)"""
    race_to_name = {}
    
    mon_dir = Path(mon_dir)
    if not mon_dir.exists():
        return race_to_name
    
    for mon_file in mon_dir.glob("*.mon"):
        race_number = None
        
        # Use filename without .mon extension (this matches RME creatures.xml format)
        monster_name = mon_file.stem  # e.g., "demonskeleton.mon" → "demonskeleton"
        
        with open(mon_file, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                if line.startswith('RaceNumber'):
                    try:
                        race_number = int(line.split('=')[1].split('#')[0].strip())
                    except:
                        pass
                
                if race_number is not None:
                    race_to_name[race_number] = monster_name
                    break
    
    return race_to_name


def parse_monster_db(monster_db_path):
    """Parse monster.db file"""
    spawns = []
    
    with open(monster_db_path, 'r', encoding='latin-1', errors='ignore') as f:
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
    """Parse .npc files to extract NPC spawn data and outfit info (using Name field for display)"""
    npc_spawns = []
    npc_creatures = {}  # For creatures.xml generation
    
    npc_dir = Path(npc_dir)
    if not npc_dir.exists():
        return npc_spawns, npc_creatures
    
    for npc_file in npc_dir.glob("*.npc"):
        # Use filename for internal tracking
        npc_filename = npc_file.stem
        
        display_name = None  # Name field - this is what we'll use in XML
        home_x = home_y = home_z = None
        radius = 3
        looktype = None
        lookhead = lookbody = looklegs = lookfeet = 0
        
        with open(npc_file, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                # Parse Name field for display name (to avoid .mon conflicts)
                if line.startswith('Name') and '=' in line:
                    try:
                        display_name = line.split('=', 1)[1].strip().strip('"')
                    except:
                        pass
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
        
        # Use display_name (from Name field) to avoid .mon conflicts
        if not display_name:
            display_name = npc_filename  # Fallback to filename if no Name field
        
        # Add to spawn list if has position
        if home_x is not None:
            npc_spawns.append({
                'name': display_name,  # Use Name field
                'x': home_x,
                'y': home_y,
                'z': home_z,
                'radius': radius
            })
        
        # Add to creatures dict if has outfit
        if looktype:
            npc_creatures[display_name] = {
                'name': display_name,  # Use Name field
                'looktype': looktype,
                'lookhead': lookhead,
                'lookbody': lookbody,
                'looklegs': looklegs,
                'lookfeet': lookfeet
            }
    
    return npc_spawns, npc_creatures


def generate_spawns_xml(monster_db_path, mon_dir, npc_dir, output_path, sectors):
    """Generate map-spawn.xml from monster.db and .npc files, checking walkability"""
    
    print("\n" + "="*70)
    print("GENERATING SPAWNS XML")
    print("="*70)
    
    race_to_name = build_race_lookup(mon_dir)
    print(f"Built race lookup: {len(race_to_name)} monsters")
    
    monster_spawns = parse_monster_db(monster_db_path)
    print(f"Found {len(monster_spawns)} monster spawn entries")
    
    npc_spawns, npc_creatures = parse_npc_files(npc_dir)
    print(f"Found {len(npc_spawns)} NPC spawns")
    
    # Build walkable tile set from map
    print("Building walkable tile map from sectors...")
    walkable_tiles = load_walkable_tiles_from_sectors(sectors)
    print(f"  Found {len(walkable_tiles)} walkable tiles")
    
    # Track globally used tiles to avoid conflicts
    global_used_tiles = set()
    global_spawn_centers = set()  # Track spawn centers to avoid duplicates
    
    xml_lines = ['<?xml version="1.0"?>']
    xml_lines.append('<spawns>')
    
    total_creatures = 0
    skipped_unwalkable = 0
    
    # Process each monster.db line as one spawn
    for spawn in monster_spawns:
        race = spawn['race']
        monster_name = race_to_name.get(race)
        
        if not monster_name:
            continue
        
        center_x = spawn['x']
        center_y = spawn['y']
        z = spawn['z']
        amount = spawn['amount']
        
        # Mark this spawn center as used
        global_spawn_centers.add((center_x, center_y, z))
        
        # Place creatures and track their offsets
        creature_offsets = []
        
        # Use spiral pattern to place creatures on unique, WALKABLE tiles
        placed_count = 0
        for radius in range(50):  # Search outward from center
            if placed_count >= amount:
                break
            
            if radius == 0:
                # Try center first
                tile = (center_x, center_y, z)
                if tile not in global_used_tiles and tile in walkable_tiles:
                    global_used_tiles.add(tile)
                    creature_offsets.append((0, 0))
                    placed_count += 1
                elif tile not in walkable_tiles:
                    skipped_unwalkable += 1
            else:
                # Spiral outward
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        if placed_count >= amount:
                            break
                        
                        # Only check perimeter of current radius
                        if abs(dx) == radius or abs(dy) == radius:
                            tile = (center_x + dx, center_y + dy, z)
                            
                            # Check if walkable AND not used
                            if tile in walkable_tiles and tile not in global_used_tiles:
                                global_used_tiles.add(tile)
                                creature_offsets.append((dx, dy))
                                placed_count += 1
                            elif tile not in walkable_tiles:
                                skipped_unwalkable += 1
                    
                    if placed_count >= amount:
                        break
        
        if placed_count < amount:
            print(f"  ⚠ Warning: Could only place {placed_count}/{amount} {monster_name} at ({center_x}, {center_y}, {z}) - not enough walkable tiles")
        
        # Calculate radius as the max offset used
        if creature_offsets:
            max_offset = max(max(abs(dx), abs(dy)) for dx, dy in creature_offsets)
            calculated_radius = max(1, max_offset)  # Minimum radius 1
        else:
            calculated_radius = 1
            continue  # Skip spawn if no creatures placed
        
        # Write spawn with calculated radius
        xml_lines.append(
            f'\t<spawn centerx="{center_x}" centery="{center_y}" '
            f'centerz="{z}" radius="{calculated_radius}">'
        )
        
        for dx, dy in creature_offsets:
            xml_lines.append(
                f'\t\t<monster name="{monster_name}" x="{dx}" y="{dy}" '
                f'z="{z}" spawntime="{spawn["spawntime"]}"/>'
            )
            total_creatures += 1
        
        xml_lines.append('\t</spawn>')
    
    # Add NPC spawns (with smart offset search to avoid duplicate centers)
    npc_count = 0
    for npc in npc_spawns:
        original_center_x = npc['x']
        original_center_y = npc['y']
        z = npc['z']
        
        # Find a spawn center that isn't already used
        center_x = original_center_x
        center_y = original_center_y
        center_found = False
        
        # Check if original center is available
        if (center_x, center_y, z) not in global_spawn_centers:
            # Check if we can place NPC at this center
            tile = (center_x, center_y, z)
            if tile in walkable_tiles and tile not in global_used_tiles:
                global_spawn_centers.add((center_x, center_y, z))
                global_used_tiles.add(tile)
                center_found = True
                final_dx = 0
                final_dy = 0
        
        if not center_found:
            # Try offsetting the spawn CENTER (not just the creature)
            # Try cardinal directions: +X, -X, +Y, -Y
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                new_center_x = original_center_x + dx
                new_center_y = original_center_y + dy
                
                if (new_center_x, new_center_y, z) not in global_spawn_centers:
                    # Can we place NPC at this new center?
                    tile = (new_center_x, new_center_y, z)
                    if tile in walkable_tiles and tile not in global_used_tiles:
                        center_x = new_center_x
                        center_y = new_center_y
                        global_spawn_centers.add((center_x, center_y, z))
                        global_used_tiles.add(tile)
                        center_found = True
                        final_dx = 0
                        final_dy = 0
                        break
        
        if not center_found:
            # Spiral search for a completely new center position
            for radius in range(2, 10):
                if center_found:
                    break
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        if abs(dx) == radius or abs(dy) == radius:
                            new_center_x = original_center_x + dx
                            new_center_y = original_center_y + dy
                            
                            if (new_center_x, new_center_y, z) not in global_spawn_centers:
                                tile = (new_center_x, new_center_y, z)
                                if tile in walkable_tiles and tile not in global_used_tiles:
                                    center_x = new_center_x
                                    center_y = new_center_y
                                    global_spawn_centers.add((center_x, center_y, z))
                                    global_used_tiles.add(tile)
                                    center_found = True
                                    final_dx = 0
                                    final_dy = 0
                                    break
                        if center_found:
                            break
                    if center_found:
                        break
        
        if center_found:
            xml_lines.append(
                f'\t<spawn centerx="{center_x}" centery="{center_y}" '
                f'centerz="{z}" radius="1">'
            )
            xml_lines.append(
                f'\t\t<npc name="{npc["name"]}" x="{final_dx}" y="{final_dy}" z="{z}" spawntime="60"/>'
            )
            xml_lines.append('\t</spawn>')
            npc_count += 1
        else:
            print(f"  ⚠ Warning: Could not place NPC '{npc['name']}' near ({original_center_x}, {original_center_y}, {z}) - no available spawn centers")
    
    xml_lines.append('</spawns>')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    print(f"✓ Spawns XML generated: {total_creatures} monsters, {npc_count} NPCs")
    print(f"  ✓ All creatures placed on walkable tiles")
    if skipped_unwalkable > 0:
        print(f"  ℹ Skipped {skipped_unwalkable} non-walkable positions during placement")


# ============================================================================
# Main
# ============================================================================
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 sec_to_otbm.py <tibia-game-folder> <output-name>")
        print("\nExample:")
        print("  python3 sec_to_otbm.py ./tibia-game myworld")
        print("\nThis will generate:")
        print("  output/myworld.otbm")
        print("  output/myworld-house.xml")
        print("  output/myworld-spawn.xml")
        print("\nKeeps original Tibia coordinates (map centered around 32000,32000).")
        sys.exit(1)
    
    tibia_game_dir = Path(sys.argv[1])
    output_name = sys.argv[2]
    
    # Validate paths
    map_dir = tibia_game_dir / 'map'
    dat_dir = tibia_game_dir / 'dat'
    mon_dir = tibia_game_dir / 'mon'
    npc_dir = tibia_game_dir / 'npc'
    
    if not tibia_game_dir.exists():
        print(f"Error: {tibia_game_dir} not found!")
        sys.exit(1)
    
    if not map_dir.exists():
        print(f"Error: {map_dir} not found!")
        sys.exit(1)
    
    print("=" * 70)
    print("SEC to OTBM Converter + Houses + Spawns")
    print("=" * 70)
    print(f"\nInput:  {tibia_game_dir.absolute()}")
    print(f"Output: {output_name}")
    print("Mode:   Original Tibia coordinates (65535x65535 map)")
    print("=" * 70)
    
    output_dir = Path('output')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load sectors once (needed for both map and spawn generation)
    sectors = load_all_sectors(map_dir)
    
    if not sectors:
        print("\n❌ No valid sectors found!")
        sys.exit(1)
    
    # Temple positions from moveuse.dat Hometeleporters (SetStart x,y,z); fallback to map.dat Mark
    moveuse_dat = dat_dir / 'moveuse.dat'
    temple_positions = parse_temples_from_moveuse(moveuse_dat)
    if temple_positions:
        print(f"\n✓ Loaded temple positions for {len(temple_positions)} towns from moveuse.dat (Hometeleporters)")
    # Towns: depots from map.dat, temple from moveuse (preferred) or map.dat Mark
    map_dat = dat_dir / 'map.dat'
    towns = parse_map_dat(map_dat, temple_positions=temple_positions)
    if towns:
        print(f"✓ Loaded {len(towns)} towns (depots from map.dat, temples from moveuse.dat)")
    elif map_dat.exists():
        print(f"\n⚠ Warning: map.dat found but no towns parsed")
    else:
        print(f"\n⚠ Warning: map.dat not found, map will have no towns")
    
    # Convert map
    convert_map_to_otbm(
        sectors,
        output_dir / f'{output_name}.otbm',
        output_name,
        towns=towns
    )
    
    # Generate houses XML
    houses_dat = dat_dir / 'houses.dat'
    houseareas_dat = dat_dir / 'houseareas.dat'
    
    if houses_dat.exists() and houseareas_dat.exists():
        generate_houses_xml(
            houses_dat,
            houseareas_dat,
            output_dir / f'{output_name}-house.xml'
        )
    else:
        print(f"\n⚠ Skipping houses (missing {houses_dat} or {houseareas_dat})")
    
    # Generate spawns XML (reuse npc_spawns and npc_creatures from earlier)
    monster_db = dat_dir / 'monster.db'
    
    if monster_db.exists():
        generate_spawns_xml(
            monster_db,
            mon_dir,
            npc_dir,
            output_dir / f'{output_name}-spawn.xml',
            sectors
        )
    else:
        print(f"\n⚠ Skipping spawns (missing {monster_db})")
    
    print("\n" + "="*70)
    print("✅ CONVERSION COMPLETE!")
    print("="*70)
    print(f"\nGenerated files in {output_dir.absolute()}:")
    print(f"  → {output_name}.otbm")
    if houses_dat.exists():
        print(f"  → {output_name}-house.xml")
    if monster_db.exists():
        print(f"  → {output_name}-spawn.xml")
    print()


if __name__ == '__main__':
    main()
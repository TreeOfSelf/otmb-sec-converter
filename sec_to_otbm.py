#!/usr/bin/env python3
"""
SEC to OTBM Converter + Houses + Spawns Generator
Converts CipSoft .sec map files to OTBM and generates auxiliary XMLs

Usage: python3 sec_to_otbm.py <tibia-game-folder> <output-name> [--no-offset]

Example:
  python3 sec_to_otbm.py ./tibia-game myworld

Output:
  output/myworld.otbm
  output/myworld-houses.xml
  output/myworld-spawns.xml
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
    print("CONVERTING MAP TO OTBM")
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
        # Use ID directly from houses.dat
        house_id = house['id']
        
        # Look up area to get depot, then townid = depot + 2
        area = house.get('area', 100)
        depot = area_to_depot.get(area, 0)
        town_id = depot + 2  # Depot 0 → Town 2 (Thais), Depot 1 → Town 3 (Carlin), etc.
        
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
# Parse .mon files and build race lookup
# ============================================================================
def build_race_lookup(mon_dir):
    """Build Race → Monster Name lookup from .mon files"""
    race_to_name = {}
    
    mon_dir = Path(mon_dir)
    if not mon_dir.exists():
        return race_to_name
    
    for mon_file in mon_dir.glob("*.mon"):
        race_number = None
        name = None
        
        with open(mon_file, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                if line.startswith('RaceNumber'):
                    try:
                        race_number = int(line.split('=')[1].split('#')[0].strip())
                    except:
                        pass
                elif line.startswith('Name'):
                    try:
                        name = line.split('=', 1)[1].strip().strip('"')
                    except:
                        pass
                
                if race_number is not None and name is not None:
                    race_to_name[race_number] = name
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
    """Parse .npc files to extract NPC spawn data"""
    npc_spawns = []
    
    npc_dir = Path(npc_dir)
    if not npc_dir.exists():
        return npc_spawns
    
    for npc_file in npc_dir.glob("*.npc"):
        name = None
        home_x = home_y = home_z = None
        radius = 3
        
        with open(npc_file, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                if line.startswith('Name'):
                    try:
                        name = line.split('=', 1)[1].strip().strip('"')
                    except:
                        pass
                elif line.startswith('Home'):
                    try:
                        coords = line.split('=')[1].strip().strip('[]')
                        parts = coords.split(',')
                        home_x = int(parts[0])
                        home_y = int(parts[1])
                        home_z = int(parts[2])
                    except:
                        pass
                elif line.startswith('Radius'):
                    try:
                        radius = int(line.split('=')[1].strip())
                    except:
                        pass
        
        if name and home_x is not None:
            npc_spawns.append({
                'name': name,
                'x': home_x,
                'y': home_y,
                'z': home_z,
                'radius': radius
            })
    
    return npc_spawns


def generate_spawns_xml(monster_db_path, mon_dir, npc_dir, output_path):
    """Generate map-spawns.xml from monster.db and .npc files"""
    
    print("\n" + "="*70)
    print("GENERATING SPAWNS XML")
    print("="*70)
    
    race_to_name = build_race_lookup(mon_dir)
    print(f"Built race lookup: {len(race_to_name)} monsters")
    
    monster_spawns = parse_monster_db(monster_db_path)
    print(f"Found {len(monster_spawns)} monster spawn entries")
    
    npc_spawns = parse_npc_files(npc_dir)
    print(f"Found {len(npc_spawns)} NPC spawns")
    
    # Group monster spawns by (x, y, z, radius) to consolidate them
    spawn_groups = defaultdict(list)
    for spawn in monster_spawns:
        race = spawn['race']
        monster_name = race_to_name.get(race)
        
        if not monster_name:
            continue
        
        # Group by location
        key = (spawn['x'], spawn['y'], spawn['z'], spawn['radius'])
        
        # Add each monster from amount
        for i in range(spawn['amount']):
            spawn_groups[key].append({
                'name': monster_name,
                'spawntime': spawn['spawntime']
            })
    
    xml_lines = ['<?xml version="1.0"?>']
    xml_lines.append('<spawns>')
    
    # Write grouped monster spawns
    for (x, y, z, radius), monsters in sorted(spawn_groups.items()):
        xml_lines.append(f'\t<spawn centerx="{x}" centery="{y}" centerz="{z}" radius="{radius}">')
        
        # Add each monster with small offset
        for idx, monster in enumerate(monsters):
            # Simple offset pattern: spread monsters in a small area
            offset_x = idx % 3 - 1  # -1, 0, 1, -1, 0, 1, ...
            offset_y = (idx // 3) % 3 - 1
            
            xml_lines.append(
                f'\t\t<monster name="{monster["name"]}" x="{offset_x}" y="{offset_y}" '
                f'z="{z}" spawntime="{monster["spawntime"]}"/>'
            )
        
        xml_lines.append('\t</spawn>')
    
    # Add NPC spawns
    for npc in npc_spawns:
        xml_lines.append(
            f'\t<spawn centerx="{npc["x"]}" centery="{npc["y"]}" '
            f'centerz="{npc["z"]}" radius="{npc["radius"]}">'
        )
        xml_lines.append(
            f'\t\t<npc name="{npc["name"]}" x="0" y="0" z="{npc["z"]}" spawntime="60"/>'
        )
        xml_lines.append('\t</spawn>')
    
    xml_lines.append('</spawns>')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(xml_lines))
    
    total_monsters = sum(len(m) for m in spawn_groups.values())
    print(f"✓ Spawns XML generated: {total_monsters} monsters in {len(spawn_groups)} locations, {len(npc_spawns)} NPCs")


# ============================================================================
# Main
# ============================================================================
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 sec_to_otbm.py <tibia-game-folder> <output-name> [--no-offset]")
        print("\nExample:")
        print("  python3 sec_to_otbm.py ./tibia-game myworld")
        print("  python3 sec_to_otbm.py ./tibia-game myworld --no-offset")
        print("\nThis will generate:")
        print("  output/myworld.otbm")
        print("  output/myworld-houses.xml")
        print("  output/myworld-spawns.xml")
        sys.exit(1)
    
    tibia_game_dir = Path(sys.argv[1])
    output_name = sys.argv[2]
    apply_offset = '--no-offset' not in sys.argv
    
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
    print("=" * 70)
    
    output_dir = Path('output')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert map
    convert_map_to_otbm(
        map_dir,
        output_dir / f'{output_name}.otbm',
        output_name,
        apply_offset
    )
    
    # Generate houses XML
    houses_dat = dat_dir / 'houses.dat'
    houseareas_dat = dat_dir / 'houseareas.dat'
    
    if houses_dat.exists() and houseareas_dat.exists():
        generate_houses_xml(
            houses_dat,
            houseareas_dat,
            output_dir / f'{output_name}-houses.xml'
        )
    else:
        print(f"\n⚠ Skipping houses (missing {houses_dat} or {houseareas_dat})")
    
    # Generate spawns XML
    monster_db = dat_dir / 'monster.db'
    
    if monster_db.exists():
        generate_spawns_xml(
            monster_db,
            mon_dir,
            npc_dir,
            output_dir / f'{output_name}-spawns.xml'
        )
    else:
        print(f"\n⚠ Skipping spawns (missing {monster_db})")
    
    print("\n" + "="*70)
    print("✅ CONVERSION COMPLETE!")
    print("="*70)
    print(f"\nGenerated files in {output_dir.absolute()}:")
    print(f"  → {output_name}.otbm")
    if houses_dat.exists():
        print(f"  → {output_name}-houses.xml")
    if monster_db.exists():
        print(f"  → {output_name}-spawns.xml")
    print()


if __name__ == '__main__':
    main()
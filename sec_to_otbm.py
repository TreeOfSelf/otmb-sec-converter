#!/usr/bin/env python3
import sys
import json
from pathlib import Path
from collections import defaultdict

# --- Constants ---
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
    """Calculate transformed map bounds after applying offset."""
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


def convert_sec_to_otbm(sec_dir, output_file, map_name="Converted Map", apply_offset=True):
    """Convert .sec files to OTBM format (CipSoft TypeIDs preserved)"""
    
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    print("Converting .sec → OTBM (CipSoft mode)")
    print("  TypeIDs will be preserved (no translation)")
    
    # Load all sectors
    sectors = load_all_sectors(sec_dir)
    
    if not sectors:
        print("Error: No valid sectors found!")
        return
    
    # Calculate offset to move map to origin
    offset_x, offset_y = calculate_offset(sectors)
    
    if apply_offset:
        print(f"\nApplying coordinate offset:")
        print(f"  X offset: -{offset_x}")
        print(f"  Y offset: -{offset_y}")
        print(f"  (Moving map from ({offset_x}, {offset_y}) to (0, 0))")
    else:
        print(f"\nNOT applying offset (using original coordinates)")
        offset_x = 0
        offset_y = 0
    
    # Calculate resulting map dimensions for OTBM header
    min_x, min_y, max_x, max_y = calculate_bounds(sectors, offset_x, offset_y)
    width = max_x - min_x + 1
    height = max_y - min_y + 1

    # OTBM header stores dimensions as uint16
    width = max(1, min(65535, width))
    height = max(1, min(65535, height))

    print(f"\nMap bounds after transform:")
    print(f"  X: {min_x}..{max_x} (width={width})")
    print(f"  Y: {min_y}..{max_y} (height={height})")

    header = build_otbm_header(width, height)
    
    # Start writing OTBM
    writer = OTBMWriter()
    writer.data.extend(header)
    
    print("\nWriting OTBM structure...")
    
    # --- Map Data Node ---
    writer.start_node(OTBM_MAP_DATA)
    writer.write_byte(OTBM_ATTR_DESCRIPTION)
    writer.write_string(map_name)
    
    # Group tiles by area WITH OFFSET
    areas = defaultdict(list)
    for (sx, sy, z), tiles in sectors.items():
        for lx, ly, items in tiles:
            # Calculate absolute coordinates
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            
            # Apply offset
            new_x = abs_x - offset_x
            new_y = abs_y - offset_y
            
            # Calculate new area and local coords
            area_x = new_x & 0xFF00  # Round down to nearest 256
            area_y = new_y & 0xFF00
            local_x = new_x & 0xFF   # Last 8 bits
            local_y = new_y & 0xFF
            
            areas[(area_x, area_y, z)].append({
                'x': local_x,
                'y': local_y,
                'items': items
            })
    
    print(f"  Writing {len(areas)} tile areas...")
    
    total_tiles = 0
    total_items = 0
    
    # Write each area
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
            
            converted_items = []
            for item_data in items:
                type_id = item_data['id']
                
                converted_item = {'id': type_id}
                for key, value in item_data.items():
                    if key != 'id':
                        converted_item[key] = value
                
                converted_items.append(converted_item)
            
            if not converted_items:
                continue
            
            writer.start_node(OTBM_TILE)
            writer.write_byte(tile['x'])
            writer.write_byte(tile['y'])
            
            for item_data in converted_items:
                writer.start_node(OTBM_ITEM)
                writer.write_uint16(item_data['id'])
                writer.end_node()
                total_items += 1
            
            writer.end_node()  # End TILE
        
        writer.end_node()  # End TILE_AREA
        
        if idx % 200 == 0:
            print(f"  Progress: {idx}/{len(areas)} areas...")
    
    writer.end_node()  # End MAP_DATA
    writer.end_node()  # End MAP_HEADER
    
    # Write to file
    print(f"\nWriting to {output_file}...")
    with open(output_file, 'wb') as f:
        # Write OTBM magic identifier (required by RME)
        f.write(b'OTBM')
        f.write(writer.get_bytes())
    
    print(f"\nConversion complete!")
    print(f"  Total tiles: {total_tiles:,}")
    print(f"  Total items: {total_items:,}")
    
    file_size = Path(output_file).stat().st_size
    print(f"\nOutput file: {output_file} ({file_size:,} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 sec_to_otbm.py <sec_folder> [output_filename] [--no-offset]")
        print("\nExample:")
        print("  python3 sec_to_otbm.py ./sectors/")
        print("  python3 sec_to_otbm.py ./sectors/ map.otbm --no-offset")
        print("\nNotes:")
        print("  - Uses CipSoft TypeIDs directly (OTB ID 100)")
        print("  - Output goes to output/ folder (created if needed)")
        print("  - Default output filename: converted.otbm")
        print("  - By default, normalizes coordinates to (0, 0)")
        sys.exit(1)
    
    sec_dir = sys.argv[1]
    output_filename = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else "converted.otbm"
    output_file = f"output/{output_filename}"
    apply_offset = '--no-offset' not in sys.argv
    
    convert_sec_to_otbm(sec_dir, output_file, "Converted Map", apply_offset)
#!/usr/bin/env python3
import sys
import json
import struct
from pathlib import Path
from collections import defaultdict

# --- Constants ---
SECTOR_SIZE = 32
NODE_ESC = 0xFD
NODE_INIT = 0xFE
NODE_TERM = 0xFF

# OTBM Node Types
OTBM_TILE_AREA = 4
OTBM_TILE = 5
OTBM_ITEM = 6
OTBM_HOUSETILE = 10
OTBM_ATTR_ITEM = 0x09

def read_byte_escape(data, pos):
    """Read byte handling escape sequences"""
    if pos >= len(data):
        return None, pos
    b = data[pos]
    pos += 1
    if b == NODE_ESC and pos < len(data):
        b = data[pos]
        pos += 1
    return b, pos

def read_uint16_escape(data, pos):
    """Read uint16 with escape handling"""
    b1, pos = read_byte_escape(data, pos)
    if b1 is None:
        return None, pos
    b2, pos = read_byte_escape(data, pos)
    if b2 is None:
        return None, pos
    return b1 | (b2 << 8), pos


def load_valid_ids(srv_file):
    """Parses objects.srv to find all valid TypeIDs"""
    print(f"Loading valid IDs from {srv_file}...")
    valid_ids = set()
    
    with open(srv_file, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            # Look for "TypeID = 1234"
            if line.startswith("TypeID") and "=" in line:
                try:
                    # Split by '=' first, then remove comments with '#'
                    val_part = line.split("=")[1].split("#")[0].strip()
                    type_id = int(val_part)
                    valid_ids.add(type_id)
                except ValueError:
                    continue
                    
    print(f"  Found {len(valid_ids)} valid object definitions in server.")
    return valid_ids

def convert_otbm_to_secs(otbm_file, out_dir, srv_file):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print("Converting OTBM → .sec (CipSoft mode)")
    print("  TypeIDs will be preserved (no translation)\n")
    
    # Load valid TypeIDs from objects.srv
    valid_server_ids = load_valid_ids(srv_file)

    # 2. Read the Map
    with open(otbm_file, 'rb') as f:
        data = f.read()

    print(f"\nReading OTBM file: {len(data):,} bytes")
    print("Converting Map (with Safety Checks)...")

    buckets = defaultdict(list)
    i = 4
    depth = 0
    
    tile_count = 0
    area_count = 0
    converted_items = 0
    
    # Error Counters
    skipped_invalid_id = 0
    invalid_unique = set()

    # Stack: (depth, sx, sy, z, lx, ly, ground_id, items[])
    tile_stack = [] 

    # Helper function to validate TypeID
    def get_valid_type_id(type_id):
        nonlocal skipped_invalid_id
        
        # Validate against objects.srv
        if type_id not in valid_server_ids:
            skipped_invalid_id += 1
            invalid_unique.add(type_id)
            return None
            
        return type_id

    while i < len(data):
        b = data[i]
        
        if b == NODE_ESC:
            i += 2
            continue
        
        if b == NODE_INIT:
            depth += 1
            i += 1
            if i >= len(data): break
            
            node_type = data[i]
            i += 1
            
            if node_type == OTBM_TILE_AREA:
                area_count += 1
                base_x, i = read_uint16_escape(data, i)
                base_y, i = read_uint16_escape(data, i)
                z, i = read_byte_escape(data, i)
                current_area = (base_x, base_y, z)
                
                if area_count % 500 == 0:
                     print(f" Processing Area {area_count}... (Tiles: {tile_count})")
            
            elif node_type in [OTBM_TILE, OTBM_HOUSETILE] and current_area:
                tile_x, i = read_byte_escape(data, i)
                tile_y, i = read_byte_escape(data, i)
                
                base_x, base_y, z = current_area
                abs_x = base_x + tile_x
                abs_y = base_y + tile_y
                
                sx = abs_x // SECTOR_SIZE
                sy = abs_y // SECTOR_SIZE
                lx = abs_x - sx * SECTOR_SIZE
                ly = abs_y - sy * SECTOR_SIZE
                
                ground_id_real = None
                
                # Peek for attributes (Ground ID)
                temp_i = i
                while temp_i < len(data):
                    if data[temp_i] == NODE_ESC:
                        temp_i += 2
                        continue
                    if data[temp_i] in [NODE_INIT, NODE_TERM]:
                        break
                    if data[temp_i] == OTBM_ATTR_ITEM:
                        ground_type_id, _ = read_uint16_escape(data, temp_i + 1)
                        # Validate Ground
                        ground_id_real = get_valid_type_id(ground_type_id)
                        if ground_id_real: converted_items += 1
                        break
                    temp_i += 1
                
                tile_stack.append((depth, sx, sy, z, lx, ly, ground_id_real, []))

            elif node_type == OTBM_ITEM:
                item_type_id, i = read_uint16_escape(data, i)
                
                if tile_stack and depth > tile_stack[-1][0]:
                    # Validate Item
                    valid_type_id = get_valid_type_id(item_type_id)
                    
                    if valid_type_id:
                        tile_stack[-1][7].append(valid_type_id)
                        converted_items += 1

        elif b == NODE_TERM:
            if tile_stack and depth == tile_stack[-1][0]:
                tile_depth, sx, sy, z, lx, ly, ground_id, items = tile_stack.pop()
                
                contents = []
                if ground_id:
                    contents.append(ground_id)
                contents.extend(items)
                
                # Only write tile if it has content
                if contents:
                    buckets[(sx, sy, z)].append((lx, ly, contents))
                    tile_count += 1
            
            depth -= 1
            i += 1
        else:
            i += 1

    print(f"\nConversion complete!")
    print(f"  Total tiles: {tile_count:,}")
    print(f"  Converted items: {converted_items:,}")
    print(f"  Skipped (Invalid TypeIDs): {skipped_invalid_id:,}")
    
    # Logs
    if invalid_unique:
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / 'invalid_typeids.log'
        with open(log_file, "w") as f:
            f.write(f"--- Invalid TypeIDs (Not in objects.srv) ---\n")
            f.write(f"{sorted(list(invalid_unique))}\n")
        print(f"  Invalid IDs logged to {log_file}")

    print(f"\nWriting {len(buckets)} sector files to {out_dir}...")

    for (sx, sy, z), records in buckets.items():
        fname = out_dir / f"{sx:04d}-{sy:04d}-{z:02d}.sec"
        with open(fname, "w", encoding="utf-8") as f:
            f.write("# Tibia - graphical Multi-User-Dungeon\n")
            f.write(f"# Data for sector {sx}/{sy}/{z}\n\n")
            f.write("# SectorFormat=TextDump\n")
            f.write("# FormatVersion=1\n\n")
            f.write(f"# SectorCoords: {sx} {sy} {z}\n\n")
            
            for lx, ly, contents in records:
                # Comma-space separated, strictly valid IDs only
                ids_str = ", ".join(str(c) for c in contents)
                f.write(f"{lx}-{ly}: Content={{{ids_str}}}\n")

    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 otbm_to_sec.py <map.otbm> <output_folder> <objects.srv>")
        print("\nExample:")
        print("  python3 otbm_to_sec.py map.otbm ./sectors assets/objects.srv")
        print("\nNotes:")
        print("  - Uses CipSoft TypeIDs directly (no translation)")
        print("  - Validates TypeIDs against objects.srv")
        sys.exit(1)
    
    otbm_file = sys.argv[1]
    output_folder = sys.argv[2]
    objects_srv = sys.argv[3]
    
    convert_otbm_to_secs(otbm_file, output_folder, objects_srv)
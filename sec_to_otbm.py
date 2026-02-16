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
import re
import sys
from pathlib import Path
from collections import defaultdict

# First-seen logging: when we see a new type (charge, container, unknown key), log coords + .sec line
# Attributes we skip when logging (we don't store them; server gives default)
_DEBUG_LOG_SKIP = frozenset({'remainingexpiretime', 'savedexpiretime', 'remaininguses'})
_debug_attributes_entries = []  # (type_name, context) for debug_attributes.log
_LOGS_DIR = Path(__file__).resolve().parent / "logs"

# All 18 server instance attributes (enums.hh INSTANCEATTRIBUTE, objects.cc InstanceAttributeNames):
#   0 Content          -> structure (nested Content={}); not a key=value
#   1 ChestQuestNumber -> actionid
#   2 Amount           -> count
#   3 KeyNumber        -> uniqueid  (key: UID = key number; RME shows both AID/UID; convention: key UID matches keyhole AID)
#   4 KeyholeNumber    -> actionid  (keyhole: AID = lock number; server moveuse.cc compares KeyNumber == KeyholeNumber)
#   5 Level            -> actionid (level doors)
#   6 DoorQuestNumber  -> actionid
#   7 DoorQuestValue   -> uniqueid
#   8 Charges          -> rune charges
#   9 String           -> text (OTBM_ATTR_TEXT)
#  10 Editor           -> stored in item_data; not written to OTBM (RME OTBM_ATTR_DESC may differ)
#  11 ContainerLiquidType -> liquid_type -> count (mapped)
#  12 PoolLiquidType   -> liquid_type -> count (mapped)
#  13 AbsTeleportDestination -> teleport_dest -> OTBM_ATTR_TELE_DEST
#  14 Responsible      -> stored in item_data; not written to OTBM (no RME equivalent)
#  15 RemainingExpireTime -> skipped (server default)
#  16 SavedExpireTime  -> skipped (server default)
#  17 RemainingUses    -> skipped (server default)
KNOWN_ITEM_TYPES = frozenset({
    "container", "string",
    "amount", "poolliquidtype", "containerliquidtype",
    "chestquestnumber", "doorquestnumber", "keyholenumber", "keynumber", "doorquestvalue",
    "charges", "level", "remainingexpiretime", "savedexpiretime", "remaininguses",
    "absteleportdestination",
    "editor", "responsible",
})


def _log_new_type(type_name, context):
    """Record occurrence for debug_attributes.log (all occurrences, grouped by type). Skips remainingexpiretime/savedexpiretime/remaininguses."""
    if type_name in _DEBUG_LOG_SKIP:
        return
    _debug_attributes_entries.append((type_name, context))

# ============================================================================
# OTBM Constants - ALL from RME source (DO NOT GUESS)
# ============================================================================
# Copy-paste reference: RME/source/iomap_otbm.h, RME/source/tile.h
# Root node: iomap_otbm.cpp saveMap() uses f.addNode(0) for root.
# ============================================================================
SECTOR_SIZE = 32
NODE_ESC = 0xFD
NODE_INIT = 0xFE
NODE_TERM = 0xFF

# Root (not in enum; RME writes 0)
OTBM_MAP_HEADER = 0x00

# iomap_otbm.h enum OTBM_NodeTypes_t
OTBM_ROOTV1 = 1
OTBM_MAP_DATA = 2          # WE USE
OTBM_ITEM_DEF = 3
OTBM_TILE_AREA = 4         # WE USE
OTBM_TILE = 5              # WE USE
OTBM_ITEM = 6              # WE USE
OTBM_TILE_SQUARE = 7
OTBM_TILE_REF = 8
OTBM_SPAWNS = 9
OTBM_SPAWN_AREA = 10
OTBM_MONSTER = 11
OTBM_TOWNS = 12            # WE USE
OTBM_TOWN = 13             # WE USE
OTBM_HOUSETILE = 14        # WE USE
OTBM_WAYPOINTS = 15
OTBM_WAYPOINT = 16

# iomap_otbm.h enum OTBM_ItemAttribute
OTBM_ATTR_DESCRIPTION = 1      # WE USE (map desc + spawn/house filenames)
OTBM_ATTR_EXT_FILE = 2
OTBM_ATTR_TILE_FLAGS = 3       # WE USE
OTBM_ATTR_ACTION_ID = 4        # WE USE
OTBM_ATTR_UNIQUE_ID = 5        # WE USE
OTBM_ATTR_TEXT = 6             # WE USE
OTBM_ATTR_DESC = 7
OTBM_ATTR_TELE_DEST = 8
OTBM_ATTR_ITEM = 9
OTBM_ATTR_DEPOT_ID = 10
OTBM_ATTR_EXT_SPAWN_FILE = 11  # WE USE
OTBM_ATTR_RUNE_CHARGES = 12
OTBM_ATTR_EXT_HOUSE_FILE = 13  # WE USE
OTBM_ATTR_HOUSEDOORID = 14     # RME: house doors only; we don't set from .sec (quest value → Unique ID)
OTBM_ATTR_COUNT = 15           # WE USE

# Server (Hardcore-Tibia-Server enums.hh LiquidType) -> RME (item.h SplashType)
# So ContainerLiquidType=9 (Milk) writes RME 6 (LIQUID_MILK) and shows as Milk in editor.
SERVER_LIQUID_TO_RME = {
    0: 0,   # None
    1: 1,   # Water
    2: 15,  # Wine
    3: 3,   # Beer
    4: 19,  # Mud
    5: 2,   # Blood
    6: 4,   # Slime
    7: 11,  # Oil
    8: 13,  # Urine
    9: 6,   # Milk
    10: 7,  # Manafluid
    11: 10, # Lifefluid
    12: 5,  # Lemonade
}
# Server packs absolute teleport coords: UnpackAbsoluteCoordinate in moveuse.cc
def _unpack_absolute_coordinate(packed):
    """Packed (signed int) -> (x, y, z). Server: x = ((p>>18)&0x3FFF)+24576, y = ((p>>4)&0x3FFF)+24576, z = p&0xF."""
    p = packed & 0xFFFFFFFF
    x = ((p >> 18) & 0x3FFF) + 24576
    y = ((p >> 4) & 0x3FFF) + 24576
    z = p & 0xF
    return (x, y, z)

OTBM_ATTR_DURATION = 16
OTBM_ATTR_DECAYING_STATE = 17
OTBM_ATTR_WRITTENDATE = 18
OTBM_ATTR_WRITTENBY = 19
OTBM_ATTR_SLEEPERGUID = 20
OTBM_ATTR_SLEEPSTART = 21
OTBM_ATTR_CHARGES = 22         # WE USE
OTBM_ATTR_EXT_SPAWN_NPC_FILE = 23
OTBM_ATTR_PODIUMOUTFIT = 40
OTBM_ATTR_TIER = 41
OTBM_ATTR_ATTRIBUTE_MAP = 128

# tile.h - map flags (stored in OTBM tile; first group only, not Internal/stat flags)
TILESTATE_NONE = 0x0000
TILESTATE_PROTECTIONZONE = 0x0001   # WE USE
TILESTATE_DEPRECATED = 0x0002       # Reserved
TILESTATE_NOPVP = 0x0004           # WE USE
TILESTATE_NOLOGOUT = 0x0008        # WE USE
TILESTATE_PVPZONE = 0x0010         # WE USE
TILESTATE_REFRESH = 0x0020         # WE USE


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
        for tile_entry in tiles:
            lx, ly = tile_entry[0], tile_entry[1]
            items = tile_entry[3] if len(tile_entry) == 4 else tile_entry[2]
            if items:  # Has items, likely has ground
                abs_x = sx * SECTOR_SIZE + lx
                abs_y = sy * SECTOR_SIZE + ly
                walkable_tiles.add((abs_x, abs_y, z))
    
    return walkable_tiles


# ============================================================================
# Parse .sec files
# ============================================================================
def _parse_sec_tile_flags(rest_before_content):
    """Parse tile flags from the part before Content=. Returns uint32 flags for OTBM.
    Real .sec examples: 'Refresh, ProtectionZone, Content={...}' (1023-0989-06.sec), etc. See docs/SEC_OTBM_DATA_CHECKLIST.md."""
    flags = 0
    s = rest_before_content
    if 'ProtectionZone' in s:
        flags |= TILESTATE_PROTECTIONZONE
    if 'Refresh' in s:
        flags |= TILESTATE_REFRESH
    if 'NoPvp' in s:
        flags |= TILESTATE_NOPVP
    if 'NoLogout' in s:
        flags |= TILESTATE_NOLOGOUT
    if 'PvpZone' in s:
        flags |= TILESTATE_PVPZONE
    return flags


def _parse_sec_content_list(content_str, context=None):
    """
    Parse Content={...} inner string into list of item specs.
    Split by comma only at top level: not inside String="..." (respects escaped \\ and \\")
    and not inside nested Content={} (brace-matched).
    Container items have nested Content={...}; we parse recursively into item_data['content'].
    context: optional dict with sec_file, lx, ly, line for debug_attributes.log (all occurrences, by type).
    """
    items = []
    segments = []
    i = 0
    start = 0
    in_string = False
    escape = False
    depth = 0
    while i < len(content_str):
        c = content_str[i]
        if escape:
            escape = False
            i += 1
            continue
        if in_string:
            if c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if content_str[i:i+8] == 'String="' and i + 8 <= len(content_str):
            in_string = True
            i += 8
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        elif c == ',' and depth == 0 and not in_string:
            segments.append(content_str[start:i].strip())
            start = i + 1
        i += 1
    if start < len(content_str):
        segments.append(content_str[start:].strip())
    for spec in segments:
        if spec:
            _append_item_from_spec(items, spec, context)
    return items


def _append_item_from_spec(items, spec, context=None):
    """Parse one item spec (e.g. '2816', '2816 String="text"', '2434 Content={3124}').
    Nested Content={...} is parsed recursively and stored as item_data['content'].
    When context is set, each attribute/container/string occurrence is recorded for logs/debug_attributes.log (sorted by type).
    Order matters: extract Content={...} (brace-matched) BEFORE String="...", else the string truncation loses the closing }} and nested item ids (e.g. 2822) can be mis-parsed."""
    string_val = None
    # Extract nested Content={...} (brace-matched) FIRST so we don't truncate at String=" and lose the closing braces.
    nested_content_str = None
    if ' Content={' in spec:
        start_marker = ' Content={'
        pos = spec.index(start_marker)
        inner_start = pos + len(start_marker)
        depth = 1
        i = inner_start
        while i < len(spec) and depth > 0:
            if spec[i] == '{':
                depth += 1
            elif spec[i] == '}':
                depth -= 1
            i += 1
        nested_content_str = spec[inner_start:i - 1]
        spec = spec[:pos].strip()
        if context is not None:
            _log_new_type("container", context)
    if 'String="' in spec:
        idx = spec.find('String="')
        head = spec[:idx].strip().rstrip(',')
        rest = spec[idx + 8:]
        end = 0
        i = 0
        while i < len(rest):
            if rest[i] == '\\':
                # Include escaped pair in content; if string ends with \", we never see a bare " so set end here
                end = i + 2
                i += 2
                continue
            if rest[i] == '"':
                # Only treat as closing delimiter when not escaped (\" is part of content)
                if i == 0 or rest[i - 1] != '\\':
                    end = i
                    break
            i += 1
        # If we consumed rest without finding a closing " (e.g. segment ended with \"), use all of rest
        if end == 0 and i > 0:
            end = i
        string_val = rest[:end].replace('\\n', '\n').replace('\\"', '"')
        spec = head
    parts = spec.split()
    if not parts:
        return
    try:
        item_id = int(parts[0])
    except (ValueError, IndexError):
        return
    item_data = {'id': item_id}
    if string_val is not None:
        item_data['text'] = string_val
        if context is not None:
            _log_new_type("string", context)
    if nested_content_str is not None:
        item_data['content'] = _parse_sec_content_list(nested_content_str, context)
    for part in parts[1:]:
        if '=' in part and 'String=' not in part and 'Content=' not in part:
            key, value = part.split('=', 1)
            key = key.lower()
            if context is not None:
                _log_new_type(key, context)
            try:
                v = int(value)
            except ValueError:
                continue
            if key in ('chestquestnumber', 'doorquestnumber', 'keyholenumber', 'level'):
                item_data['actionid'] = v  # level = required level for level doors (Gate of Expertise); RME has no Level attr, Action ID is conventional
            elif key == 'keynumber':
                item_data['uniqueid'] = v
            elif key == 'doorquestvalue':
                item_data['uniqueid'] = v  # RME disables "Door ID" for non-house tiles; Unique ID is visible for all doors
            elif key == 'amount':
                item_data['count'] = v
            elif key in ('poolliquidtype', 'containerliquidtype'):
                item_data['liquid_type'] = v
            elif key == 'charges':
                item_data['charges'] = v
            elif key == 'absteleportdestination':
                item_data['teleport_dest'] = _unpack_absolute_coordinate(v)  # RME OTBM_ATTR_TELE_DEST = (x, y, z)
            elif key in ('remainingexpiretime', 'savedexpiretime', 'remaininguses'):
                pass  # skip: server gives default (full TotalExpireTime / TotalUses) on load when omitted
            else:
                item_data[key] = v
    items.append(item_data)


def parse_sec_file(sec_file):
    """Parse a single .sec file and return tiles with items and tile flags."""
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
                
                if 'Content={' not in rest:
                    continue
                
                rest_before_content = rest.split('Content={', 1)[0].strip()
                map_flags = _parse_sec_tile_flags(rest_before_content)
                
                try:
                    content_part = rest.split('Content={', 1)[1]
                    # Brace-matched extract: do not truncate at first '}' (nested Content={}).
                    depth = 1
                    i = 0
                    in_str = False
                    escape = False
                    while i < len(content_part) and depth > 0:
                        if escape:
                            escape = False
                            i += 1
                            continue
                        if in_str:
                            if content_part[i] == '\\':
                                escape = True
                            elif content_part[i] == '"':
                                in_str = False
                            i += 1
                            continue
                        if content_part[i:i+8] == 'String="' and i + 8 <= len(content_part):
                            in_str = True
                            i += 8
                            continue
                        if content_part[i] == '{':
                            depth += 1
                        elif content_part[i] == '}':
                            depth -= 1
                        i += 1
                    if depth != 0:
                        continue
                    content_str = content_part[:i - 1]
                except IndexError:
                    continue
                    
                if not content_str.strip():
                    continue
                
                # Absolute coords from sector filename (e.g. 0998-0990-05.sec) + tile (lx, ly)
                try:
                    stem = Path(sec_file).stem
                    parts = stem.split("-")
                    if len(parts) >= 3:
                        sx, sy, sz = int(parts[0]), int(parts[1]), int(parts[2])
                        abs_x = sx * SECTOR_SIZE + lx
                        abs_y = sy * SECTOR_SIZE + ly
                    else:
                        abs_x = abs_y = sz = None
                except (ValueError, IndexError):
                    abs_x = abs_y = sz = None
                
                context = {
                    "sec_file": str(sec_file),
                    "lx": lx,
                    "ly": ly,
                    "x": abs_x,
                    "y": abs_y,
                    "z": sz,
                    "line": line,
                }
                items = _parse_sec_content_list(content_str, context)
                
                if items:
                    tiles.append((lx, ly, map_flags, items))
                    
            except (ValueError, IndexError):
                continue
    
    return tiles


def _init_debug_attributes_log():
    """Clear debug attribute entries for this run."""
    global _debug_attributes_entries
    _debug_attributes_entries = []


def _write_debug_attributes_log():
    """Write all collected attribute occurrences to logs/debug_attributes.log, sorted by type."""
    if not _debug_attributes_entries:
        return
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOGS_DIR / "debug_attributes.log"
    lines = []
    for type_name, context in sorted(_debug_attributes_entries, key=lambda e: e[0]):
        x = context.get("x")
        y = context.get("y")
        z = context.get("z")
        coord_str = f"X={x} Y={y} Z={z}" if x is not None and y is not None and z is not None else "X=? Y=? Z=?"
        sec_file = context.get("sec_file", "?")
        lx = context.get("lx", "?")
        ly = context.get("ly", "?")
        line = context.get("line", "")
        lines.append(f"{type_name} | {coord_str} | {sec_file} | tile {lx}-{ly} | {line}\n")
    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(lines)


# Engine stack order (map.hh: Bank=0, Clip=1, Bottom=2, Top=3, Creature=4, Low=5).
# We add Height=4 from objects.srv so Height-only items (e.g. small table) sort before Low (cup on top).
STACK_PRIORITY_BANK = 0
STACK_PRIORITY_CLIP = 1
STACK_PRIORITY_BOTTOM = 2
STACK_PRIORITY_TOP = 3
STACK_PRIORITY_HEIGHT = 4
STACK_PRIORITY_LOW = 5


def load_item_stack_priority(objects_srv_path):
    """Parse objects.srv; return dict type_id -> stack priority (0=Bank .. 5=Low).
    Used so sec->OTBM writes canonical engine order; OTBM->sec would use same sort."""
    path = Path(objects_srv_path)
    if not path.exists():
        return {}
    type_to_priority = {}
    current_type_id = None
    with open(path, 'r', encoding='latin-1', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if line.startswith('TypeID'):
                match = re.search(r'TypeID\s*=\s*(\d+)', line)
                if match:
                    current_type_id = int(match.group(1))
            elif current_type_id is not None and line.startswith('Flags'):
                match = re.search(r'Flags\s*=\s*\{([^}]*)\}', line)
                if match:
                    flags_str = match.group(1)
                    flags = {s.strip().lower() for s in flags_str.split(',')}
                    if 'bank' in flags:
                        p = STACK_PRIORITY_BANK
                    elif 'clip' in flags:
                        p = STACK_PRIORITY_CLIP
                    elif 'bottom' in flags:
                        p = STACK_PRIORITY_BOTTOM
                    elif 'top' in flags:
                        p = STACK_PRIORITY_TOP
                    elif 'height' in flags:
                        p = STACK_PRIORITY_HEIGHT
                    else:
                        p = STACK_PRIORITY_LOW
                    type_to_priority[current_type_id] = p
                current_type_id = None
    return type_to_priority


def _sort_tile_items_by_priority(items, type_to_priority):
    """Stable-sort tile items by engine stack priority. Same priority = keep original order."""
    if not items or not type_to_priority:
        return items
    def key(item):
        return type_to_priority.get(item.get('id'), STACK_PRIORITY_LOW)
    return sorted(items, key=key)


def _reverse_trailing_low_group(items, type_to_priority):
    """Reverse the contiguous trailing block of LOW-priority items.
    .sec has same-priority (e.g. meat, plate) as first=bottom; RME first=bottom. So we need
    plate then meat in OTBM so RME draws plate (bottom), meat (top). Hence reverse LOW tail."""
    if not items or not type_to_priority or len(items) < 2:
        return items
    def priority(item):
        return type_to_priority.get(item.get('id'), STACK_PRIORITY_LOW)
    i = len(items)
    while i > 0 and priority(items[i - 1]) == STACK_PRIORITY_LOW:
        i -= 1
    if i < len(items):
        items = items[:i] + list(reversed(items[i:]))
    return items


def load_all_sectors(sec_dir):
    """Load all .sec files and organize by sector"""
    sec_dir = Path(sec_dir)
    sectors = defaultdict(list)
    _init_debug_attributes_log()
    
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
        for tile_entry in tiles:
            lx, ly = tile_entry[0], tile_entry[1]
            _items = tile_entry[3] if len(tile_entry) == 4 else tile_entry[2]
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
    """Build OTBM header for CipSoft 7.7 (OTB ID 100). MAP_OTBM_2 (1): count via OTBM_ATTR_COUNT."""
    writer = OTBMWriter()
    writer.start_node(OTBM_MAP_HEADER)
    writer.write_uint32(1)    # MAP_OTBM_2 = 1: item count is OTBM_ATTR_COUNT (15) + byte
    writer.write_uint16(width)
    writer.write_uint16(height)
    writer.write_uint32(1)    # OTB major version
    writer.write_uint32(100)  # OTB minor version (ID 100 = CipSoft 7.7)
    return writer.get_bytes()


def build_house_positions(houses):
    """Build (x,y,z) -> house_id lookup from parsed houses (with Fields/tiles)."""
    pos_to_house = {}
    for house in houses:
        hid = house.get('id')
        if hid is None:
            continue
        for (x, y, z) in house.get('tiles', []):
            pos_to_house[(x, y, z)] = hid
    return pos_to_house


def _write_otbm_item_recursive(writer, item_data, counters):
    """Write one OTBM_ITEM (id + attributes), then recursively write child items (containers).
    RME (iomap_otbm.cpp): MAP_OTBM_2 reads count from OTBM_ATTR_COUNT; getCount() returns subtype only if item is stackable (items.otb)."""
    writer.start_node(OTBM_ITEM)
    writer.write_uint16(item_data['id'])
    if item_data.get('liquid_type') is not None:
        writer.write_byte(OTBM_ATTR_COUNT)
        writer.write_byte(SERVER_LIQUID_TO_RME.get(item_data['liquid_type'], item_data['liquid_type']))
    elif item_data.get('count') is not None:
        writer.write_byte(OTBM_ATTR_COUNT)
        writer.write_byte(min(255, max(0, item_data['count'])))
    if item_data.get('actionid') is not None:
        writer.write_byte(OTBM_ATTR_ACTION_ID)
        writer.write_uint16(item_data['actionid'])
        counters['n_action_id'] += 1
    if item_data.get('uniqueid') is not None:
        writer.write_byte(OTBM_ATTR_UNIQUE_ID)
        writer.write_uint16(item_data['uniqueid'])
    if item_data.get('charges') is not None:
        writer.write_byte(OTBM_ATTR_CHARGES)
        writer.write_uint16(min(65535, max(0, item_data['charges'])))
    if item_data.get('text'):
        writer.write_byte(OTBM_ATTR_TEXT)
        writer.write_string(item_data['text'])
        counters['n_text'] += 1
    if item_data.get('teleport_dest'):
        tx, ty, tz = item_data['teleport_dest']
        writer.write_byte(OTBM_ATTR_TELE_DEST)
        writer.write_uint16(min(65535, max(0, tx)))
        writer.write_uint16(min(65535, max(0, ty)))
        writer.write_byte(min(15, max(0, tz)))
    content_list = item_data.get('content') or item_data.get('contents') or []
    for child in content_list:
        _write_otbm_item_recursive(writer, child, counters)
    if content_list:
        counters['container_children'] = counters.get('container_children', 0) + len(content_list)
    writer.end_node()
    counters['total_items'] += 1


def convert_map_to_otbm(sectors, output_file, map_name, towns=None, house_positions=None, item_stack_priority=None):
    """Convert .sec files to OTBM format. house_positions: dict (x,y,z) -> house_id for OTBM_HOUSETILE.
    item_stack_priority: optional dict type_id -> priority from load_item_stack_priority(objects.srv); if set, tile items are sorted by engine priority before writing (semantic lossless)."""
    
    print("\n" + "="*70)
    print("CONVERTING MAP TO OTBM")
    print("="*70)
    
    if towns is None:
        towns = []
    if house_positions is None:
        house_positions = {}
    
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
    
    areas = defaultdict(list)
    for (sx, sy, z), tiles in sectors.items():
        for tile_entry in tiles:
            if len(tile_entry) == 4:
                lx, ly, map_flags, items = tile_entry
            else:
                lx, ly, items = tile_entry[0], tile_entry[1], tile_entry[2]
                map_flags = 0
            
            abs_x = sx * SECTOR_SIZE + lx
            abs_y = sy * SECTOR_SIZE + ly
            
            # No offset - use original coordinates
            new_x = abs_x
            new_y = abs_y
            
            area_x = new_x & 0xFF00
            area_y = new_y & 0xFF00
            local_x = new_x & 0xFF
            local_y = new_y & 0xFF
            
            tile_record = {'x': local_x, 'y': local_y, 'items': items, 'map_flags': map_flags}
            hid = house_positions.get((new_x, new_y, z))
            if hid is not None:
                tile_record['house_id'] = hid
            areas[(area_x, area_y, z)].append(tile_record)
    
    print(f"  Writing {len(areas)} tile areas...")
    
    total_tiles = 0
    counters = {'n_action_id': 0, 'n_text': 0, 'total_items': 0, 'container_children': 0}
    
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
            
            house_id = tile.get('house_id')
            if house_id is not None:
                writer.start_node(OTBM_HOUSETILE)
                writer.write_byte(tile['x'])
                writer.write_byte(tile['y'])
                writer.write_uint32(house_id)
            else:
                writer.start_node(OTBM_TILE)
                writer.write_byte(tile['x'])
                writer.write_byte(tile['y'])
            
            if tile.get('map_flags'):
                writer.write_byte(OTBM_ATTR_TILE_FLAGS)
                writer.write_uint32(tile['map_flags'])
            
            # Flags + reverse whole: sort by engine priority, then reverse whole tile list. RME uses flags (alwaysOnBottom/topOrder) so bottom-block items go below; reversed order gives correct stack (see LOSSLESS_ROUNDTRIP.md, FLAG_ATTRIBUTE_MAPPING.md).
            if item_stack_priority:
                items_to_write = _sort_tile_items_by_priority(items, item_stack_priority)
                items_to_write = list(reversed(items_to_write))
            else:
                items_to_write = items
            for item_data in items_to_write:
                _write_otbm_item_recursive(writer, item_data, counters)
            
            writer.end_node()
        
        writer.end_node()
        
        if idx % 200 == 0:
            print(f"  Progress: {idx}/{len(areas)} areas...")
    
    # RME: OTBM_TOWNS after TILE_AREA so getOrCreateTile(temple) finds existing tile (no duplicate-tile warning)
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
        print(f"  ✓ Wrote {len(towns)} towns (after tile areas)")
    
    writer.end_node()  # Close MAP_DATA
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'wb') as f:
        f.write(b'OTBM')
        f.write(writer.get_bytes())
    
    n_action_id = counters['n_action_id']
    n_text = counters['n_text']
    total_items = counters['total_items']
    n_container_children = counters.get('container_children', 0)
    print(f"\n✓ Map generated: {output_file}")
    print(f"  Tiles: {total_tiles:,}, Items: {total_items:,}")
    if n_container_children:
        print(f"  Container contents written: {n_container_children:,} child items (e.g. banana in bananapalm)")
    if n_action_id or n_text:
        print(f"  Item attributes written: action_id={n_action_id:,}, text={n_text:,}")
        if n_action_id:
            print(f"  → In RME: action/unique IDs show when items.otb marks those item IDs as Door or Teleport (isDoor/isTeleport).")
    if total_items and n_action_id == 0:
        print(f"  (No action_id in output; RME only shows them when items.otb marks the item as Door/Teleport)")


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
                    fields_str = line.split('=', 1)[1].strip()
                    # Fields = {[32258,32309,5],[32259,32309,5],...} -> list of (x,y,z)
                    house['tiles'] = [
                        (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                        for m in re.finditer(r'\[(\d+),(\d+),(\d+)\]', fields_str)
                    ]
                    house['size'] = len(house.get('tiles', []))
                
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
        
        # Use filename with "mon-" prefix (matches RME creatures.xml and spawn XML)
        monster_name = 'mon-' + mon_file.stem  # e.g., "demonskeleton.mon" → "mon-demonskeleton"
        
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
    """Parse .npc files to extract NPC spawn data and outfit info (using filename with npc- prefix)"""
    npc_spawns = []
    npc_creatures = {}  # For creatures.xml generation
    
    npc_dir = Path(npc_dir)
    if not npc_dir.exists():
        return npc_spawns, npc_creatures
    
    for npc_file in npc_dir.glob("*.npc"):
        npc_filename = npc_file.stem
        # Use filename with "npc-" prefix (e.g. frans.npc -> npc-frans); matches RME creatures.xml
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
        
        # Add to creatures dict (for consistency; spawn XML uses name npc- + filename)
        effective_looktype = looktype if (looktype is not None and looktype != 0) else 130
        npc_creatures[display_name] = {
            'name': display_name,
            'looktype': effective_looktype,
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
    
    # House positions for OTBM_HOUSETILE (so RME shows houses; house XML only updates metadata)
    houses_dat = dat_dir / 'houses.dat'
    houseareas_dat = dat_dir / 'houseareas.dat'
    house_positions = {}
    if houses_dat.exists():
        houses_list = parse_houses_dat(dat_dir / 'houses.dat')
        house_positions = build_house_positions(houses_list)
        if house_positions:
            print(f"\n✓ House tiles: {len(house_positions)} positions from houses.dat Fields")
    
    # Stack order: sort tile items by engine priority (objects.srv) for semantic lossless sec↔OTBM
    objects_srv = dat_dir / 'objects.srv'
    item_stack_priority = load_item_stack_priority(objects_srv) if objects_srv.exists() else None
    if item_stack_priority:
        print(f"\n✓ Stack order: loaded priorities for {len(item_stack_priority)} types from objects.srv")
    else:
        print(f"\n⚠ Stack order: objects.srv not found, writing tile items in .sec order (no priority sort)")
    
    # Convert map (with house tiles so RME creates House objects)
    convert_map_to_otbm(
        sectors,
        output_dir / f'{output_name}.otbm',
        output_name,
        towns=towns,
        house_positions=house_positions,
        item_stack_priority=item_stack_priority
    )
    
    # Generate houses XML (name, entry, rent, townid - applied to houses created from OTBM)
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
    _write_debug_attributes_log()
    if _debug_attributes_entries:
        print(f"  → logs/debug_attributes.log ({len(_debug_attributes_entries)} attribute occurrences, by type)")
    print()


if __name__ == '__main__':
    main()
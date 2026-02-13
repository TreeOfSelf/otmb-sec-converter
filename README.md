# OTBM ↔ SEC Converter (CipSoft 7.7)

Convert between CipSoft `.sec` files and OTBM format for RME editing.

## Quick Start

1. **Generate RME data files:**
   ```bash
   python3 generate_rme_data.py
   ```

2. **Install RME config** (copy `output/rme_config/` contents to RME)

3. **Convert .sec → .otbm:**
   ```bash
   python3 sec_to_otbm.py ./sectors/ mymap.otbm
   ```

4. **Edit in RME** using "7.70 (CipSoft)" client

5. **Convert back .otbm → .sec:**
   ```bash
   python3 otbm_to_sec.py output/mymap.otbm ./output_sectors/ assets/objects.srv
   ```

## Features

- ✅ Direct CipSoft TypeID support (no translation needed)
- ✅ Auto-generates RME items.otb from objects.srv
- ✅ Preserves all TypeIDs and attributes
- ✅ Coordinate normalization (optional `--no-offset`)

See `CIPSOFT_MODE.md` for full documentation.

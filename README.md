# portage-pro100s

Reverse-engineering notes and tools to use the **Canon PIXMA PRO-100S** (USB ID `04a9:10ec`) on Linux.

Canon ships no Linux driver for this printer and it is not supported by Gutenprint. This project documents its print stream and provides small Python tools built from captures of the official Windows driver.

> **Status: experimental.** Ink levels work on Linux. The raster format is decoded and re-encoded byte-for-byte, but **no page has been printed from generated data yet**.

| Feature | Status |
|---|---|
| Read ink levels over USB on Linux | Working (tested on Debian) |
| Decode the raster stream of a Windows driver capture | Working |
| Re-encode raster data identical to the driver's | Working (1,043 blocks, 2 captures) |
| Print a page from generated data | Not yet (first test stopped at a paper-feed error) |
| RGB to 8 inks separation and dithering | Not started |
| CUPS filter | Skeleton only |

## Print stream format

A job captured with "Print to file" from the Windows driver contains:

1. IVEC XML commands (`StartJob`, `VendorCmd ModeShift`, ... `EndJob`).
2. A `BJLSTART ... BJLEND` text block.
3. Binary `ESC (` commands: `ESC ( X` + uint16 little-endian length + payload.

Known commands:

| Command | Meaning |
|---|---|
| `ESC ( L` | Ink order, e.g. `CMYcmkHL` (8 inks) |
| `ESC ( l` | Media / paper source (`0x1e` = Photo Paper Pro Platinum) |
| `ESC ( N` | Raster block (length is a **uint32** little-endian) |

### Raster blocks (`ESC ( N`)

- Payload = `F` (`0x46`) alone: empty band for this ink.
- Otherwise `F` followed by exactly **8 lines**, each one `PackBits(line) + 0x80`.
- Each line is cut after its last non-zero byte; an empty line is `0x80` alone.
- Block `n` is ink `n % 8` (in `ESC ( L` order) and band `n // 8`.
- 4 bits per pixel (2 pixels per byte) in the captured modes.

## Ink levels

The read-only BJL status command `SSR=BST,SFA,CHD,CIL,CIR,...;` wrapped in `ESC [ K` returns an IVEC XML document. Its `CIR` field gives each ink level in 10 % steps.

## Files

| File | Purpose |
|---|---|
| `ink_levels_linux.py` | Read ink levels from `/dev/usb/lp*` (JSON output) |
| `decoder_raster.py` | Decode a `.prn` capture into 8 PGM images (one per ink), plus the encoder |
| `pro100s_proto.py` | Stream parser and experimental minimal stream generator |
| `rejouer_usb_linux.py` | Replay a `.prn` to `/dev/usb/lp*` (dry run by default) |
| `envoyer_brut_windows.py` | Send a `.prn` as RAW to a Windows print queue |
| `rastertopro100s.py` | CUPS filter skeleton |
| `test_pro100s_proto.py` | Unit tests |
| `captures/` | Sample captures from the Windows driver (A4, no personal data) |

Code comments are mostly in French.

## Usage

```sh
python3 ink_levels_linux.py                   # print ink levels once
python3 decoder_raster.py captures/capture_ordinaire_test.prn out/
python3 -m unittest test_pro100s_proto
```

The user needs access to `/dev/usb/lp*`, for example with this udev rule:

```
SUBSYSTEMS=="usb", ATTRS{idVendor}=="04a9", ATTRS{idProduct}=="10ec", GROUP="plugdev", MODE="0660"
```

## Legal

This is an independent interoperability project, not affiliated with Canon. It contains no Canon driver files. Canon and PIXMA are trademarks of Canon Inc.

## License

GPL-3.0-or-later. See `LICENSE`.

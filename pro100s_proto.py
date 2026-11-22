#!/usr/bin/env python3
"""Analyseur et générateur expérimental de flux Canon PIXMA PRO-100S (modèle interne 0454).

Basé sur l'inspection en lecture seule du pilote Windows officiel 1.02 / 2.87.2.20
(`PRO-100S6.inf`, `CNM_0454.DLL`, `CNMLMON2.DLL`, `CNB_4540.TBL`).

Statut :
- Partie ANALYSE (`inspect`) : décodage vérifié sur les structures IVEC XML, BJL,
  ESC(c, ESC(d, ESC(t (27 octets / 8 canaux), ESC(p (46 octets géométrie page),
  ESC(l, ESC(b, ESC(q, ESC(r, ESC(e, ESC(v et trames raster ESC(F / ESC(A.
- Partie GÉNÉRATION (`gen-test`) : PROTOTYPE EXPÉRIMENTAL NON TESTÉ SUR PAPIER.
  Les détails exacts de l'entrelacement raster (`ESC(F` vs `ESC(A` et ordre exact
  des 8 canaux) doivent être validés sur une capture `.prn` du pilote Windows
  (port `FILE:`) avant tout envoi vers l'imprimante réelle.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import struct
import sys
from typing import Any


# --- Constantes vérifiées dans CNM_0454.DLL / Registre Windows ---

USB_VID_CANON = 0x04A9
USB_PID_PRO100S = 0x10EC
MODEL_CODE = "0454"
COMPATIBLE_ID = "1284_CID_CA_IVEC1TYPE3_IJP"

# Noms des 8 cartouches CLI-42 (vérifiés via commande BJL SSR/CIR sur USB)
CLI42_INKS = ("PBK", "GY", "LGY", "C", "M", "Y", "LC", "LM")

# Ordre supposé des 8 canaux dans ESC(t (27 octets = 3 + 8*3) :
# Dans les modes 4 encres papier ordinaire de CNM_0454.DLL, seuls les indices
# 0, 1, 2 et 5 sont actifs -> cohérent avec C, M, Y, (LC), (LM), PBK, (GY), (LGY).
# À confirmer sur capture `.prn` réelle (port FILE:).
ESC_T_CHANNEL_GUESS = ("C", "M", "Y", "LC?", "LM?", "PBK", "GY?", "LGY?")

# Enveloppes XML IVEC exactes extraites de CNM_0454.DLL
XML_START_JOB = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<cmd xmlns:ivec="http://www.canon.com/ns/cmd/2008/07/common/">'
    '<ivec:contents><ivec:operation>StartJob</ivec:operation>'
    '<ivec:param_set servicetype="print">'
    '<ivec:jobID>{job_id:08d}</ivec:jobID>'
    '<ivec:bidi>{bidi}</ivec:bidi>'
    '</ivec:param_set></ivec:contents></cmd>'
)

XML_VENDOR_MODESHIFT = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<cmd xmlns:ivec="http://www.canon.com/ns/cmd/2008/07/common/" '
    'xmlns:vcn="http://www.canon.com/ns/cmd/2008/07/canon/">'
    '<ivec:contents><ivec:operation>VendorCmd</ivec:operation>'
    '<ivec:param_set servicetype="print">'
    '<vcn:ijoperation>ModeShift</vcn:ijoperation>'
    '<vcn:ijmode>{mode}</vcn:ijmode>'
    '<ivec:jobID>{job_id:08d}</ivec:jobID>'
    '</ivec:param_set></ivec:contents></cmd>'
)

XML_END_JOB = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<cmd xmlns:ivec="http://www.canon.com/ns/cmd/2008/07/common/">'
    '<ivec:contents><ivec:operation>EndJob</ivec:operation>'
    '<ivec:param_set servicetype="print">'
    '<ivec:jobID>{job_id:08d}</ivec:jobID>'
    '</ivec:param_set></ivec:contents></cmd>'
)

# Quelques géométries ESC(p (46 octets) vérifiées directement dans CNM_0454.DLL
ESC_P_PRESETS: dict[str, bytes] = {
    # A4 (210.0 x 297.0 mm) avec marges : area=(80, 70, 4800, 6827), paper=(4961, 7016) @ 600 DPI
    "a4_bordered": bytes.fromhex(
        "02ab000001e00000000000000258"
        "0000005000000046000012c000001aab"
        "00000000000000000000136100001b68"
    ),
    # A4 (210.0 x 297.0 mm) sans bordure : area=(-66, -47, 5093, 7182), paper=(4961, 7016) @ 600 DPI
    "a4_borderless": bytes.fromhex(
        "0000000000000000000000000258"
        "ffffffbeffffffd1000013e500001c0e"
        "00000000000000000000136100001b68"
    ),
    # 10x15 cm / 4x6" (101.6 x 152.4 mm) sans bordure @ 600 DPI
    "4x6_borderless": bytes.fromhex(
        "0000000000000000000000000258"
        "ffffffbeffffffd1000009e500000eb6"
        "00000000000000000000096000000e10"
    ),
    # A3+ (329.0 x 483.0 mm) avec marges @ 600 DPI
    "a3plus_bordered": bytes.fromhex(
        "0488000003080000000000000258"
        "000000500000004600001dbc00002bd5"
        "000000000000000000001e5c00002c92"
    ),
}

# Modes vérifiés dans CNM_0454.DLL : (ESC(c payload, ESC(d payload, ESC(t payload))
MODE_PRESETS: dict[str, tuple[bytes, bytes, bytes]] = {
    # Papier ordinaire brouillon : 600x600 DPI, 4 canaux 1-bit binaire (2 niveaux)
    "plain_draft_600_4ch_1bit": (
        bytes.fromhex("300001"),
        bytes.fromhex("02580258"),
        bytes.fromhex("800001010002010002010002000000000000010002000000000000"),
    ),
    # Papier ordinaire standard : 600x600 DPI, 4 canaux (0x24, 6 niveaux)
    "plain_std_600_4ch_6lvl": (
        bytes.fromhex("300002"),
        bytes.fromhex("02580258"),
        bytes.fromhex("808001240006240006240006000000000000240006000000000000"),
    ),
    # Papier ordinaire haute qualité : 600x600 DPI, 8 canaux (0x24, 6 niveaux)
    "plain_high_600_8ch_6lvl": (
        bytes.fromhex("300003"),
        bytes.fromhex("02580258"),
        bytes.fromhex("808001240006240006240006240006240006240006240006240006"),
    ),
    # Photo brillant/platine haute qualité : 1200x1200 DPI, 8 canaux 2-bit (4 niveaux)
    "photo_1200_8ch_2bit": (
        bytes.fromhex("301d04"),
        bytes.fromhex("04b004b0"),
        bytes.fromhex("808001020004020004020004020004020004020004020004020004"),
    ),
}


# --- Compression PackBits (TIFF / Canon ESC(b=1) ---

def packbits_encode(data: bytes) -> bytes:
    """Encode des octets en RLE PackBits (utilisé par Canon ESC(b = 0x01)."""
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        # Détecter une répétition >= 3 octets identiques
        run_len = 1
        while i + run_len < n and run_len < 128 and data[i + run_len] == data[i]:
            run_len += 1
        if run_len >= 3:
            out.append((257 - run_len) & 0xFF)
            out.append(data[i])
            i += run_len
        else:
            # Séquence littérale jusqu'au prochain run >= 3 ou 128 octets
            start = i
            lit_len = 0
            while i < n and lit_len < 128:
                if i + 2 < n and data[i] == data[i + 1] == data[i + 2]:
                    break
                i += 1
                lit_len += 1
            out.append(lit_len - 1)
            out.extend(data[start : start + lit_len])
    return bytes(out)


def packbits_decode(data: bytes) -> bytes:
    """Décode un tampon RLE PackBits."""
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        hdr = data[i]
        i += 1
        if hdr <= 127:
            count = hdr + 1
            out.extend(data[i : i + count])
            i += count
        elif hdr >= 129:
            count = 257 - hdr
            if i < n:
                out.extend(bytes([data[i]]) * count)
                i += 1
        # hdr == 128 : NOP PackBits
    return bytes(out)


# --- Décodage des commandes ESC( ---

def decode_esc_c(payload: bytes) -> dict[str, Any]:
    if len(payload) < 3:
        return {"raw": payload.hex()}
    return {
        "flags_hex": f"0x{payload[0]:02x}",
        "media_hex": f"0x{payload[1]:02x}",
        "quality": payload[2],
    }


def decode_esc_d(payload: bytes) -> dict[str, Any]:
    if len(payload) != 4:
        return {"raw": payload.hex()}
    dx, dy = struct.unpack(">HH", payload)
    return {"dpi_x": dx, "dpi_y": dy}


def decode_esc_t(payload: bytes) -> dict[str, Any]:
    if len(payload) < 3:
        return {"raw": payload.hex()}
    hdr = payload[:3]
    rest = payload[3:]
    channels = []
    for idx in range(len(rest) // 3):
        bpp_flag, reserved, levels = rest[idx * 3 : idx * 3 + 3]
        name = ESC_T_CHANNEL_GUESS[idx] if idx < len(ESC_T_CHANNEL_GUESS) else f"ch{idx}"
        channels.append({
            "index": idx,
            "name_guess": name,
            "bpp_or_flag": bpp_flag,
            "reserved": reserved,
            "levels": levels,
            "active": bpp_flag != 0 or levels != 0,
        })
    return {
        "header_hex": hdr.hex(),
        "active_channels": sum(1 for c in channels if c["active"]),
        "total_channels": len(channels),
        "channels": channels,
    }


def decode_esc_p(payload: bytes) -> dict[str, Any]:
    if len(payload) != 46:
        return {"raw": payload.hex(), "length": len(payload)}
    hdr = payload[:12].hex()
    dpi = struct.unpack(">H", payload[12:14])[0]
    vals = struct.unpack(">8i", payload[14:46])
    w_mm = round(vals[6] * 25.4 / dpi, 1) if dpi else 0.0
    h_mm = round(vals[7] * 25.4 / dpi, 1) if dpi else 0.0
    return {
        "header_hex": hdr,
        "dpi": dpi,
        "printable_area": {"left": vals[0], "top": vals[1], "width": vals[2], "height": vals[3]},
        "paper_box": {"x": vals[4], "y": vals[5], "width": vals[6], "height": vals[7]},
        "paper_mm": [w_mm, h_mm],
        "borderless": vals[0] < 0 or vals[1] < 0,
    }


# --- Parseur de flux PRO-100S (.prn capturé ou généré) ---

@dataclasses.dataclass
class StreamToken:
    offset: int
    kind: str
    length: int
    detail: Any


def parse_stream(data: bytes) -> list[StreamToken]:
    """Découpe un flux binaire Canon PRO-100S en blocs XML IVEC, BJL, ESC[K, ESC(, FF."""
    tokens: list[StreamToken] = []
    n = len(data)
    i = 0

    while i < n:
        # 1. Enveloppe XML IVEC
        if data.startswith(b"<?xml", i):
            end = data.find(b"</cmd>", i)
            if end != -1:
                end += len(b"</cmd>")
                xml_txt = data[i:end].decode("utf-8", "replace")
                op_m = re.search(r"<ivec:operation>([^<]+)</ivec:operation>", xml_txt)
                tokens.append(StreamToken(
                    offset=i,
                    kind="IVEC_XML",
                    length=end - i,
                    detail={"operation": op_m.group(1) if op_m else "unknown", "xml": xml_txt},
                ))
                i = end
                continue

        # 2. Bloc BJLSTART ... BJLEND
        if data.startswith(b"BJLSTART", i):
            end = data.find(b"BJLEND", i)
            if end != -1:
                end += len(b"BJLEND")
                if end < n and data[end : end + 1] == b"\n":
                    end += 1
                txt = data[i:end].decode("ascii", "replace").strip()
                tokens.append(StreamToken(offset=i, kind="BJL", length=end - i, detail=txt))
                i = end
                continue

        # 3. Commande ESC [K (contrôle / reset / BJL) : 1B 5B 4B <len_lo> <len_hi> <payload>
        if data.startswith(b"\x1b[K", i) and i + 5 <= n:
            ln = struct.unpack("<H", data[i + 3 : i + 5])[0]
            end = min(n, i + 5 + ln)
            payload = data[i + 5 : end]
            tokens.append(StreamToken(
                offset=i,
                kind="ESC[K",
                length=end - i,
                detail={"len": ln, "hex": payload.hex(), "ascii": payload.decode("ascii", "replace")},
            ))
            i = end
            continue

        # 4. Commande ESC ( <cmd> <len_lo> <len_hi> <payload>
        if data.startswith(b"\x1b(", i) and i + 5 <= n:
            cmd_byte = data[i + 2]
            cmd_ch = chr(cmd_byte) if 32 <= cmd_byte < 127 else f"0x{cmd_byte:02x}"
            ln = struct.unpack("<H", data[i + 3 : i + 5])[0]
            end = min(n, i + 5 + ln)
            payload = data[i + 5 : end]

            detail: Any
            if cmd_ch == "c":
                detail = decode_esc_c(payload)
            elif cmd_ch == "d":
                detail = decode_esc_d(payload)
            elif cmd_ch == "t":
                detail = decode_esc_t(payload)
            elif cmd_ch == "p":
                detail = decode_esc_p(payload)
            elif cmd_ch in ("F", "A"):
                # Données raster : ne pas stocker le binaire brut dans le résumé
                head = payload[:8].hex()
                detail = {"payload_bytes": len(payload), "head_hex": head}
            else:
                detail = {"len": ln, "hex": payload.hex()}

            tokens.append(StreamToken(offset=i, kind=f"ESC({cmd_ch}", length=end - i, detail=detail))
            i = end
            continue

        # 5. ESC @ (reset imprimante)
        if data.startswith(b"\x1b@", i):
            tokens.append(StreamToken(offset=i, kind="ESC@", length=2, detail="reset"))
            i += 2
            continue

        # 6. Form Feed (éjection page)
        if data[i] == 0x0C:
            tokens.append(StreamToken(offset=i, kind="FF", length=1, detail="eject_page"))
            i += 1
            continue

        # 7. Octets non reconnus (regroupés jusqu'au prochain marqueur connu)
        start = i
        i += 1
        while i < n and data[i] not in (0x1B, 0x0C) and not data.startswith(b"<?xml", i) and not data.startswith(b"BJLSTART", i):
            i += 1
        chunk = data[start:i]
        tokens.append(StreamToken(
            offset=start,
            kind="RAW",
            length=len(chunk),
            detail={"len": len(chunk), "head_hex": chunk[:16].hex()},
        ))

    return tokens


def summarize_tokens(tokens: list[StreamToken], total_bytes: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    bytes_by_kind: dict[str, int] = {}
    ivec_ops: list[str] = []
    esc_c = None
    esc_d = None
    esc_t = None
    esc_p = None
    bjl_blocks: list[str] = []

    for tok in tokens:
        counts[tok.kind] = counts.get(tok.kind, 0) + 1
        bytes_by_kind[tok.kind] = bytes_by_kind.get(tok.kind, 0) + tok.length
        if tok.kind == "IVEC_XML":
            ivec_ops.append(tok.detail["operation"])
        elif tok.kind == "BJL":
            bjl_blocks.append(tok.detail)
        elif tok.kind == "ESC(c" and esc_c is None:
            esc_c = tok.detail
        elif tok.kind == "ESC(d" and esc_d is None:
            esc_d = tok.detail
        elif tok.kind == "ESC(t" and esc_t is None:
            esc_t = tok.detail
        elif tok.kind == "ESC(p" and esc_p is None:
            esc_p = tok.detail

    return {
        "total_bytes": total_bytes,
        "token_count": len(tokens),
        "counts": counts,
        "bytes_by_kind": bytes_by_kind,
        "ivec_operations": ivec_ops,
        "bjl_blocks": bjl_blocks,
        "first_esc_c": esc_c,
        "first_esc_d": esc_d,
        "first_esc_t": esc_t,
        "first_esc_p": esc_p,
    }


# --- Générateur expérimental de flux PRO-100S ---

def make_esc(cmd: str, payload: bytes) -> bytes:
    """Construit une commande binaire ESC(<cmd> <len_u16_le> <payload>."""
    if len(cmd) != 1:
        raise ValueError("cmd doit être un caractère ASCII unique")
    return b"\x1b(" + cmd.encode("ascii") + struct.pack("<H", len(payload)) + payload


def make_esc_k(payload: bytes) -> bytes:
    """Construit une commande ESC[K <len_u16_le> <payload>."""
    return b"\x1b[K" + struct.pack("<H", len(payload)) + payload


def build_experimental_stream(
    width_px: int = 600,
    height_px: int = 60,
    mode: str = "plain_draft_600_4ch_1bit",
    paper: str = "a4_bordered",
    job_id: int = 1,
    compress: bool = True,
) -> bytes:
    """Génère un flux de test minimal (EXPÉRIMENTAL, NON TESTÉ SUR PAPIER).

    Produit 4 bandes horizontales (C, M, Y, K) en 1-bit dans le mode papier
    ordinaire brouillon 600x600 DPI pour valider la structure de trame.
    """
    if mode not in MODE_PRESETS:
        raise ValueError(f"Mode inconnu : {mode}. Choix : {list(MODE_PRESETS)}")
    if paper not in ESC_P_PRESETS:
        raise ValueError(f"Format inconnu : {paper}. Choix : {list(ESC_P_PRESETS)}")

    c_pay, d_pay, t_pay = MODE_PRESETS[mode]
    p_pay = ESC_P_PRESETS[paper]

    out = bytearray()
    # 1. Ouverture de job IVEC XML
    out.extend(XML_START_JOB.format(job_id=job_id, bidi=0).encode("utf-8"))
    out.extend(XML_VENDOR_MODESHIFT.format(job_id=job_id, mode=1).encode("utf-8"))

    # 2. En-tête BJL / ESC[K minimal (basé sur CNM_0454.DLL)
    out.extend(make_esc_k(b"\x00\x1f"))
    out.extend(b"BJLSTART\nControlMode=Common\nBJLEND\n")

    # 3. Configuration page / média / résolution / format 8 canaux
    out.extend(make_esc("b", b"\x01" if compress else b"\x00"))
    out.extend(make_esc("c", c_pay))
    out.extend(make_esc("d", d_pay))
    out.extend(make_esc("t", t_pay))
    out.extend(make_esc("p", p_pay))
    out.extend(make_esc("q", b"\x01"))

    # 4. Données raster expérimentales (ESC(F — structure exacte à confirmer par capture FILE:)
    row_bytes = (width_px + 7) // 8
    solid_line = b"\xff" * row_bytes
    empty_line = b"\x00" * row_bytes
    band_h = max(1, height_px // 4)

    for y in range(height_px):
        active_plane = min(3, y // band_h)
        # 4 plans actifs en mode plain_draft_600_4ch_1bit : C(0), M(1), Y(2), PBK(3)
        for plane_idx in range(4):
            raw_line = solid_line if plane_idx == active_plane else empty_line
            encoded = packbits_encode(raw_line) if compress else raw_line
            # En-tête de plan minimal (1 octet identifiant couleur supposé + payload)
            plane_tag = b"CMYK"[plane_idx : plane_idx + 1]
            out.extend(make_esc("F", plane_tag + encoded))

    # 5. Fin de page et clôture IVEC
    out.append(0x0C)  # Form Feed
    out.extend(make_esc("b", b"\x00"))
    out.extend(XML_END_JOB.format(job_id=job_id).encode("utf-8"))
    return bytes(out)


# --- Interface ligne de commande ---

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyseur / générateur expérimental de flux Canon PIXMA PRO-100S."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Analyser un fichier .prn capturé depuis Windows (port FILE:)")
    p_ins.add_argument("fichier", help="Chemin du fichier .prn")
    p_ins.add_argument("--json", action="store_true", help="Afficher le résumé complet en JSON")
    p_ins.add_argument("--tokens", type=int, default=20, help="Nombre de premiers tokens à détailler")

    p_gen = sub.add_parser("gen-test", help="Générer un flux de test EXPÉRIMENTAL (ne pas envoyer sans validation)")
    p_gen.add_argument("-o", "--output", required=True, help="Fichier de sortie (.prn)")
    p_gen.add_argument("--mode", default="plain_draft_600_4ch_1bit", choices=sorted(MODE_PRESETS))
    p_gen.add_argument("--paper", default="a4_bordered", choices=sorted(ESC_P_PRESETS))
    p_gen.add_argument("--width", type=int, default=600)
    p_gen.add_argument("--height", type=int, default=60)

    args = parser.parse_args(argv)

    if args.cmd == "inspect":
        with open(args.fichier, "rb") as f:
            raw = f.read()
        tokens = parse_stream(raw)
        summary = summarize_tokens(tokens, len(raw))
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"Fichier : {args.fichier} ({summary['total_bytes']} octets, {summary['token_count']} blocs)")
            print("Opérations IVEC :", summary["ivec_operations"])
            print("Compteurs blocs :", summary["counts"])
            if summary["first_esc_c"]:
                print("ESC(c :", summary["first_esc_c"])
            if summary["first_esc_d"]:
                print("ESC(d :", summary["first_esc_d"])
            if summary["first_esc_t"]:
                t = summary["first_esc_t"]
                print(f"ESC(t : header={t['header_hex']} actifs={t['active_channels']}/{t['total_channels']}")
            if summary["first_esc_p"]:
                p = summary["first_esc_p"]
                print(f"ESC(p : {p['paper_mm'][0]}x{p['paper_mm'][1]} mm @ {p['dpi']} DPI, borderless={p['borderless']}")
            if args.tokens > 0:
                print(f"\nPremiers {min(args.tokens, len(tokens))} blocs :")
                for tok in tokens[: args.tokens]:
                    print(f"  @0x{tok.offset:06x} {tok.kind:10s} ({tok.length:5d} B) {tok.detail}")
        return 0

    if args.cmd == "gen-test":
        stream = build_experimental_stream(
            width_px=args.width,
            height_px=args.height,
            mode=args.mode,
            paper=args.paper,
        )
        with open(args.output, "wb") as f:
            f.write(stream)
        print(f"Flux expérimental écrit : {args.output} ({len(stream)} octets)")
        print("ATTENTION : flux expérimental non testé sur papier ; à comparer d'abord avec une capture Windows FILE:.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

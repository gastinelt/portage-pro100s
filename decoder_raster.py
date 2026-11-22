#!/usr/bin/env python3
"""Décode / ré-encode le raster d'une capture Canon PRO-100S (.prn, impression vers fichier).

Format vérifié le 22/11/2026 sur 1 043 blocs de 2 captures réelles :
  ESC ( N + longueur uint32 little-endian + charge utile
  charge utile = 'F' (0x46) seul            -> bande vide pour cette encre
               = 'F' + 8 x [ PackBits(ligne) + 0x80 ]
  - bloc n -> encre n % 8, dans l'ordre de ESC(L (ex. 'CMYcmkHL'), bande n // 8 ;
  - chaque bloc = exactement 8 lignes de pixels, chacune terminée par l'octet 0x80 ;
  - chaque ligne est coupée après son dernier octet non nul (ligne vide = 0x80 seul) ;
  - 4 bits par pixel (2 pixels par octet) dans les modes capturés (ESC(t = 04 00 09 x 8).
Usage : py -3 decoder_raster.py capture.prn dossier_sortie    -> 8 images PGM (une par encre)
"""
import os, struct, sys

ZERO = bytes(1)


def blocs(d):
    i = d.find(b"\x1b(N")
    while i != -1:
        n = struct.unpack("<I", d[i + 3:i + 7])[0]
        yield d[i + 7:i + 7 + n]
        i = d.find(b"\x1b(N", i + 7 + n)


def lignes_bloc(s):
    """Charge utile sans le 'F' -> liste de 8 lignes (bytes, non complétées)."""
    lignes = [bytearray()]; i = 0
    while i < len(s):
        c = s[i]; i += 1
        if c == 0x80:
            lignes.append(bytearray()); continue
        if c < 128:
            lignes[-1] += s[i:i + c + 1]; i += c + 1
        else:
            lignes[-1] += bytes([s[i]]) * (257 - c); i += 1
    return [bytes(l) for l in lignes[:8]]


def packbits_ligne(ligne, rep_min=3):
    """Compresse une ligne (zéros de fin retirés) puis ajoute le séparateur 0x80."""
    ligne = ligne.rstrip(ZERO)
    o = bytearray(); lit = bytearray(); i = 0; n = len(ligne)

    def vider():
        nonlocal lit
        while lit:
            morceau = lit[:128]; o.append(len(morceau) - 1); o.extend(morceau); lit = lit[128:]

    while i < n:
        j = i
        while j < n and ligne[j] == ligne[i] and j - i < 128:
            j += 1
        if j - i >= rep_min:
            vider(); o.append(257 - (j - i)); o.append(ligne[i]); i = j
        else:
            lit.append(ligne[i]); i += 1
    vider(); o.append(0x80)
    return bytes(o)


def bloc_depuis_lignes(lignes8, rep_min=3):
    if not any(l.strip(ZERO) for l in lignes8):
        return b"F"
    return b"F" + b"".join(packbits_ligne(l, rep_min) for l in lignes8)


def canaux(d):
    i = d.find(b"\x1b(L")
    if i == -1:
        return "01234567"
    n = struct.unpack("<H", d[i + 3:i + 5])[0]
    return d[i + 5:i + 5 + n].decode("ascii", "replace")


def decoder(chemin, sortie):
    d = open(chemin, "rb").read()
    noms = canaux(d)
    B = list(blocs(d))
    L = [None if b == b"F" else lignes_bloc(b[1:]) for b in B]
    w = max((len(x) for l in L if l for x in l), default=0)
    if not w:
        print("aucune ligne encrée"); return
    os.makedirs(sortie, exist_ok=True)
    for p in range(8):
        pix = bytearray(); h = 0
        for k in range(p, len(L), 8):
            for r in range(8):
                ligne = (L[k][r] if L[k] else b"").ljust(w, ZERO)
                for octet in ligne:
                    pix += bytes([255 - (octet >> 4) * 17, 255 - (octet & 15) * 17])
                h += 1
        nom = "plan%d_%s.pgm" % (p, noms[p] if p < len(noms) else p)
        with open(os.path.join(sortie, nom), "wb") as f:
            f.write(b"P5 %d %d 255" % (w * 2, h) + bytes([10]) + bytes(pix))
        print("%s : %d x %d px, pixels encrés %d" % (nom, w * 2, h, sum(1 for x in pix if x < 255)))


if __name__ == "__main__":
    decoder(sys.argv[1], sys.argv[2])

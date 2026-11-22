#!/usr/bin/env python3
"""Ébauche de filtre CUPS `rastertopro100s` (NON INSTALLÉ, PROTOTYPE LOCAL).

Signature standard d'un filtre CUPS :
    rastertopro100s job-id user title copies options [file]

Ce squelette montre comment raccorder un flux `application/vnd.cups-raster`
au générateur `pro100s_proto.py`. Il ne gère pas encore :
- le décodage complet des en-têtes CUPS Raster v2/v3 multi-pages,
- la séparation couleur 8 encres CLI-42 (PBK/GY/LGY/C/M/Y/LC/LM) ni les profils ICC,
- le tramage multi-niveaux (2 bits / 4 bits) et l'entrelacement exact `ESC(F`
  (à confirmer par capture Windows `FILE:`).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pro100s_proto


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (5, 6):
        sys.stderr.write("Usage: rastertopro100s job-id user title copies options [file]\n")
        return 1

    job_id_str, _user, _title, _copies, options_str = args[:5]
    job_id = int(job_id_str) if job_id_str.isdigit() else 1
    in_path = args[5] if len(args) == 6 else None

    # Lecture minimale du flux d'entrée (CUPS Raster) pour vérifier la présence du magic "RaS2"/"RaS3"
    if in_path:
        with open(in_path, "rb") as f:
            head = f.read(4)
    else:
        head = sys.stdin.buffer.read(4)

    if head not in (b"RaS2", b"RaS3", b"2SaR", b"3SaR"):
        sys.stderr.write(f"ERROR: flux CUPS Raster invalide (magic={head!r})\n")
        return 1

    paper = "a4_borderless" if "Borderless" in options_str else "a4_bordered"
    sys.stderr.write(f"INFO: rastertopro100s (prototype) job={job_id} paper={paper}\n")

    # Sortie d'un flux de test minimal (en attendant le moteur de tramage 8 encres)
    stream = pro100s_proto.build_experimental_stream(
        width_px=600,
        height_px=60,
        mode="plain_draft_600_4ch_1bit",
        paper=paper,
        job_id=job_id,
    )
    sys.stdout.buffer.write(stream)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

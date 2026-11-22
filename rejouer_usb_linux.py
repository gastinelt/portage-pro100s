#!/usr/bin/env python3
"""Outil de rejeu USB Linux pour un flux `.prn` capturé depuis Windows (voie b).

Sécurité :
- Mode `--dry-run` actif par défaut : n'écrit RIEN vers l'imprimante tant que
  `--envoyer` n'est pas passé explicitement.
- Vérifie que le fichier ressemble bien à un flux Canon PRO-100S (`StartJob` / `ESC(`)
  avant tout envoi vers `/dev/usb/lp0` (module noyau `usblp`).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pro100s_proto


def verifier_flux(chemin: str) -> tuple[bytes, dict]:
    with open(chemin, "rb") as f:
        data = f.read()
    tokens = pro100s_proto.parse_stream(data)
    summary = pro100s_proto.summarize_tokens(tokens, len(data))
    if "StartJob" not in summary["ivec_operations"] and not any(k.startswith("ESC(") for k in summary["counts"]):
        raise ValueError("Le fichier ne contient ni enveloppe IVEC StartJob ni commande ESC(.")
    return data, summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rejouer un fichier .prn Canon PRO-100S vers /dev/usb/lp* (Linux).")
    p.add_argument("fichier", help="Fichier .prn capturé via le port FILE: de Windows")
    p.add_argument("--device", default="/dev/usb/lp0", help="Périphérique usblp cible (défaut: /dev/usb/lp0)")
    p.add_argument("--chunk", type=int, default=16384, help="Taille des blocs d'écriture en octets")
    p.add_argument("--envoyer", action="store_true", help="Effectuer réellement l'envoi USB (sinon dry-run)")
    args = p.parse_args(argv)

    data, summary = verifier_flux(args.fichier)
    print(f"Flux valide : {args.fichier} ({len(data)} octets, IVEC={summary['ivec_operations']})")
    if summary["first_esc_d"]:
        print(f"  Résolution : {summary['first_esc_d']}")
    if summary["first_esc_p"]:
        print(f"  Papier     : {summary['first_esc_p']['paper_mm']} mm (borderless={summary['first_esc_p']['borderless']})")

    if not args.envoyer:
        print(f"[DRY-RUN] Aucun octet envoyé vers {args.device}. Ajoutez --envoyer sous Linux pour exécuter.")
        return 0

    if os.name != "posix":
        sys.stderr.write("ERREUR : ce script de rejeu cible Linux (/dev/usb/lp*).\n")
        return 2

    sent = 0
    with open(args.device, "wb", buffering=0) as dev:
        for pos in range(0, len(data), args.chunk):
            blk = data[pos : pos + args.chunk]
            dev.write(blk)
            sent += len(blk)
    print(f"Envoi terminé : {sent} octets écrits vers {args.device}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

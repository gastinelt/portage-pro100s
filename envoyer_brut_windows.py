#!/usr/bin/env python3
"""Envoie un fichier .prn tel quel (type RAW) à une file d'impression Windows, sans pilote intermédiaire.
Usage : py -3 envoyer_brut_windows.py fichier.prn ["Canon PRO-100S series"]
"""
import ctypes, sys
from ctypes import wintypes

winspool = ctypes.WinDLL("winspool.drv")


class DOC_INFO_1(ctypes.Structure):
    _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]


def envoyer(chemin, file_impr="Canon PRO-100S series"):
    data = open(chemin, "rb").read()
    h = wintypes.HANDLE()
    if not winspool.OpenPrinterW(file_impr, ctypes.byref(h), None):
        raise OSError("OpenPrinter a échoué")
    try:
        doc = DOC_INFO_1("essai-brut-pro100s", None, "RAW")
        if not winspool.StartDocPrinterW(h, 1, ctypes.byref(doc)):
            raise OSError("StartDocPrinter a échoué")
        winspool.StartPagePrinter(h)
        ecrit = wintypes.DWORD()
        ok = winspool.WritePrinter(h, data, len(data), ctypes.byref(ecrit))
        winspool.EndPagePrinter(h)
        winspool.EndDocPrinter(h)
        print("envoyé : %d / %d octets (ok=%s)" % (ecrit.value, len(data), bool(ok)))
    finally:
        winspool.ClosePrinter(h)


if __name__ == "__main__":
    envoyer(sys.argv[1], *(sys.argv[2:3]))

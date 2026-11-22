# Canon PRO-100S on Linux (USB, /dev/usb/lp*): reads ink levels with the read-only BJL "SSR=" status command.
# Usage: python3 ink_levels_linux.py                 -> print JSON once
#        python3 ink_levels_linux.py --loop FILE     -> write JSON to FILE every 60 s
import glob, json, os, select, sys, time
import xml.etree.ElementTree as ET

DEST = None
PERIODE = 60
NOMS = {"PBK": ("Noir", "noir"), "GY": ("Gris", "gris"), "LGY": ("Gris clair", "grisclair"),
        "C": ("Cyan", "cyan"), "M": ("Magenta", "magenta"), "Y": ("Jaune", "jaune"),
        "LC": ("Cyan photo", "cyanphoto"), "LM": ("Magenta photo", "magentaphoto")}
ORDRE = ["PBK", "GY", "LGY", "C", "M", "Y", "LC", "LM"]


def commande(cmd):
    L = len(cmd)
    return b"\x1b[K" + bytes([(L + 4) & 255, 0]) + b"\x00\x1e" + bytes([0, L + 2]) + cmd


def trouver():
    for p in sorted(glob.glob("/sys/class/usbmisc/lp*")):
        try:
            ident = open(p + "/device/ieee1284_id", encoding="utf-8", errors="replace").read()
        except OSError:
            ident = ""
        if "PRO-100S" in ident or "Canon" in ident:
            return "/dev/usb/" + os.path.basename(p)
    raise RuntimeError("Imprimante Canon non vue en USB (éteinte ou débranchée)")


def interroger(dev, delai=4.0):
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, commande(b"SSR=BST,SFA,CHD,CIL,CIR,HRI,DBS,DWS,DOC,DSC,DJS,CTK,HCF;"))
        brut = b""; fin = time.time() + delai
        while time.time() < fin:
            r, _, _ = select.select([fd], [], [], 0.3)
            if r:
                try:
                    morceau = os.read(fd, 4096)
                except BlockingIOError:
                    morceau = b""
                brut += morceau
                if b"</cmd>" in brut:
                    break
        return brut
    finally:
        os.close(fd)


def lire():
    brut = interroger(trouver()).decode("utf-8", "replace")
    if "<?xml" not in brut:
        raise RuntimeError("Pas de réponse de l'imprimante (%d octets)" % len(brut))
    racine = ET.fromstring(brut[brut.index("<?xml"):brut.rindex("</cmd>") + 6])
    ns = {"ivec": "http://www.canon.com/ns/cmd/2008/07/common/", "cijn": "http://www.canon.com/ns/wdp/2008/01/print/"}
    statut = (racine.findtext(".//ivec:status", "", ns) or "").strip()
    detail = (racine.findtext(".//ivec:status_detail", "", ns) or "").strip()
    champs = {}
    for part in (racine.findtext(".//cijn:ReadData", "", ns) or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1); champs[k] = v
    cir = dict(p.split("=") for p in champs.get("CIR", "").split(",") if "=" in p)

    def paires(cle):
        out = {}
        for bloc in champs.get(cle, "").split(",/,"):
            t = bloc.split(",")
            if len(t) >= 2:
                out[t[0]] = t[1]
        return out
    ctk, tni = paires("CTK"), paires("TNI")
    encres = [{"code": c, "nom": NOMS[c][0], "couleur": NOMS[c][1], "pct": int(cir[c]),
               "reference": tni.get(c, ""), "etat": ctk.get(c, "")} for c in ORDRE if c in cir]
    if not encres:
        raise RuntimeError("Réponse sans niveaux (CIR absent)")
    return {"ok": True, "horodatage": int(time.time()), "statut": statut, "statut_detail": detail,
            "encres": encres, "source": "USB, BJL SSR command (CIR, 10 % steps)"}


def cycle():
    try:
        return lire()
    except Exception as e:
        return {"ok": False, "horodatage": int(time.time()), "erreur": f"{type(e).__name__}: {e}"}


def ecrire(obj):
    tmp = DEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, DEST)


if __name__ == "__main__":
    if "--loop" not in sys.argv:
        print(json.dumps(cycle(), ensure_ascii=False, indent=1)); sys.exit(0)
    DEST = sys.argv[sys.argv.index("--loop") + 1]
    while True:
        ecrire(cycle())
        time.sleep(PERIODE)

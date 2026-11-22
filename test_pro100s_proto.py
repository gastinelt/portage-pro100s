#!/usr/bin/env python3
"""Tests unitaires pour le prototype Canon PIXMA PRO-100S."""

from __future__ import annotations

import os
import re
import tempfile
import unittest

import pro100s_proto
import rastertopro100s
import rejouer_usb_linux


DRIVER_DLL = (
    r"C:\Windows\System32\DriverStore\FileRepository"
    r"\pro-100s6.inf_amd64_0c1aeb211c1e34b7\CNM_0454.DLL"
)


class TestPro100sProto(unittest.TestCase):
    def test_packbits_roundtrip(self) -> None:
        samples = [
            b"",
            b"\x00",
            b"\x00" * 50,
            b"\xff" * 200,
            b"\x01\x02\x03\x04\x05",
            b"AAABBBCCCDEFGH" + b"\xaa" * 130 + b"\x01\x02",
        ]
        for s in samples:
            enc = pro100s_proto.packbits_encode(s)
            dec = pro100s_proto.packbits_decode(enc)
            self.assertEqual(dec, s)

    def test_generate_and_parse_roundtrip(self) -> None:
        stream = pro100s_proto.build_experimental_stream(
            width_px=320,
            height_px=16,
            mode="plain_draft_600_4ch_1bit",
            paper="a4_bordered",
            job_id=42,
            compress=True,
        )
        tokens = pro100s_proto.parse_stream(stream)
        summary = pro100s_proto.summarize_tokens(tokens, len(stream))

        self.assertEqual(summary["ivec_operations"], ["StartJob", "VendorCmd", "EndJob"])
        self.assertEqual(summary["first_esc_c"], {"flags_hex": "0x30", "media_hex": "0x00", "quality": 1})
        self.assertEqual(summary["first_esc_d"], {"dpi_x": 600, "dpi_y": 600})
        self.assertEqual(summary["first_esc_t"]["active_channels"], 4)
        self.assertEqual(summary["first_esc_t"]["total_channels"], 8)
        self.assertEqual(summary["first_esc_p"]["paper_mm"], [210.0, 297.0])
        self.assertFalse(summary["first_esc_p"]["borderless"])
        self.assertEqual(summary["counts"].get("ESC(F"), 16 * 4)
        self.assertEqual(summary["counts"].get("FF"), 1)
        self.assertNotIn("RAW", summary["counts"])

    def test_decode_a4_borderless_preset(self) -> None:
        info = pro100s_proto.decode_esc_p(pro100s_proto.ESC_P_PRESETS["a4_borderless"])
        self.assertEqual(info["paper_mm"], [210.0, 297.0])
        self.assertTrue(info["borderless"])

    def test_replay_verifier_dry_run(self) -> None:
        stream = pro100s_proto.build_experimental_stream(width_px=64, height_px=8)
        with tempfile.NamedTemporaryFile(suffix=".prn", delete=False) as tf:
            tf.write(stream)
            path = tf.name
        try:
            rc = rejouer_usb_linux.main([path])
            self.assertEqual(rc, 0)
        finally:
            os.remove(path)

    def test_cups_filter_rejects_invalid_magic(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".ras", delete=False) as tf:
            tf.write(b"NOT_A_RASTER")
            path = tf.name
        try:
            rc = rastertopro100s.main(["1", "theo", "test", "1", "", path])
            self.assertEqual(rc, 1)
        finally:
            os.remove(path)

    @unittest.skipUnless(os.path.exists(DRIVER_DLL), "Pilote Windows CNM_0454.DLL absent (normal sous Linux)")
    def test_real_driver_tables_match_decoder(self) -> None:
        with open(DRIVER_DLL, "rb") as f:
            raw = f.read()

        # Vérifier la présence des 3 enveloppes XML clés
        self.assertIn(b"<ivec:operation>StartJob</ivec:operation>", raw)
        self.assertIn(b"<vcn:ijoperation>ModeShift</vcn:ijoperation>", raw)
        self.assertIn(b"<ivec:operation>EndJob</ivec:operation>", raw)

        # Vérifier les 71 entrées ESC(t (27 octets)
        esc_t_hits = list(re.finditer(rb"\x1b\(t\x1b\x00", raw))
        self.assertEqual(len(esc_t_hits), 71)
        for m in esc_t_hits:
            dec = pro100s_proto.decode_esc_t(raw[m.start() + 5 : m.start() + 32])
            self.assertEqual(dec["total_channels"], 8)
            self.assertIn(dec["active_channels"], (4, 8))

        # Vérifier les 43 géométries ESC(p (46 octets), dont A4 et A3+
        esc_p_hits = list(re.finditer(rb"\x1b\(p\x2e\x00", raw))
        self.assertEqual(len(esc_p_hits), 43)
        sizes = {
            tuple(pro100s_proto.decode_esc_p(raw[m.start() + 5 : m.start() + 51])["paper_mm"])
            for m in esc_p_hits
        }
        self.assertIn((210.0, 297.0), sizes)  # A4
        self.assertIn((329.0, 483.0), sizes)  # A3+


if __name__ == "__main__":
    unittest.main()

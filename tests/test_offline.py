"""Offline tests: synthetic MP4 layout + fragment decrypt against a fake
decrypt server (XOR pipe). No network, no Apple bits, no credentials."""

import socket
import struct
import threading
import unittest

from fairplay_decrypter import fragdecrypt, mp4, verify


def box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def synth_fragment(sizes):
    """One moof+mdat: tfhd, trun (data_offset + per-sample sizes), senc
    whole-sample prot entries. Offsets are exact by construction."""
    count = len(sizes)
    tfhd = box(b"tfhd", struct.pack(">I", 0x0) + struct.pack(">I", 1))
    # pass 1 with placeholder offset to measure the moof
    trun = box(b"trun", struct.pack(">IIi", 0x201, count, 0) +
               b"".join(struct.pack(">I", s) for s in sizes))
    senc = box(b"senc", struct.pack(">II", 0x2, count) +
               b"".join(struct.pack(">H", 0) for s in sizes))
    data_off = len(box(b"moof", box(b"traf", tfhd + trun + senc))) + 8
    trun = box(b"trun", struct.pack(">IIi", 0x201, count, data_off) +
               b"".join(struct.pack(">I", s) for s in sizes))
    moof = box(b"moof", box(b"traf", tfhd + trun + senc))
    assert data_off == len(moof) + 8
    mdat_pay = b"".join(bytes(((i + j) % 256 for j in range(s))) for i, s in enumerate(sizes))
    return moof + box(b"mdat", mdat_pay), mdat_pay


class FakeDecryptServer:
    """XOR-with-0x55 pipe speaking the decrypt-port framing."""

    def __init__(self):
        self.ls = socket.socket()
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.port = self.ls.getsockname()[1]
        self.ls.listen(8)
        self.arms = 0
        self._stop = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        self.ls.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = self.ls.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        try:
            c.settimeout(5)
            # arm: two wire_strings (len-prefixed)
            for _ in range(2):
                n = c.recv(1)[0]
                while n:
                    got = c.recv(n)
                    n -= len(got)
            self.arms += 1
            while True:
                hdr = c.recv(4)
                if len(hdr) < 4:
                    return
                n = struct.unpack("<I", hdr)[0]
                if n == 0:
                    return
                data = b""
                while len(data) < n:
                    got = c.recv(n - len(data))
                    if not got:
                        return
                    data += got
                c.sendall(bytes(b ^ 0x55 for b in data))
        except (OSError, IndexError):
            pass
        finally:
            c.close()

    def close(self):
        self._stop.set()


class Tests(unittest.TestCase):
    def test_layout_and_spans(self):
        seg, _ = synth_fragment([64, 48, 80])
        samples = mp4.sample_layout(seg, 0)
        self.assertEqual([s["size"] for s in samples], [64, 48, 80])
        for s in samples:
            self.assertGreater(s["offset"], 0)
            self.assertLessEqual(s["offset"] + s["size"], len(seg))

    def test_truncation_rejected(self):
        seg, _ = synth_fragment([64, 48])
        with self.assertRaises(ValueError):
            mp4.sample_layout(seg[:-10], 0)

    def test_decrypt_roundtrip_batched(self):
        seg, _ = synth_fragment([64] * 40 + [48] * 8)
        srv = FakeDecryptServer()
        try:
            pt = fragdecrypt.decrypt_fragment(seg, "1", "skd://x", srv.port,
                                              batch=8, workers=4)
        finally:
            srv.close()
        # headers untouched, samples XORed
        self.assertEqual(pt[:seg.find(b"mdat")], seg[:seg.find(b"mdat")])
        samples = mp4.sample_layout(seg, 0)
        for s in samples:
            o, ln = s["offset"], s["size"] & ~15
            self.assertEqual(pt[o:o + ln], bytes(b ^ 0x55 for b in seg[o:o + ln]))
        self.assertGreaterEqual(srv.arms, 2)  # batching used several arms


    def test_verify_ec3(self):
        seg, _ = synth_fragment([64] * 10)
        samples = mp4.sample_layout(seg, 0)
        good = bytearray(seg)
        for s in samples:
            good[s["offset"]:s["offset"] + 2] = b"\x0bw"
        verify.check(bytes(good), "ec-3")
        with self.assertRaises(ValueError):
            verify.check(seg, "ec-3")

    def test_verify_alac(self):
        seg, _ = synth_fragment([64] * 10)
        samples = mp4.sample_layout(seg, 0)
        good = bytearray(seg)
        for s in samples:
            good[s["offset"]:s["offset"] + 4] = b"\x20\x00\x04\x04"
        verify.check(bytes(good), "alac")
        with self.assertRaises(ValueError):
            verify.check(seg, "alac")


if __name__ == "__main__":
    unittest.main()

"""Offline tests for runtime staging: fake install trees, zip fetch, config
resolution. No network, no Apple bits."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fairplay_decrypter import config, libs


def make_install(root: Path, with_secret: bool = True) -> Path:
    (root / "rootfs/system/bin").mkdir(parents=True)
    (root / "rootfs/system/lib64").mkdir(parents=True)
    (root / "rootfs/data/data/com.apple.android.music/files").mkdir(parents=True)
    (root / "rootfs/dev").mkdir(parents=True)
    (root / "rootfs/proc").mkdir(parents=True)
    wrapper = root / "wrapper"
    wrapper.write_bytes(b"#!/bin/sh\n")
    wrapper.chmod(0o755)
    (root / "rootfs/system/bin/main").write_bytes(b"main")
    (root / "rootfs/system/bin/linker64").write_bytes(b"linker")
    for name in libs.LIB_NAMES:
        (root / "rootfs/system/lib64" / name).write_bytes(b"x" * 32)
    if with_secret:
        (root / "rootfs/data/data/com.apple.android.music/files/MUSIC_TOKEN").write_text("SECRET")
    return root


class Tests(unittest.TestCase):
    def test_install_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_install(Path(tmp) / "inst")
            self.assertTrue(libs.is_install(inst))
            self.assertEqual(libs.missing(inst), [])
            self.assertEqual(len(libs.sizes(inst)), len(libs.LIB_NAMES))

            (inst / "rootfs/system/lib64" / libs.LIB_NAMES[0]).unlink()
            self.assertFalse(libs.is_install(inst))
            self.assertIn(f"rootfs/system/lib64/{libs.LIB_NAMES[0]}", libs.missing(inst))

    def test_stage_skips_account_state_and_runtime_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_install(Path(tmp) / "inst")
            dest = Path(tmp) / "staged" / "service"
            self.assertEqual(libs.stage(inst, dest), [])
            self.assertTrue(libs.is_install(dest))
            self.assertTrue(os.access(dest / "wrapper", os.X_OK))
            self.assertFalse((dest / "rootfs/data/data/com.apple.android.music/files/MUSIC_TOKEN").exists())
            self.assertFalse((dest / "rootfs/dev").exists())
            self.assertFalse((dest / "rootfs/proc").exists())
            files = dest / "rootfs/data/data/com.apple.android.music/files"
            self.assertTrue(files.is_dir())

    def test_stage_is_idempotent_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_install(Path(tmp) / "inst")
            dest = Path(tmp) / "service"
            libs.stage(inst, dest)
            (dest / "keep.txt").write_text("x")
            self.assertEqual(libs.stage(inst, dest), [])
            self.assertTrue((dest / "keep.txt").exists())

    def test_fetch_from_file_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inst = make_install(tmp / "inst")
            archive = tmp / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for p in inst.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(inst))
            dest = tmp / "staged"
            self.assertEqual(libs.fetch(dest, url=archive.as_uri()), [])
            self.assertTrue(libs.is_install(dest))
            self.assertFalse((dest / "rootfs/data/data/com.apple.android.music/files/MUSIC_TOKEN").exists())

    def test_service_dir_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inst = make_install(tmp / "inst")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp / "cfg"),
                                              "XDG_DATA_HOME": str(tmp / "data")}, clear=False):
                os.environ.pop("FPDECRYPT_WRAPPER", None)
                os.environ.pop("FPDECRYPT_SERVICE", None)
                self.assertIsNone(config.service_dir())
                config.set_service_dir(inst)
                self.assertEqual(config.service_dir(), inst)
                # config file is private
                mode = (tmp / "cfg/fairplay-decrypter/config.json").stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)
                # env override wins
                os.environ["FPDECRYPT_WRAPPER"] = str(inst)
                self.assertEqual(config.service_dir(), inst)


if __name__ == "__main__":
    unittest.main()

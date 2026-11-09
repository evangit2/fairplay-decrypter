"""fairplay_decrypter CLI: login, ladder, dl, stream, status, doctor."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import __version__, config, ladder as ladder_mod, login as login_mod, service
from . import fragdecrypt, mp4, verify, wire


def _ports():
    return config.ports()


def cmd_login(args) -> int:
    ok = login_mod.login(args.user, args.password, fresh=args.fresh)
    return 0 if ok else 1


def cmd_status(_args) -> int:
    print(f"service: {'running' if service.is_running() else 'stopped'}")
    tok = config.music_token()
    print(f"session: {'signed in' if tok else 'not signed in'}", end="")
    if tok:
        print(f" (storefront {config.storefront()})", end="")
    print()
    print(f"device: {config.device_base()}")
    return 0


def cmd_doctor(_args) -> int:
    ok = True

    def check(name, good, hint=""):
        nonlocal ok
        print(("ok  " if good else "FAIL") + f" {name}" + ("" if good else f" -- {hint}"))
        ok = ok and good

    inst = config.wrapper_install()
    check("wrapper install", inst is not None, "set FPDECRYPT_WRAPPER=~/.lidra-wrapper/inst2")
    check("ffmpeg", shutil.which("ffmpeg") is not None, "install ffmpeg for remux/decode checks")
    r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    check("passwordless sudo (service mounts)", r.returncode == 0, "add a sudoers NOPASSWD rule")
    check("signed in", config.music_token() is not None, "run: fairplay_decrypter login")
    return 0 if ok else 1


def _pick_tier(adam: str, want: str | None):
    tiers = ladder_mod.ladder(adam, _ports()["manifest"])
    if want:
        for t in tiers:
            if t["id"] == want:
                return t
        raise SystemExit(f"unknown tier {want!r} (see: fairplay_decrypter ladder {adam})")
    return tiers[0]


def cmd_ladder(args) -> int:
    print(ladder_mod.describe(args.adam, _ports()["manifest"]))
    return 0


def _segments(tier: dict, seg: str):
    text = wire.fetch(tier["playlist"]).decode()
    entries = wire.media_entries(text, tier["playlist"])
    segs = entries["segments"]
    if seg == "all":
        return entries["init"], segs
    i = int(seg)
    return entries["init"], [segs[i]]


def _download(adam: str, tier: dict, segs, key_uri: str, workers: int, log=print):
    p = _ports()
    blobs = []
    external = bool(os.environ.get("FPDECRYPT_EXTERNAL"))
    for n, (url, span, _k) in enumerate(segs):
        ct = wire.fetch(url, span)
        attempt = 0
        while True:
            pt = fragdecrypt.decrypt_fragment(ct, adam, key_uri, p["decrypt"], workers=workers)
            try:
                if n == 0:
                    verify.check(pt, tier["codecs"])
                break
            except ValueError as e:
                attempt += 1
                if attempt > 1 or external:
                    raise SystemExit(f"verify failed ({e}); refusing to save noise")
                log("  verify failed, restarting service and retrying once...")
                service.stop()
                service.start()
        blobs.append(pt)
        log(f"  [{n + 1}/{len(segs)}] {len(pt) // 1024} KiB")
    return blobs


def cmd_dl(args) -> int:
    tier = _pick_tier(args.adam, args.tier)
    init_e, segs = _segments(tier, args.seg)
    key_uri = segs[0][2]
    print(f"{tier['label']} -- {len(segs)} segment(s), key {key_uri.split('/')[-2:]}")
    init_bytes = b""
    if init_e:
        u, sp = init_e
        init_bytes = wire.fetch(u, sp) if sp else wire.fetch(u)
    blobs = _download(args.adam, tier, segs, key_uri, args.workers)
    out = args.o or f"{args.adam}-{tier['id']}.m4a"
    with open(out, "wb") as f:
        f.write(init_bytes)
        for b in blobs:
            f.write(b)
    print(f"wrote {out} ({(len(init_bytes) + sum(map(len, blobs))) // 1024} KiB)")
    return 0


def cmd_stream(args) -> int:
    """Decrypt to stdout (init + segments) for piping into a player."""
    tier = _pick_tier(args.adam, args.tier)
    init_e, segs = _segments(tier, args.seg)
    key_uri = segs[0][2]
    out = sys.stdout.buffer
    if init_e:
        u, sp = init_e
        out.write(wire.fetch(u, sp) if sp else wire.fetch(u))
    for blob in _download(args.adam, tier, segs, key_uri, args.workers, log=lambda m: print(m, file=sys.stderr)):
        out.write(blob)
        out.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fairplay-decrypt", description="Apple Music FairPlay decrypter")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="sign in with an Apple ID (stores session, no 2FA next time)")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--fresh", action="store_true", help="wipe device identity and start over")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("ladder", help="list quality tiers for a song")
    p.add_argument("adam", help="song id (e.g. 268443097)")
    p.set_defaults(fn=cmd_ladder)

    p = sub.add_parser("dl", help="download a song (default: best tier, all segments)")
    p.add_argument("adam")
    p.add_argument("--tier", default=None)
    p.add_argument("--seg", default="all", help="segment index or 'all'")
    p.add_argument("-o", default=None)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_dl)

    p = sub.add_parser("stream", help="decrypt to stdout for piping into a player")
    p.add_argument("adam")
    p.add_argument("--tier", default=None)
    p.add_argument("--seg", default="all")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_stream)

    p = sub.add_parser("status", help="service + session state")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("doctor", help="environment checks")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("stop", help="stop the owned service instance")
    p.set_defaults(fn=lambda a: (service.stop(), 0)[1])
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if (args.cmd in ("ladder", "dl", "stream") and not service.is_running()
            and not os.environ.get("FPDECRYPT_EXTERNAL")):
        print("starting service...")
        try:
            service.start()
        except RuntimeError as e:
            print(f"cannot start service: {e}")
            return 1
    try:
        return args.fn(args)
    except (ConnectionError, TimeoutError, ValueError) as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

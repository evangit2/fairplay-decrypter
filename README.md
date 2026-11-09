# fairtunes

Source-driven Apple Music streaming/download CLI. CENC parsing, ladder,
and fragment assembly are pure source; per-song key contexts are borrowed
from a privately-owned local wrapper service instance (your own install —
Apple binaries are used in place, never vendored here).

## Install

```bash
pip install -e .
fairtunes doctor
```

`doctor` checks: a wrapper install (`FAIRTUNES_WRAPPER` or
`~/.lidra-wrapper/inst2`), ffmpeg, passwordless sudo (the service needs
mounts), and session state.

## Use

```bash
fairtunes login                       # Apple ID, 2FA once; device stays trusted
fairtunes ladder 268443097            # quality tiers for a song
fairtunes dl 268443097 --tier hirez192 -o song.m4a
fairtunes dl 268443097 --tier aac256 --seg 2 -o seg2.m4a
fairtunes stream 268443097 --tier atmos768 | ffplay -
fairtunes status | fairtunes stop
```

Tiers: `hirez192` (ALAC 192/24), `alac48`, `atmos768`, `atmos448`,
`aac256`, `aac128`, `heaac64` (+ binaural/downmix variants where offered).
Default tier is the highest-bandwidth rendition.

## How it works

- `fairtunes login` launches the owned service (`-L user:pass`, `-F`),
  feeds the 2FA code file when asked, and waits for `MUSIC_TOKEN`. Later
  starts reuse the trusted device with no 2FA.
- `ladder` reads the master playlist off the manifest port.
- `dl`/`stream` fetch init + segments over HTTPS, parse sample spans with
  `mp4.py`, and decrypt one whole sample per decrypt-port call in
  fresh-arm batches (default 64 samples x 4 workers). Output is
  init + plaintext segments: a playable `.m4a`.
- Verify-first: the first segment of every download is codec-checked in
  source (EC-3 sync / ALAC frame headers / AAC ADTS-decode with non-empty
  output). On failure with an owned instance, the service restarts and the
  segment retries once — a wedged or stale key context returns
  same-length noise, never an error, so silence is not proof. In external
  mode (`FAIRTUNES_EXTERNAL=1`, e.g. a Lidra-owned wrapper on 10020) a
  verify failure aborts instead of restarting someone else's service.

## Proven tiers (song 268443097, live runs)

| tier | segments | proof |
|---|---|---|
| `hirez192` ALAC 192/24 | 703 frag | 40/40 ALAC frame headers |
| `alac48` ALAC 48/24 | 175 frag | 60/60 ALAC frame headers |
| `atmos768` EC-3 | 468 frag | 0x0b77 syncs throughout |
| `aac256` AAC-LC | 703 frag | ADTS rewrap decodes to real PCM, zero errors |

Full-song hi-res: 324 MB in ~26 s (~12 MB/s) on 4 workers.

## Layout

- `src/fairtunes/mp4.py` — box walk, tenc, sample layout (bounds-checked)
- `src/fairtunes/wire.py` — manifest/decrypt/key-port clients
- `src/fairtunes/fragdecrypt.py` — batched fragment decryptor
- `src/fairtunes/service.py` — owned service lifecycle (process-group stop)
- `src/fairtunes/login.py`, `ladder.py`, `config.py`, `cli.py`
- `tests/test_offline.py` — synthetic-MP4 + fake-server tests, no network

## Notes

- One whole sample per decrypt call: the service is a byte pipe and
  decrypts whatever it is sent (headers would be mangled; sub-block runs
  would break CBC chaining). A whole large fragment on one connection
  stalls the service — hence fresh-arm batches.
- Placeholder key segments (`P000000000/s1/e1`) arm with id `0`; the
  switch marker is only for re-arming a live connection, never a fresh one.
- ffmpeg 6 cannot init Apple's 24-byte ALAC cookie (`extradata too small`);
  ALAC output is frame-verified, remuxing is a player-side concern.

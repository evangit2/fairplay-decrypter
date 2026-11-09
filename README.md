# fairplay-decrypter

Source-driven Apple Music streaming/download CLI. CENC parsing, ladder,
and fragment assembly are pure source; per-song key contexts are borrowed
from a privately-owned local wrapper service instance (your own install —
Apple binaries are used in place, never vendored here).

## Install

```bash
pip install -e .
fairplay-decrypt doctor
```

`doctor` checks: a wrapper install (`FPDECRYPT_WRAPPER` or
`~/.lidra-wrapper/inst2`), ffmpeg, passwordless sudo (the service needs
mounts), and session state.

## Use

```bash
fairplay-decrypt login                       # Apple ID, 2FA once; device stays trusted
fairplay-decrypt ladder 268443097            # quality tiers for a song
fairplay-decrypt dl 268443097 --tier hirez192 -o song.m4a
fairplay-decrypt dl 268443097 --tier aac256 --seg 2 -o seg2.m4a
fairplay-decrypt stream 268443097 --tier atmos768 | ffplay -
fairplay-decrypt status | fairplay_decrypter stop
```

Tiers: `hirez192` (ALAC 192/24), `alac48`, `atmos768`, `atmos448`,
`aac256`, `aac128`, `heaac64` (+ binaural/downmix variants where offered).
Default tier is the highest-bandwidth rendition.

## How it works

- `fairplay_decrypter login` launches the owned service (`-L user:pass`, `-F`),
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
  mode (`FPDECRYPT_EXTERNAL=1`, e.g. a Lidra-owned wrapper on 10020) a
  verify failure aborts instead of restarting someone else's service.

## Proven tiers (song 268443097, live runs)

| tier | segments | proof |
|---|---|---|
| `hirez192` ALAC 192/24 | 703 frag | 40/40 ALAC frame headers |
| `alac48` ALAC 48/24 | 175 frag | 60/60 ALAC frame headers |
| `atmos768` EC-3 | 468 frag | 0x0b77 syncs throughout |
| `aac256` AAC-LC | 703 frag | ADTS rewrap decodes to real PCM, zero errors |

Full-song hi-res: 324 MB in ~26 s (~12 MB/s). The ceiling is the
service's single-threaded crypto (~10-11 MB/s decrypt vs 75 MB/s fetch),
not this client: 1/2/4/8 workers all land within 8-11 MB/s, so the default
is 2 (less connection churn). That's ~9x realtime for 192/24 and ~40x for
AAC-256.

## Layout

- `src/fairplay_decrypter/mp4.py` — box walk, tenc, sample layout (bounds-checked)
- `src/fairplay_decrypter/wire.py` — manifest/decrypt/key-port clients
- `src/fairplay_decrypter/fragdecrypt.py` — batched fragment decryptor
- `src/fairplay_decrypter/service.py` — owned service lifecycle (process-group stop)
- `src/fairplay_decrypter/login.py`, `ladder.py`, `config.py`, `cli.py`
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

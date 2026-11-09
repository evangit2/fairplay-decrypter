# fairplay-decrypter

Source-driven Apple Music streaming/download CLI. CENC parsing, quality
ladder, and fragment assembly are pure source; per-song key contexts come
from a FairPlay runtime on your own machine.

## Install

```bash
pip install -e .
fairplay-decrypt setup       # stage the local FairPlay runtime
fairplay-decrypt doctor
```

`setup` stages the FairPlay runtime into
`~/.local/share/fairplay-decrypter/service` and records it in
`~/.config/fairplay-decrypter/config.json` (mode 0600). It uses a local
install when one exists (`--from PATH` points at one explicitly, `--force`
replaces a staged copy), otherwise it downloads the prebuilt runtime once
(~47 MB). No separate install or running service is needed after setup.
Account state is never copied — `login` creates a fresh session on this
machine.

`doctor` checks: staged runtime, ffmpeg, passwordless sudo (the runtime needs
mounts), and session state.

## Use

```bash
fairplay-decrypt login                       # Apple ID, 2FA once; device stays trusted
fairplay-decrypt ladder 268443097            # quality tiers for a song
fairplay-decrypt dl 268443097 --tier hirez192 -o song.m4a
fairplay-decrypt dl 268443097 --tier aac256 --seg 2 -o seg2.m4a
fairplay-decrypt stream 268443097 --tier atmos768 | ffplay -
fairplay-decrypt status | fairplay-decrypt stop
```

Tiers: `hirez192` (ALAC 192/24), `alac48`, `atmos768`, `atmos448`,
`aac256`, `aac128`, `heaac64` (+ binaural/downmix variants where offered).
Default tier is the highest-bandwidth rendition.

## How it works

- `login` starts the runtime (`-L user:pass`, `-F`), feeds the 2FA code file
  when asked, and waits for `MUSIC_TOKEN`. Later starts reuse the trusted
  device with no 2FA.
- `ladder` reads the master playlist off the manifest port.
- `dl`/`stream` fetch init + segments over HTTPS, parse sample spans with
  `mp4.py`, and decrypt one whole sample per decrypt-port call in
  fresh-arm batches (default 64 samples x 2 workers). Output is
  init + plaintext segments: a playable `.m4a`.
- Verify-first: the first segment of every download is codec-checked in
  source (EC-3 sync / ALAC frame headers / AAC ADTS-decode with non-empty
  output). On failure with a local runtime the service restarts and the
  segment retries once — a wedged or stale key context returns
  same-length noise, never an error, so silence is not proof. In external
  mode (`FPDECRYPT_EXTERNAL=1`) a verify failure aborts instead of
  restarting a service this client does not own.

## Proven tiers (song 268443097, live runs)

| tier | segments | proof |
|---|---|---|
| `hirez192` ALAC 192/24 | 703 frag | 40/40 ALAC frame headers |
| `alac48` ALAC 48/24 | 175 frag | 60/60 ALAC frame headers |
| `atmos768` EC-3 | 468 frag | 0x0b77 syncs throughout |
| `aac256` AAC-LC | 703 frag | ADTS rewrap decodes to real PCM, zero errors |

Full-song hi-res: 324 MB in ~26 s (~12 MB/s). The ceiling is the runtime's
single-threaded crypto (~10-11 MB/s decrypt vs 75 MB/s fetch), not this
client: 1/2/4/8 workers all land within 8-11 MB/s, so the default is 2
(less connection churn). That's ~9x realtime for 192/24 and ~40x for
AAC-256.

## Layout

- `src/fairplay_decrypter/mp4.py` — box walk, tenc, sample layout (bounds-checked)
- `src/fairplay_decrypter/wire.py` — manifest/decrypt/key-port clients
- `src/fairplay_decrypter/fragdecrypt.py` — batched fragment decryptor
- `src/fairplay_decrypter/service.py` — runtime lifecycle (process-group stop)
- `src/fairplay_decrypter/libs.py` — install staging + verification
- `src/fairplay_decrypter/login.py`, `ladder.py`, `config.py`, `cli.py`
- `tests/test_offline.py` — synthetic-MP4 + fake-server tests, no network
- `tests/test_setup.py` — install staging/verification, no network

## Notes

- Per-run override: `FPDECRYPT_SERVICE=/path/to/install fairplay-decrypt …`
  (ports: `FPDECRYPT_PORT_DECRYPT`, `_MANIFEST`, `_ACCOUNT`, `_KEY`).
- No keys, tokens, or account databases are committed. Session state lives
  under `~/.local/share/fairplay-decrypter/device` and stays out of git.
- One whole sample per decrypt call: the runtime is a byte pipe and
  decrypts whatever it is sent (headers would be mangled; sub-block runs
  would break CBC chaining). A whole large fragment on one connection
  stalls the service — hence fresh-arm batches.
- Placeholder key segments (`P000000000/s1/e1`) arm with id `0`; the
  switch marker is only for re-arming a live connection, never a fresh one.
- ffmpeg 6 cannot init Apple's 24-byte ALAC cookie (`extradata too small`);
  ALAC output is frame-verified, remuxing is a player-side concern.

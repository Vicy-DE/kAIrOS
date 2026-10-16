# Public kAIrOS images — full and minimal (R87, 2026-10-16)

The owner wants two shareable disk images of v7.5-2: a **full** one and a **minimal** one, with every private bit
removed. Muse and the Gemma LLM go into an image only if their licences allow it; what may not ship downloads at the
first start. This report is the code side: what the repository now provides, how it was tested, and exactly what the
image build has to do.

The licence research behind it (read on 2026-10-16, outside the repository) found:

- **The heretic Gemma 4 is Apache-2.0.** It ships in both images, with the licence and an attribution note.
- **Muse cannot be redistributed.** Neither civitai's permissions nor its Terms grant redistribution outside civitai, and
  civitai serves the file only to a logged-in account. Muse becomes an optional download with **the user's own** civitai
  API key. It is never baked and never mirrored.
- **Everything else redistributes with its notices.** The Qwen-Image-2.1 editor is under the Qwen Research licence
  (redistribution with LICENSE + NOTICE; use "research or evaluation"); the FreeDict GPL / AGPL dictionaries need their
  licence texts and their source.

Integrator decisions: the public default picture model is **Z-Image-Turbo** (the `image_classic` set, Apache-2.0, all
URLs anonymous); stock Krea 2 is not used (its licence demands content filters); the owner's private images keep Muse —
public behaviour switches on a build-time profile.

## 1. What differs

| | the owner's images (profile `private`) | public images (profile `public`) |
|---|---|---|
| profile | no `/etc/kairos/profile` | `/etc/kairos/profile` = `public` (the prep script writes it) |
| picture model | Muse (baked), `safe` = Muse + SFW rules | **Z-Image-Turbo** with Muse's prompt rules (`krea-neutral`, `krea-18plus` on an adult request), `safe` = Z-Image + the SFW rules — until the user adds Muse; then exactly the owner's sections |
| language model | the heretic Gemma 4 | the same file, baked in both public images |
| models on the image | all baked | **full**: everything except Muse; **minimal**: the LLM only, the rest at first boot |
| SSH | enabled, key-only (the rig's key) | **disabled**, no keys; new host keys at the first boot; users run `systemctl enable --now ssh` |
| WiFi, logs, histories, machine-id, … | the build's | removed (§6) |
| licences | the same folder | `/usr/share/doc/kairos/model-licences/` + (full image) the FreeDict sources |

The profile changes nothing for the owner: with no profile file, `public.Profile.apply()` returns the tier manifest
**object itself**, untouched. The test `pp_private_kernel_unchanged` boots the real kernel without a profile and checks
that the manifest it serves equals the tier JSON, that there is no `muse` alias and no first-boot downloads, and that the
new key/download routes answer 409.

## 2. The profile (`kernel/public.py`, `manifests/profile-public.json`)

`/etc/kairos/profile` holding exactly `public` (case-insensitive; `KAIROS_PROFILE` / `KAIROS_PROFILE_FILE` override it for
tests) applies the overlay on top of the tier manifest:

- **While Muse is not installed** (its three files are not on disk at their manifest size):
  - `image` and `image_safe` = the tier's `image_classic` files and tuned arguments, with `krea-neutral` / `krea`
    (`expander_parallel` 2). The R80 menubar names it "Z-Image-Turbo" / "Z-Image-Turbo SFW". `classic` stays the same
    model without prompt expansion.
  - `image_muse` = the tier's Muse section as an add-on with `auth: civitai` and the licence summary; the settings alias is
    `muse`.
- **Once Muse is installed** the tier's own `image` / `image_safe` apply — as on the owner's images — and `image_muse`
  disappears. The kernel re-applies the overlay in place when the user's Muse download completes.
- `vision_encoder`: the tier's `image.text_encoder` (Qwen3-VL-4B: Q4_K_M on 8 GB, Q8_0 above) as its own entry, and
  `vision.weights` point at it, because Z-Image's own text encoder is a text-only Qwen3-4B.
- `knowledge_zim`: the offline Wikipedia (`wikipedia_en_all_mini_2026-06.zim`) as a concrete file entry with kiwix's
  sha256.
- `sha256`: pins for the two Muse-set files the tier manifests carry without one.

## 3. The first start and the first-boot downloads

The **essentials** are the files the owner's images bake. A public image may leave any of them out (the minimal image
leaves out everything but the LLM):

| item | files | bytes (8 GB tier) |
|---|---|---|
| the language model (heretic Gemma 4) | 1 | 13 917 722 144 |
| the picture model (Z-Image-Turbo + Qwen3-4B encoder + VAE) — skipped while Muse is installed | 3 | 6 515 956 452 |
| the translation model (Index-Translate-2B, fast) | 1 | 1 312 164 352 |
| the dictionaries (BEOLINGUS + 37 FreeDict pairs) | 38 | 118 463 187 |
| the vision encoder (Qwen3-VL-4B Q4_K_M) | 1 | 2 497 281 664 |
| the offline Wikipedia (ZIM 2026-06) | 1 | 12 531 679 311 |
| **all** | 45 | **36 893 267 110** (the minimal image fetches 22 975 544 966 of it) |

Every URL is anonymous (Hugging Face, kiwix, FreeDict, TU Chemnitz). On the 8 GB tier every file is sha256-pinned; the
larger tiers' LLM and Q8 vision encoder are not pinned yet.

**How they download.**

- They run in the kernel's background at start-up, with aria2c (R47's mechanism), one file at a time.
- **Resumable:** an aria2 control file stays next to a partial file. A pause or a restart continues from it with a Range
  request.
- **Checked:** the size, then the sha256 (sha512 too where the manifest has it). A file whose checksum differs is fetched
  a second time, then the item stops with the reason and the others go on. A wrong file is never kept.
- **Offline:** a network failure puts the download into `waiting`. A TCP probe to the server runs before each retry
  (backoff 5 → 300 s), and the download resumes when the server answers again.
- **The user's pause** survives a restart. A disk without room stops the item with the sizes in the message.
- **Never blocking:** aria2c is a child process, and the checksum runs in a thread.

**While a file is missing**, each feature answers a sentence instead of failing. The sentence comes from the item's state:
"… is downloading (37 %) — pictures work when it is done", "… is waiting for the network", "… is queued", "… needs a
download (6.5 GB, paused) — resume it in ⚙ Models & licences", or "… could not be downloaded (reason)". It reaches the user
through:

- `/api/image`, `/api/images`, `/api/wallpaper`: 503 `{error, missing: true}` — no job, no engine call;
- the sd floor (`MM.sd_batch`): every other picture path (pages, apps, icons, the forge);
- the LLM start: llama-server's model file, through the engine-missing middleware;
- vision, translation and `/api/dict`;
- `/api/search` → `knowledge_note`;
- `vibe.models.download("default")` → `{essential: true, note}`.

The bridge's `vibe.image()` now passes the kernel's sentence through instead of "no job was started".

**Where it shows:**

- **The first-start dialog** (a public image, first boot): what is being fetched and its size, plus the Muse prompt (§4).
- **The menubar chip ⬇:** `⬇ models 37 %`, `⏸ … · offline`, `⏸ paused` or `⚠`. A click opens the panel.
- **The panel ⚙ Models & licences** (`shell/models.js`, the OS's own window): every item with its state and progress,
  plus Pause / Resume / Retry / Download now. It is reached from the chip, About kAIrOS → Models & downloads, the
  first-start dialog, and apps through `vibe.system.models()`; the Settings app's prompt asks for a Models button (`vibe.system.models()`; the prompt had about 60
  characters of room left in the 8 GB budget, so the Settings API sheet is unchanged and docs/API.md documents the call).

API: `GET /api/downloads`, `POST /api/downloads {action}` (guarded), `GET /api/status` → `downloads` (docs/API.md).

## 4. Muse with the user's own civitai key

- **Where the key is entered:** the first-start dialog and the panel ask "Add Muse (needs a free civitai account): paste
  your API key". The key is entered only there, in the OS's own window — never in a generated app.
  - The field is a password field. It is emptied the moment Save is pressed, and the page never shows the key again (it
    only says "a key is stored").
  - The 2-second panel refresh never redraws the field while the user is typing in it.
- **Storage:** `POST /api/muse/key` stores the key at `/var/lib/kairos/secrets/civitai.key`: 0600 in a 0700 directory,
  root-only (the kernel runs as root). `{key: null}` removes it. The answer is `{ok, key_set}` only.
- **The licence first:** the licence summary is shown before the download and must be ticked.
  `POST /api/muse/download` without `accept_licence: true` is 409 `needs_licence`, and without a key 409 `needs_key`. The
  summary says:
  - credit Stable Yogi;
  - not part of kAIrOS: the user's own download under civitai's Terms;
  - the creator's permissions: no commercial use beyond RentCivit, no derivatives;
  - the Krea 2 licence and AUP apply, with the exact Krea notice line;
  - never minors, never intimate content of real people.
- **Where the key goes:** only to the configured URL's own origin, as `Authorization: Bearer` (the default) or `?token=`.
  - The URL is `muse.auth.url` in the overlay, `https://civitai.com/api/download/models/3258948`; `KAIROS_MUSE_URL`
    overrides it. The mode is `auth.mode`, the panel's choice, or `KAIROS_CIVITAI_AUTH`.
  - An app cannot choose where the key goes.
  - Redirects are followed by hand: civitai's 307 to its CDN is followed **without** the key.
- **The download:** the encoder and VAE files Muse needs come anonymously via aria2c. The Muse file resumes from its
  `.part` file with Range, and its size and sha256 are checked.
- **Errors and logs:** an error text has the key and every query string removed before it reaches the log or the page. A
  refused key reads "civitai refused the download (HTTP 401) — check the API key …".
- **When Muse is in:** the profile re-applies and Muse is the default picture model.
- **Tested against mocks only** (no real key exists here): a mock civitai and a mock CDN on another port. The test proves:
  - the key reached civitai's origin only;
  - the CDN and the anonymous file server saw neither the header nor the token;
  - the key is in no response, no kernel output, no log, and no file other than its store.

## 5. Licences

`iso/config/includes.chroot/usr/share/doc/kairos/model-licences/` (34 files, 404 KB) becomes
`/usr/share/doc/kairos/model-licences/` on the image:

| folder | what | files |
|---|---|---|
| `gemma-4-heretic/` | the LLM, Apache-2.0 | LICENSE (apache.org's text, sha `cfc7749b…`), ATTRIBUTION.txt (Google DeepMind; llmfan46's Heretic v1.2.0; mradermacher's i1 GGUF; sha) |
| `z-image-turbo/` | the public picture model | LICENSE (Tongyi-MAI's Apache text), LICENSE-Qwen3-4B-Instruct-2507, ATTRIBUTION.txt |
| `qwen3-vl/`, `qwen-image-vae/` | encoders, VAE | LICENSE, ATTRIBUTION.txt |
| `qwen-image-2.1/` | the ✨ editor (Qwen Research) | LICENSE.txt, NOTICE.txt (already shipped since R85) |
| `index-translate/` | translation | LICENSE, LICENSE-Qwen3.5-2B, ATTRIBUTION.txt |
| `wan-2.2/`, `ace-step-1.5/` | video, music | LICENSE(.txt) (Wan `c71d239d…`, MIT `05a6bce4…`), ATTRIBUTION.txt (+ LICENSE-Qwen3) |
| `dictionaries/` | BEOLINGUS, FreeDict, WikDict, JMdict | GPL-2.0, GPL-3.0, AGPL-3.0, CC-BY-SA-3.0, ATTRIBUTION.txt, SOURCE.txt |
| `wikipedia/` | the ZIM (+ Wiktionary add-ons) | CC-BY-SA-4.0.txt, ATTRIBUTION.txt |
| `krea-2-muse/` | Muse — **not distributed**; the user's download | NOTICE.txt (credit, permissions, the exact Krea 2 notice line) |
| `qwen2.5-coder/`, `chroma1-hd/` | add-ons a user may download | LICENSE, ATTRIBUTION.txt |

- `README.txt` names every folder.
- `index.json` is the machine-readable list behind the **Licences view**: About kAIrOS → Licences, or the panel's last
  section.
  - `GET /api/licences` lists every model with its licence, attribution, texts and `installed` (from the files on disk;
    file families like `dict/…` and `wiktionary_…` by prefix).
  - `GET /api/licences/<id>/<file>` serves one text; anything else is 404.
- The texts were fetched from their upstreams on 2026-10-16. The Apache, Wan and MIT texts match the hashes in the
  licence research.
- The Z-Image components' licences (Tongyi-MAI/Z-Image-Turbo, leejet's GGUF, Comfy-Org's VAE repack, unsloth's
  Qwen3-4B-Instruct-2507) and the add-ons' licences were read from the Hugging Face API: all apache-2.0, except
  Qwen/Qwen-Image-2.1 (qwen-research).

**FreeDict sources.** The image build fetches them; they are not repository material.
`scripts/fetch-licence-sources.sh --root ROOT` puts the 8 `.src.tar.xz` tarballs (36 004 656 B) into
`/usr/share/doc/kairos/sources/freedict/`:

- each tarball is checked against the sha512 from FreeDict's `freedict-database.json`;
- `SHA512SUMS` and a `README.txt` are written next to them;
- a file already there and correct is kept;
- `--check` only verifies.

It was run for real on 2026-10-16: 8/8 files, 36 004 656 B, `sha512sum -c` OK.

## 6. The prep script (`scripts/public-image-prep.sh`)

```
bash scripts/public-image-prep.sh ROOT [--esp ESP] [--patterns FILE]           # prepare, then its own verify
bash scripts/public-image-prep.sh ROOT [--esp ESP] [--patterns FILE] --verify  # read-only; exit 1 on any finding
```

`ROOT` is the mounted image root; the script refuses `/`, or a directory without `opt/vibeos` + `etc/os-release`. `ESP`
defaults to `ROOT/boot/efi`. The script is idempotent: a second prepare reports 0 changes, and the test checks the tree is
byte-identical.

**What prepare removes or resets:**

- **network:** NetworkManager system-connections (the rendered WiFi) and NM state (leases, seen BSSIDs, the secret
  key); wpa_supplicant configs with a network/psk; DHCP leases; Bluetooth pairings;
- **ssh:** every `authorized_keys`, `~/.ssh` private keys and known_hosts, the SSH host keys and Debian's snakeoil key;
  `kairos-public-firstboot.service` (written + enabled) makes new ones at the first boot; **sshd disabled**;
- **identity:** `/etc/machine-id` emptied, the random seeds (root + ESP), hostname `kairos` (+ `/etc/hosts`);
- **logs:** every file under `/var/log` (journals included), the ESP's `KAIROS-LOGS`, core dumps, `/tmp`, `/var/tmp`;
- **history:** shell / editor histories, root's caches (`.cache`, `.nv`), the kiosk browser profile and caches;
- **testrig and build leftovers:** the remote-UI drop-in, the rig's VNC unit, the build stamps (`/opt/vibeos/HOTFIX`
  names build-host paths and stick serials), and the house rule's `*.bak*` edit backups (gitignored, never public — the
  bundle copied 139 of them into v7.5-2);
- **kAIrOS state:** the cache, `/data` (settings + the first-start flag), generated games, the `/system` doc copy (the
  kernel rewrites it), the civitai key store; growroot is re-armed and `/etc/kairos/profile` is set to `public`;
- **Muse:** `museByStableYogi*`, and any file of its exact size with its sha256.

**What verify checks on top of that:**

- the licence folder: `index.json` and every text it names;
- the FreeDict sources, whenever a GPL FreeDict archive is on the image;
- private keys and WiFi PSKs anywhere in machine state;
- `/home/<user>` paths other than `/home/kairos` outside comments;
- the operator's own strings, one per line, from `iso/secrets/private-patterns.txt` (gitignored, like everything in
  `iso/secrets`) or `--patterns`.

Hits inside `/opt/vibeos/os` (the shipped copy of the public repository) and `/opt/vibeos/fs/system` are WARN, because the
same text is public on GitHub; everywhere else they FAIL.

**On the real v7.5-2 state.** Read-only extracts of the real `kairos-hwtest-4070-v7.5-2-media.img` (debugfs without
`-w`, mtools; the image's size and mtime unchanged) were taken: `/etc`, `/root`, `/home`, `/var/log`, the relevant
`/var/lib`, `/opt/vibeos` without the models, and the ESP's KAIROS-LOGS and loader. On that copy:

1. **Verify: 56 FAIL.** Among them: the WiFi profile + PSK, NM state, five journal dirs, 29 log files, the rig's
   authorized_keys, sshd enabled, the random seeds, the browser profile, the HOTFIX stamps, Muse, the missing profile /
   licences / sources, and the 139 backups.
2. **Prepare: 41 changes.** What was left: the licence overlay and the FreeDict sources.
3. **The build steps:** the overlay was copied in and `fetch-licence-sources.sh --root` was run.
4. **Verify: PASS, 0 findings, 51 warnings.**

The 51 warnings are shipped repository docs (dated reports, `scripts/update-progress.py`) that contain build-host home
paths and one USB stick serial — the same text is in the public GitHub repository. The extract was deleted afterwards.

## 7. What the image build must do

Both images:

1. **Apply the repository as it is now.** New files: `kernel/public.py`, `shell/models.js`,
   `manifests/profile-public.json`, `scripts/public-image-prep.sh`, `scripts/fetch-licence-sources.sh`,
   `tests/test_public_profile.py`, and the 32 new files under
   `iso/config/includes.chroot/usr/share/doc/kairos/model-licences/`. Changed: `kernel/server.py`, `shell/shell.js`,
   `shell/index.html`, `shell/shell.css`, `shell/bench/host.js`, and the docs. Copy the whole `model-licences/` overlay
   folder to `/usr/share/doc/kairos/model-licences/`.
2. **Models** — see below.
3. **The full image only:** `bash scripts/fetch-licence-sources.sh --root ROOT` (network; 36 004 656 B).
4. `bash scripts/public-image-prep.sh ROOT --esp ESP`, with the owner's `iso/secrets/private-patterns.txt` in place.
   It must end with `VERIFY: PASS`.
5. Boot-test as usual, read-only on the flat image: the boot-log verdict, and `/api/status` → `profile: public`,
   `image: Z-Image-Turbo (Q4_0)`.

| | full public image | minimal public image |
|---|---|---|
| base | the v7.5-2-media state (+ this code) | the same system |
| remove | `image/museByStableYogi_v35Q4Extended.gguf` (8 316 720 160 B) — prep does it | every model but the LLM |
| add | `image/z_image_turbo-Q4_0.gguf` 3 683 370 944 B, `image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` 2 497 281 120 B, `image/ae.safetensors` 335 304 388 B (sha256 in tier-8gb `image_classic`; all three are in the laptop store) | — |
| keep | the LLM, Qwen3VL-4B, translation 2B, the 38 dictionaries, the ZIM, video, music, the editor; `qwen_image_vae` (Apache, 253 806 246 B) may stay | `llm/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf` (13 917 722 144 B) |
| FreeDict sources | required (GPL archives on the image) | not needed (no dictionary archive on the image; the user fetches them from FreeDict) |
| first boot fetches | nothing | 22 975 544 966 B (picture set, translation, dictionaries, vision encoder, ZIM) |
| net model change vs v7.5-2-media | −1.80 GB (−8.32 Muse + 6.52 Z-Image) | about 13.9 GB of models |

Muse in a public image: never.

## 8. Verification

- **Tests:** `tests/test_public_profile.py`, 12 tests, run by `tests/run-tests.sh` →
  `tests/results-public-profile.json`. In `kairos-fix-x11:local` with `--network none`:
  - the overlay and the wiring;
  - the profile on the real 4-tier manifests;
  - the private kernel unchanged;
  - the public kernel's resolution and the menubar name;
  - every feature's "needs a download" sentence;
  - the first-boot download with aria2c from a local mock: pause → Range resume → server gone (`waiting`) → back (Range)
    → a wrong sha256 refused twice → retry → complete → Z-Image renders;
  - picture ✨ on a Z-Image picture (the Qwen editor, the source as `-r`);
  - the Licences API and its traversal 404s;
  - the panel in headless Chromium: the first-start dialog with the Muse prompt, the ⬇ chip, About → Licences → a
    LICENSE text, the key field cleared and stored, Remove key;
  - the civitai-key flow (§4);
  - the prep script on planted-secrets trees (§6);
  - the source fetcher against the mock.
- **The full suite and the JS checks:**
  - **Suite run 1** (`kairos-fix-x11:local`, `--network none`, the data mounts explicit; 881 s): 340 passed / 2 failed /
    6 skipped. The new file was 12/0/0. Both failures were fixed before the final run:
    1. PROGRESS.md had no R87 row yet — the suite regenerates it.
    2. The Settings prompt's new sentence pushed the 8 GB dock-prompt budget over (28 573 > 28 278). The sentence is now
       "A Models button: vibe.system.models()." (28 250), and the Settings API sheet is unchanged.
  - **Suite run 2** (the final tree, the same command; 877 s, exit 0): **342 passed / 0 failed / 6 skipped**.
    - results.json 136/0/4, llm-parallel 30/0/0, splash 20/0/1, desktop-web 12/0/1, image-lora 12/0/0,
      forge-quality 13/0/0, imaging-workflow 17/0/0, wifi-bt 27/0/0, refusal-fallback 35/0/0, dock-browser 12/0/0,
      games-windows 8/0/0, picture-edit 8/0/0, **public-profile 12/0/0**.
    - The 6 skips are the environment's: no remote peer under `--network none`, `pod/testrig` and `vibeos-cuda-next` not
      beside the checkout (3), no Pillow, no Playwright image.
    - PROGRESS.md: 84/87 requirements implemented, 0 failing.
  - **JS checks:** `node --check` OK on the six shell files, BRIDGE_SRC parses, `tests/bridge-test.js` 20/20.
- **The real weights, on the CPU engines** (the 4070 was absent, §9): unit `kairos-public-smoke-cpu`.
  - **Setup:** the public profile on the laptop store's real files (the heretic Gemma, the Z-Image set, the Qwen editor
    set; Muse absent), the CPU engine set (llama.cpp 65840ed, sd.cpp a1ded76, sd-cli), `--network none`, 512².
  - **Result: PASS.**
    - The heretic answered "Mercury" (HTTP 200, 22.5 s).
    - An SFW picture by Z-Image-Turbo: 321 s, with the resident Gemma's krea-neutral expansion.
    - An 18+ picture of a fictional adult: 327 s, krea-18plus, the rendered prompt "a woman in her thirties". It was saved
      only to the owner's 18+ proof folder.
    - Picture ✨ "make the sky purple" on the Z-Image picture by Qwen-Image-2.1 Turbo edit: 322 s; the sky turned purple
      and the content was kept.
  - This proves the code path with the real models. It is not the 4070 check.
- **The GPU check:** see §9.

## 9. Open

- **The GPU check on the RTX 4070 did not run** (the same path ran on the CPU engines, §8). The card is absent from the PCI bus since this boot ("NVRM: No NVIDIA
  GPU found", no `01:00.0`). Bringing it back needs root (a PCI rescan, the BIOS GPU mode, or a reboot), which this work
  must not do.
  - The unit `kairos-public-smoke` ran, took the GPU lock, and stopped at its card check.
  - The harness (`smoke/run.sh` + `layout.py` + `driver.py` in the task's results folder) runs the public profile on the
    real weights, with Muse absent and `--network none`. It covers: the heretic's answer, an SFW picture, an 18+ picture of
    fictional adults (written only to the owner's 18+ proof folder), and picture ✨ on the Z-Image picture.
  - `MODE=gpu` is the 4070 run; `MODE=cpu` is a labelled CPU-engine run.
- **Muse with a real civitai key is untested** (only the mocks). civitai's `Authorization: Bearer` and `?token=` forms are
  from civitai's API docs, not re-checked against the live service.
- **The shipped repository docs** carry build-host home paths and a stick serial (51 warnings). Scrubbing them in the
  repository is the owner's call.
- **The Qwen-Image-2.1 editor's licence** limits use to "research or evaluation" (an owner decision from the licence
  research). The Apache-2.0 alternative is Qwen-Image-Edit-2511.
- **The larger tiers:** their LLM files and the Q8 vision encoder carry no sha256 pin (2 entries).
- **The Settings app is LLM-written.** Its new button depends on the generation. The panel is reachable without it
  (About, the chip, the first-start dialog).
- **The repository itself still has no licence** (README §Licence).

# kAIrOS system image v7.3 for the USB stick (2026-10-07)

Built by session kairos-b3 (Claude Fable 5.1) from `~/kairos/build-v7.3/BRIEF.md`. Owner's words: "build it in the system".
Every step is appended here as it finishes (times are local, CEST). Recipe: SESSION-HANDOFF.md R-AJ/R-AL/R-AM
(session kairos-e3), i.e. overlay on the host → `sync-guest.sh` with `KAIROS_SYNC_ENGINES=0` → bake → reboot →
bootlog PASS → smoke → ordered clean-up → flatten → compare → sha256 → `vm.sh test` at the stick's size.

## Contents

**v7.3 = v7.2 + today's `repo/vibeOS-local` + the bake-flagged add-ons.**

- Base: `kairos-hwtest-4070-v7.2.img` (never flashed; the stick holds v7.1), sha256
  `7a7f2d260b6d42c7180682b001264e83817ac1d425d07bfce53669f5f6c94401`.
- Repo since v7.2: R72 parallel LLM requests (GPU-validated, `parallel: 2` on llm/llm_fast) + its fix round;
  kairos-a9's llama-server caps (`--cache-ram 4096/2048 --ctx-checkpoints 8`, `expander_parallel: 2` on
  image/image_uncensored); R71 P3 (`/api/dict`, dictionaries + Wiktionary add-ons, `--cache-ram 0` on translation,
  DOM batches always fast); the bake flag (`docs/IMAGE-BAKE-ADDONS-2026-10-07.md`). Integrator's verdict before the
  freeze: suite 129/129 + `test_llm_parallel` 30/30.
- Baked into `/opt/vibeos/models`: exactly `scripts/check_tier_present.py manifests/tier-8gb.json <models> --list`
  = the 4 non-addon files already in v7.2 + the 39 bake-flagged add-on files (1 430 627 539 B):
  - `translation/Index-Translate-2B.Q4_K_M.gguf` (1 312 164 352 B, sha256 `044b313d…746a`);
  - `dict/beolingus-de-en.txt.gz` + 37 `dict/freedict-*.dictd.tar.xz` (118 463 187 B).
  - Host source: `/home/layer/kairos/models/models/translation-candidates/{Index-Translate-2B.Q4_K_M.gguf,dict/}`.
    All 39 re-hashed on the host against the manifest's sha256 + size_bytes before the copy: **0 mismatches**.
  - Download-only as before: the quality 9B (`translation/Index-Translate-9B.Q4_K_M.gguf`), the 7 Wiktionary ZIMs.
- Engines: unchanged = the stamped `vibeos-cuda-next` set of v7.2 (`KAIROS_SYNC_ENGINES=0`); the 2026-10-06 set has
  no GPU proof and stays out.
- Translation setting ships **off** (the default).

## Log

### Pre-flight (12:2x)
- `df -h /` → 372 G free (needs ~85 G peak). Images present: v6, v7.1, v7.2 (+ `.sha256`); nothing deleted.
- GPU on the host `nvidia` driver, `/dev/vfio` has no group → `VM_GPU=off` only, as required. `kairos-trbench.service`
  running (container `kairos-trbench-llm`), left alone.
- `pod/testrig/vm.sh` re-read: default `KAIROS_IMG` is v6 → every call passes `KAIROS_IMG`/`KAIROS_OVERLAY` explicitly.
- Caches the smoke will create and the clean-up must drop: `/opt/vibeos/cache/dict/*.idx|.meta.json` (dictionary
  indexes, kernel/dictionary.py) and `/opt/vibeos/cache/translate/<lang>.json` (translation cache, server.py
  `TRANSLATE_CACHE_DIR`). `dist-clean.sh` wipes `/opt/vibeos/cache/*` → both go; dictionary indexes are rebuilt on
  first use (~20 s per dictionary). Decision: **dropped** (nothing in the image depends on them; keeps the image clean).

### a) Overlay + build VM (12:39–12:40)
- 12:39 `qemu-img create -f qcow2 -b ../kairos-hwtest-4070-v7.2.img -F raw build-v7.3/v7.3-build.qcow2 57881936384`
  (run from `build-v7.3/`; the brief's `-b kairos-hwtest-4070-v7.2.img` fails because qemu resolves a relative backing
  path against the overlay's own directory — `../` is what resolves on the host AND in the container, where the same
  tree is mounted at `/images`). Virtual size 57 881 936 384 B = the base's size, no FS shrink needed later.
- 12:40 `docker compose run -d -e KAIROS_IMG=/images/kairos-hwtest-4070-v7.2.img -e KAIROS_OVERLAY=/images/build-v7.3/v7.3-build.qcow2 -e VM_MEM=12G -e VM_GPU=off --name kairos-v73-build dev /images/pod/testrig/vm.sh run`
  (container `d8c2300f3c2b`). Guest commands go through `docker exec kairos-v73-build ssh -i /images/pod/testrig/id_kairos -p 2222 … root@127.0.0.1`.

### b) Repo sync (12:40:37–12:40:59) — exit 0
`docker exec kairos-v73-build env KAIROS_SYNC_ENGINES=0 /images/pod/testrig/sync-guest.sh` (full log:
build session scratchpad `sync.log`). 1/8 kernel/shell/manifests/scripts/docs synced; 1b skipped (engines kept);
2/8 includes.chroot overlay applied (8 `kairos-*` sbin scripts incl. `kairos-prewarm`, `kairos-growroot.sh`);
3/8 run/log dirs + getty drop-ins (`autologin.conf kairos-xretry.conf`); 4/8 units enabled, `layout=other`;
5/8 loader: only `kairos.conf` + `kairos-serial.conf`, `default kairos.conf timeout 0 editor no`, ESP `nofail`;
6/8 fonts present (272 noto faces), python3-xlib present, openbox rc.xml 664 decor=1; 7/8 mpv + 1.4 M default clip
present; 8/8 expose-ui drop-in written (removed again in step c), `/api/status` HTTP 200,
**engines: ok — llama.cpp 4da6337 (2026-09-27), stable-diffusion.cpp 3f8527a (2026-09-27), acestep.cpp daf7644 (2026-09-11)**
(= the stamped v7.2 set).

**SYNC DONE 2026-10-07 12:40:59 CEST** — the repo freeze can be released; nothing of `repo/vibeOS-local` is read by the build after this line.

### b2) Bake the add-ons (12:41:20–12:42:17)
- 12:41 tar stream (no compression) from the container's `/images/models/models/translation-candidates/` into the guest's
  `/opt/vibeos/models/`, `--transform` maps `Index-Translate-2B.Q4_K_M.gguf` → `translation/…`, `dict/` → `dict/`
  (`.sha256ok` markers excluded). 25 s.
- Verified IN the guest against the manifest (`sha256sum -c` of the 39 manifest sha256s + `stat` size vs `size_bytes`):
  **39/39 OK, 0 size mismatches**. `check_tier_present.py tier-8gb.json /opt/vibeos/models` → `total 25.0GB (without
  add-ons, +39 baked add-on files) -> ALL PRESENT`, exit 0; `--list` 43 files, `--bytes` **24 995 886 393** (= the
  manifest sum of docs/IMAGE-BAKE-ADDONS-2026-10-07.md); `find` vs `--list`: no extra, no missing file → the models dir
  is exactly the baked set.
- Ownership normalised to the image's convention (`root:root`, dirs 755, files 644 — tar had kept the host's uid 1000).
- `df -h /` after the bake: **1.4 G available** (53 G size, 49 G used, 98 %) — the brief expected ~2.7 − 1.43; growroot adds
  ~4.7 GiB on the stick.

### c) expose-ui removed + reboot (12:42)
- `rm /etc/systemd/system/kairos.service.d/expose-ui.conf` → drop-in dir empty, `daemon-reload`; the API is loopback-only
  again after the reboot (R66).
- 12:42 `sync -f /boot/efi; systemctl reboot` (attempt #1 was this build boot; the verdict that counts is attempt #2).

### c2) Boot verdict attempt #2 + status (12:43)
- SSH back at 12:43:09 (38 s after the reboot command). `/boot/efi/KAIROS-LOGS/latest.json`: reason `boot`, **attempt #2,
  VERDICT: PASS — all checks passed**: boot_history, boot_target (graphical.target, system running), failed_units 0,
  boot_errors 0, crash none, hung_units none, rootfs_grow (53G total, 1.4G free), gpu_driver virtio, firmware ok,
  modules ok, **xorg up on :0 (attempt 1)**, **kernel_api answering on :1337**, **kiosk 1 Chromium alive**, network
  10.0.2.15, esp mounted. attempts.txt: #1 boot PASS 10:40:37 UTC, #1 shutdown PASS 10:42:35, #2 boot PASS.
- R66 bind: `ss` → `127.0.0.1:1337` only (sshd on `*:22`), `VIBEOS_HOST` unset, `kairos.service.d/` empty.
- `/api/status`: `engines: ok` llama.cpp 4da6337 / stable-diffusion.cpp 3f8527a / acestep.cpp daf7644, tier 8gb,
  llm "Gemma 4 26B-A4B-it (UD-IQ4_XS …)", `translation.enabled false, language en, quality fast, effective fast`.
- `/api/translate/status`: **`present: {"fast": true, "quality": false}`**, `available: true`, `fallback: null`,
  `running: {fast: false, quality: false}` (nothing runs while the setting is off).
- `/api/dict/status`: **`present: true`, files 38, present_files 38**, indexed 0 (indexes are built on first use), 42 sources
  (the reverse-served directions included), index_dir `/opt/vibeos/cache/dict`.
- Settings: `translation off, language en, translation_quality fast, llm_model default` (the defaults).
- kairos-prewarm: ran, "root is not on USB: no prewarm needed" (12 s) — it only acts on the stick, as designed.
- Known, NOT a failure: `/api/models` → the `translation` section is `present: false` while the optional 9B is absent
  (section_present wants every file; docs/IMAGE-BAKE-ADDONS-2026-10-07.md). **Follow-up** for ModelManager (kairos-a9):
  a per-file "present" for a partly baked add-on. → **Done 2026-10-07** in the kernel (`translation_presence()`:
  `extras[translation]` = `present` true, `present_modes {fast, quality}`, `missing_gb`; docs/TRANSLATION-LAYER-2026-10-06.md
  "present_modes fix") — ships with v7.4, this image still shows `present: false`.

### c3) In-guest smoke with translation ON (12:44:21–12:45:30, all from loopback)
| step | result |
|---|---|
| `POST /api/settings {"translation":"on","language":"de"}` | 200, settings stored |
| `GET /api/dict?word=Haus&from=de&to=en` | **20 entries** (exact 8, folded 6, prefix 40), errors `[]`; first: BEOLINGUS `Haus → home`, `→ house`, `→ establishment, institution`; sources BEOLINGUS de-en (553 892 entries) + FreeDict deu-eng (465 726); **44.0 s** = two first-use index builds (`cache/dict/beolingus-de-en.idx` 47 MB + `freedict-deu-eng.idx` 34 MB) |
| `POST /api/translate {"text":"The weather is nice today and the dictionary works offline.","to":"de","from":"en"}` | **"Das Wetter ist heute gut und das Wörterbuch funktioniert offline."**, model `fast`, calls 1, cached 0, **19 105 ms** incl. the fast server's cold start |
| fast server | `llama-server -m …/translation/Index-Translate-2B.Q4_K_M.gguf --host 127.0.0.1 --port 8085 -ngl 0 -dev none -c 8192 -np 4 …` on `127.0.0.1:8085` (CPU only, loopback) |
| `/api/translate/status` after | enabled, language de, active, effective fast, running `{fast: true}`, calls 1 |
| `/api/dict/status` after | indexed 2, lookups 1, builds 2, build_s 44.0 |
| `POST /api/settings {"translation":"off","language":"en"}` | settings now `translation off, language en, translation_quality fast`; 3 s later **no :8085 listener**, status `running {fast: false}` |
| kernel journal this boot | 0 error/traceback lines; `systemctl --failed` 0 |

Ships with translation **off** (default). The two dictionary indexes and `cache/translate/` are dropped by the clean-up
(dist-clean wipes `/opt/vibeos/cache/*`); they rebuild on first use (~20 s per dictionary on NVMe, longer on the stick).

### d) Clean-up in the guest, in the recipe's order (12:46:36–12:46:46) → poweroff
0. Checked first: `/opt/vibeos/fs/data/settings.json` = `{"wallpaper": "/files/images/81443c070ae8.png"}` only (the kernel
   stores non-default keys only → the on/off toggle left no trace); `/root/.ssh/authorized_keys` 1 line (rig key, stays).
1. `pod/testrig/dist-clean.sh` (piped in): kairos + bootanim stopped, `/opt/vibeos/cache/*` wiped (→ the 2 dict indexes
   and `cache/translate/` are gone), apt lists/archives, journal vacuumed, `/var/log` files truncated, first-boot markers
   + ssh host keys removed, machine-id emptied, histories/tmp, `active-tier` = 8gb.
2. `/var/lib/systemd/random-seed` removed (`credential.secret` did not exist).
3. NetworkManager: `secret_key`, the enp0s4 `.lease`, `timestamps`, `seen-bssids` removed; left `NetworkManager-intern.conf`,
   `NetworkManager.state`; profile `OpenAP.nmconnection` kept (owner's test stick, as in v7–v7.2).
4. `/root/.ssh/known_hosts` (none existed; `authorized_keys` untouched).
5. `/var/lib/systemd/pstore/` was already empty.
6. `/home/kairos/.local/share/xorg/Xorg.0.log{,.old}` removed.
7. `systemctl stop kairos-bootlog-stall.timer kairos-bootlog.service` → shutdown capture written. It reads
   `#2 shutdown … verdict FAIL (rootfs_grow: kairos-growroot enabled but never completed)` — an artefact of the order:
   step 1 had just removed `/var/lib/kairos-resized`, so the capture sees "never completed". The live boot verdict of
   attempt #2 was PASS; the stick-size `vm.sh test` below re-proves growroot on a true first boot.
8. `KAIROS-LOGS` reset to `README.txt` only (4 boot captures, early.txt, attempts.txt, latest.* removed).
9. `fstrim -v`: `/` 3.6 GiB trimmed, `/boot/efi` 409 MiB trimmed.
10. Final state: `df` / 53G, 49G used, **1.4G available**; `/boot/efi` 102M used of 511M; machine-id empty, 0 ssh host
    keys, tier 8gb; `sync; poweroff` — QEMU exited, container `kairos-v73-build` Exited (0) 12:46:49, removed.

### e) Flatten, compare, sha256 (12:47:22–12:52:44)
1. `qemu-img convert -p -O raw build-v7.3/v7.3-build.qcow2 kairos-hwtest-4070-v7.3.img` → exit 0 (87 s);
   `kairos-hwtest-4070-v7.3.img` = **57 881 936 384 B** (same size as v6–v7.2; fits the 62 914 560 000 B stick), 49 G allocated.
2. `qemu-img compare -f qcow2 -F raw build-v7.3/v7.3-build.qcow2 kairos-hwtest-4070-v7.3.img` → **Images are identical.** (exit 0)
3. `sha256sum kairos-hwtest-4070-v7.3.img > kairos-hwtest-4070-v7.3.img.sha256`:
   **`8dd38227135b5ed0c1a12ce7fce63bb3c4694df7998a2b2a12910c82324a6cc7`**
4. Host `df -h /` after: 322 G free (was 372 G). Images on disk: v6, v7.1, v7.2, v7.3 — nothing deleted.
5. 12:53 `vm.sh test` on the RAW image at the stick's exact size:
   `docker compose run --rm -e KAIROS_IMG=/images/kairos-hwtest-4070-v7.3.img -e KAIROS_OVERLAY=/images/build-v7.3/v7.3-test.qcow2 -e VM_DISK=62914560000 -e VM_GPU=off -e VM_MEM=12G -e VM_TIMEOUT=600 --name kairos-v73-test dev /images/pod/testrig/vm.sh test`
   (vm.sh creates the throwaway overlay; growroot runs as on the stick) — result below.

### e5) `vm.sh test` on the raw image at the stick's size (12:53:00–12:53:42) → **16/16, ALL CHECKS PASSED**
boot 6.12.90+deb13.1-amd64 · root ext4 · **auto-resize 58G** (growroot on a true first boot: `nvme0n1p2` 58.1G, 5.8G free,
`/var/lib/kairos-resized` written) · ssh host keys regenerated (3) · machine-id regenerated · kairos.service active ·
HTTP 200 · trust boundary (test guest exposed by vm.sh itself, token ok) · **engines match manifests: ok** (llama.cpp
4da6337, sd.cpp 3f8527a, acestep.cpp daf7644) · nvidia-load hybrid-safe, idle · 0 ordering cycles · kairos.conf booted ·
0 failed units · getty@tty1 active · Xorg on :0 (attempt 1) · 1 kiosk Chromium · **bootlog verdict PASS (attempt #1)**.
Read-only while it ran: `/api/translate/status` `present {fast: true, quality: false}`, `/api/dict/status` `present true
38/38`, settings `translation off / en / fast`, models dir = llm image translation dict (38 files), cache empty.
Overlays `build-v7.3/v7.3-build.qcow2` (1.6 G) + `v7.3-test.qcow2` deleted 12:53; no container, no qemu left; `/dev/vfio`
untouched; `kairos-trbench.service` active throughout. `~/kairos/testrig-console.log` + `testrig-latest.json` are vm.sh's
standard (root-owned) outputs of this run, left as the rig keeps them.

## Result

| | |
|---|---|
| image | `/home/layer/kairos/kairos-hwtest-4070-v7.3.img` — raw GPT, **57 881 936 384 B** (49 G allocated) |
| sha256 | **`8dd38227135b5ed0c1a12ce7fce63bb3c4694df7998a2b2a12910c82324a6cc7`** (`kairos-hwtest-4070-v7.3.img.sha256`) |
| base | v7.2 `7a7f2d26…c94401` (unchanged on disk) |
| repo | `repo/vibeOS-local` as of 2026-10-07 12:40:37 (R72 + fix round + llama-server caps, R71 P3, bake flag; suite 129/129 + 30/30 per the integrator) |
| engines | the stamped v7.2 CUDA set (llama.cpp 4da6337 / sd.cpp 3f8527a / acestep.cpp daf7644) |
| models | 43 files = Gemma 4 26B-A4B IQ4_XS, Krea 2 Q4_K_M, Qwen3-VL-4B Q4_K_M, qwen_image_vae + **Index-Translate-2B Q4_K_M + 38 dictionaries** (24 995 886 393 B), all sha256-verified in the guest |
| build guest | boot PASS attempt #2, smoke: dict Haus→house, translate EN→DE on CPU in 19 s, setting back off |
| raw test | `vm.sh test` at 62 914 560 000 B: 16/16, root 58G, bootlog PASS |
| df inside | build guest 1.4 G free at 53 G; on the stick after growroot 5.8 G free at 58 G |

## Flash (owner; the desktop automounts the stick's partitions — unmount first)

```
sudo umount /dev/disk/by-id/usb-Intenso_Premium_Line_FC131399483FA-0:0-part* 2>/dev/null; sudo dd if=/home/layer/kairos/kairos-hwtest-4070-v7.3.img of=/dev/disk/by-id/usb-Intenso_Premium_Line_FC131399483FA-0:0 bs=4M oflag=direct conv=fsync,nocreat status=progress
```

## Not done / follow-ups
- Nothing on the 4070 (`VM_GPU=off` by brief): R72 `parallel: 2` + the `--cache-ram` caps ship on kairos-a9's host-side GPU
  numbers, not on a VM run of this image; the hybrid-GPU desktop, boot time, prewarm and the LLM load window are only
  measurable on the stick (first stick run of v7.2's fixes is this image's).
- `/api/models` keeps the `translation` section `present: false` until the optional 9B is downloaded (kernel
  `section_present` wants every file) although `/api/translate/status` says `present.fast: true` and translation works.
  Per-file presence for a partly baked add-on = ModelManager territory (kairos-a9); docs/IMAGE-BAKE-ADDONS-2026-10-07.md.
  Fixed in the kernel 2026-10-07 (`present_modes`, docs/TRANSLATION-LAYER-2026-10-06.md) → v7.4; not in this image.
- Dictionary indexes are not pre-built (dropped with the cache): the first lookup per dictionary pays ~20 s on NVMe,
  more on the stick. Pre-building them at image time would be a build-recipe addition (`cache/dict` survives dist-clean
  only if it moves out of `cache/`).
- The shutdown capture's `FAIL rootfs_grow` after dist-clean (step d.7) is an artefact of the clean-up order; a future
  recipe could stop the bootlog units before dist-clean (then step 8 still resets the logs).

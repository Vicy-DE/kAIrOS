# Faster first picture on the card (2026-10-24)

> **Status: paused 2026-10-24 — the GPU went to the music batch; measurements incomplete; nothing shipped.** This is a draft: §1 (the breakdown of the card run's journal) and §2 (the method and the decision rule) are written; §3–§7 (results, decision, what ships) are still empty, and no code in the tree comes from it.

The owner ticked "Faster first picture" after the v7.5-4 card run (`docs/FIXES-2026-10-24.md` covers that run's other
findings). This report breaks the card's picture times down from its journal, reproduces the card's I/O on the
build laptop, measures the candidates against a written decision rule, and says what ships. Numbers without a
source are from this host (RTX 4070 Laptop 8 GB, 30 GiB RAM, NVMe) under the throttle described in §2.

## 1. The breakdown: where the card's 386 s went

Source: the card run's journal (`boot-minus0.txt`, boot of 08:02:49) and the sd-server log of its last session.
Every picture goes through an sd-server **session** (R79): a fresh `sd-server` process that reads the picture
model (Muse DiT 8.3 GB + Qwen3-VL-4B encoder 2.5 GB + VAE 0.25 GB = **11.1 GB**) from the card, paints the batch,
then stays open for its 8 s idle window.

| session | start | pictures | first picture (POST → next POST / done) | later pictures | closed by |
|---|---|---|---|---|---|
| 1 | 08:10:30 | 4 (web page, 512²) | **161.0 s** | 9.8 / 10.4 / 10.8 s | a GPU waiter = picture job `3ea09d` (same model) |
| 2 | 08:13:45 | 1 (user picture) | **181.5 s** | – | a GPU waiter = an LLM request (Gemma back in 8 s) |
| 3 | 08:20:40 | 1 (settings icon) | **154.6 s** | – | a GPU waiter = a picture batch (same model) |
| 4 | 08:23:17 | 4 (web page) | **160.8 s** | 9.6 / 9.6 / 10.1 s | a GPU waiter = a picture (the settings icon again) |
| 5 | 08:26:30 | 1 (settings icon) | **154.9 s** | – | a GPU waiter = an LLM request (the next picture's expansion) |
| 6 | 08:29:20 | 1 | cut by the shutdown at 62 s | – | shutdown |

**Job `3ea09d152591`, "image done after 386.1 s"** (created 08:10:21.6):

| part | time | share |
|---|---|---|
| expansion by the resident Gemma (already loaded) | ≤ 9 s | 2 % |
| GPU-queue wait behind session 1 (its 161 s first picture + 3 × 10 s) | 194 s | 50 % |
| session 1's idle window, closed at once because this job was "a GPU waiter" | 2 s | 1 % |
| sd-server start (it listens before it reads anything) | 1 s | – |
| **the model read: 11.1 GB from the card into sd-server's pinned copy** | **~150 s** | **39 %** |
| render at the user size (8 steps + VAE) | ~30 s | 8 % |

The Gemma restart that followed (8 s) is charged to the next answer, not to this job.

What dominates, with numbers:

1. **The per-session model read**: ~145–150 s of every 155–181 s first picture. The card reads the encoder at
   73 MB/s and the DiT's lazy 13-tensor chunks at 110–145 MB/s (sd.cpp's segmented loader), and about a quarter
   of the load time is not reading at all: the engine logs `taking 67 s (read: 53 s)` for the first 4.4 GB — the
   pinned host buffers (`cudaHostAlloc`) each chunk is copied into. Six sessions in 20 minutes spent ~15 minutes
   reading the same 11.1 GB, because R79 drops those files from the page cache on purpose (sd keeps its own copy,
   and 13.9 + 2 × 11.1 + 6 GB do not fit in 32.9 GB).
2. **The queue wait** is the previous session's model read plus its renders — the same cost seen from the next
   user.
3. **A structural cause, 4 of the 5 session closes**: a picture of the *same model* that arrived while the session
   was still painting became an exclusive GPU waiter, so the idle window closed "at once" for it, the server was
   stopped, the files dropped, and the next session re-read the 11.1 GB. `sd_batch` only tried to join a session
   that was already *in its idle window* (`_SdSession` was created after the batch), never one that was loading
   or painting. The one close that was a genuine LLM request (session 5) was the expansion of the *next* picture:
   a user's consecutive pictures always alternate LLM ↔ sd because each needs the LLM for its prompt rewrite, so
   the 8 s idle window never helps them; it helps icons, wallpapers and page batches (kernel-written prompts).
4. **Not the problem**: render steps + VAE (~10 s at 512², ~30 s at 1024²), the LLM restart (7–8 s, Gemma stays
   100 % resident — R79's drop works as designed), the expander on the resident LLM (≤ 9 s), sd-server's start
   (1 s).

## 2. Method: the card's I/O on the laptop

* **The real kernel** of this tree (a copy without `.git`) with today's `tier-8gb.json`, the shipped engine set
  (sd.cpp 3f8527a, llama.cpp 4da6337 — the card's), the heretic Gemma and Muse as symlinks into the model store,
  in the `kairos-fix:local` container with `--gpus all`, a private `VIBEOS_DIR` (every picture stays there), on a
  Docker `--internal` network (host ↔ container only; a probe to 1.1.1.1 is unreachable).
* **The throttle**: Docker `--device-read-bps <nvme>:80mb` (cgroup v2 `io.max`; the io controller is not
  delegated to the user slice, so `systemd-run --user IOReadBandwidthMax=` cannot apply, and dm-delay/loop
  need root). Proof: the 2.5 GB encoder evicted from the page cache (0.0 % resident), read with `dd` inside the
  container: **88.3 MB/s at `84mb`** (Docker's `mb` is MiB) — the runs use `80mb` = **83.9 MB/s**, the card's
  ~84 MB/s; every run re-measures it (`throttle_proof` in its `session.json`). Reads that hit the page cache are
  not throttled — exactly as on the card.
* **Memory**: `--memory 24g --memory-reservation 20g` (`memory.low`) + 8 GiB of swap (the card's swapfile). The
  card has ~27.5 GiB for the kernel's processes (30.6 GiB minus its desktop); this host's own desktop left
  18.5 GiB free on the day, so 24 GiB is the most it could give without swapping the owner's applications.
  Cache-based candidates are therefore measured a little **worse** here than the card would show them, and the
  baseline's Gemma can lose ~0.6 GB during an sd session (13.9 GB + sd's 12.5 GB pinned copy against 25.8 GB);
  the journal's `page cache after the image engine: LLM … N % resident` lines make that visible per run.
* **Cold cache**: every model file is evicted (`posix_fadvise(DONTNEED)` as the user) and verified < 5 % resident
  before a run; the LLM is then read once *inside the container* through the throttle, as `kairos-prewarm` does
  at boot on the card (195 s there).
* **The session, per variant** (same prompts, 1024² — the section's default size):
  `P1` first picture after boot (SFW, expanded) → `Q1` an LLM turn → `P2` first picture after the LLM turn (adult,
  a fictional woman in her thirties, expanded) → `P3` a kernel-style picture (`expand: off`, like icons /
  wallpapers / page batches) posted 20 s into P2's session → `P4` a second user picture (adult, expanded) 3 s
  after P3 → `Q2` the next LLM answer. Recorded: every latency, the kernel journal, the sd-server log of every
  session (load vs render), page-cache residency of Gemma and the image files after each step (`mincore`),
  container memory / swap / anon / file, host MemAvailable and swap, VRAM, CPU clock and the engines' RSS every 5 s.
* **Power** is logged before each run (`ACAD`, the USB-C source, the CPU clock); the throttle here is I/O.
* **What the emulation cannot show**: the throttle caps bandwidth; the card's random reads are far slower than
  its sequential 84 MB/s, which matters for an `mmap` loader that faults pages in tensor order (sd.cpp's chunks
  are MB-sized and contiguous, so readahead should keep it close to sequential — the next card run is the proof).

### The decision rule (written before measuring)

A candidate ships only if it cuts the **median first-picture time** (the median of P1, P2, P3) by **≥ 30 %**
against the baseline on this setup, without pushing the container into swap (`memory.swap.current` stays 0, the
host's swap use does not grow), without slowing the next LLM answer (Q2) by more than 10 %, and with the R79/R72
scheduling tests green in the full offline suite. Winners that add up are combined and re-measured.

## 3. Candidates and results

_(filled in from `runs/<variant>/session.json` below)_

## 4. Decision

_(pending)_

## 5. What ships

_(pending)_

## 6. What the next card run should show

_(pending)_

## 7. Open

_(pending)_

# Architecture — the bulletproof safety stack, explained

This doc explains *why* `scripts/scraper.py` is built the way it is. Read this when:
- You need to debug a stuck or misbehaving scrape
- You need to modify the engine (a rare and risky thing to do)
- Something in the scraper looks weird and you want to know if it's intentional
- The user asks "what's actually happening in here"

The TL;DR for the lazy reader: **don't touch the engine**. The CONFIG block at the top of `scraper.py` is the only thing meant to change per-vertical. Everything else exists for a reason — usually a painful one we learned the hard way.

---

## The 8 safety features

These aren't theoretical. Each one solved a real problem that cost real hours on a real scrape.

### 1. PID-based single-instance lock

**File**: `{project}.lock`
**Why**: Two scraper processes running on the same `{project}_results.csv` will corrupt it — interleaved writes, duplicated rows, broken dedup. The lock file contains the running process's PID. On startup the scraper checks if that PID is alive (`os.kill(pid, 0)`); if alive, refuses to start. If the PID is dead (stale lock from a crash or hard-killed process), it overwrites and continues.

**What you'll see**: `ERROR: another scraper is already running as PID 12345` — and a hint to delete the lock if you're sure it's dead. **Never** delete the lock while a real scraper is alive.

### 2. Atomic JSON checkpoint

**File**: `{project}_progress.json`
**Updated**: every `CHECKPOINT_EVERY` API calls (default 50)
**Why**: `--resume` needs a reliable, never-half-written record of which tasks are done, how many API calls have been made, and the dedup ID count. We write to a `.tmp` file and atomically `os.rename` it over the real file — even if the process dies mid-write, the existing JSON is intact.

**Schema**:
```json
{
  "started_at": "2026-04-23T22:26:46",
  "last_update": "2026-04-24T15:47:38",
  "api_calls_made": 18620,
  "rows_written": 40394,
  "completed_tasks": ["zip|grid|query|offset", ...],
  "seen_ids_count": 40394,
  "current_position": "27610 (Raleigh, NC) ..."
}
```

`completed_tasks` is what `--resume` reads to skip already-done work.

### 3. Append-only `seen_ids.txt` for dedup

**File**: `{project}_seen_ids.txt`
**Format**: one Google `business_id` per line
**Why**: On startup with `--resume`, we load this into a Python `set` so any business we've already written to the CSV is skipped. **Why a separate file instead of reading the CSV?** Reading a multi-million-row CSV at every resume is slow; reading a flat text file is instant. Also append-only writes are safer than CSV writes (no quote/comma escaping needed).

### 4. Per-row CSV write + flush()

**File**: `{project}_results.csv`
**Why**: If the scraper crashes — system reboot, OOM kill, network blip — we want every result already written to be on disk. The naive approach (write rows to a list in memory, dump at the end) loses everything on crash. So: every single row is `writer.writerow(...)` + `f.flush()` before the next worker iteration.

**Performance cost**: trivial. Disk flush is ~1 ms. The bottleneck is the API (~2s/call), not file I/O.

**Trade-off**: if the scraper holds the CSV open while you `tail -f` it, that's fine. But don't move/rename the open file mid-run — it'll keep the handle to the original inode.

### 5. SIGINT/SIGTERM signal handlers

**Why**: `Ctrl+C` (SIGINT) or `kill <pid>` (SIGTERM) needs to flush state cleanly before exiting. The handler:
1. Acquires the state lock
2. Calls `state.save_progress()` → atomic JSON write
3. Calls `state.write_status()` → human-readable status file
4. Closes file handles
5. Releases the PID lock
6. `os._exit(0)`

Result: the scraper can be stopped at any moment and `--resume` will pick up cleanly.

**Note**: `kill -9` (SIGKILL) **cannot** be caught — the process dies instantly without running the handler. Use plain `kill` (SIGTERM) to stop gracefully. We never need `-9` because the SIGTERM handler is fast.

### 6. 429 retry with exponential backoff

**Ladder**: `[5, 15, 30, 60]` seconds, plus an initial immediate retry.
**Why**: RapidAPI throttles on burst load. With 5 parallel workers we occasionally exceed their per-second cap — they respond `429 Too Many Requests`. The worker that got 429d sleeps and retries. After 4 failed attempts (going through the full ladder), it gives up on that single call and returns an empty result — the rest of the scrape continues, and one missed call is recoverable on the next run with `--resume`.

**What you'll see**: nothing in the log (the v2 version of this is intentionally quiet to avoid log spam). If you suspect 429 issues, check throughput: if cumulative calls/min is dropping but workers are alive, you're probably hitting backoffs.

### 7. Human-readable status file

**File**: `{project}_run_status.txt`
**Updated**: every `CHECKPOINT_EVERY` API calls
**Why**: A one-screen text file you can `cat` to know what's happening without parsing the JSON progress file or grepping the log.

```
my_scrape scraper — RUNNING (PID 89084, 5 workers)
Started:    2026-04-23T22:26:46
Elapsed:    13:45:04
Progress:   14460 / ~21720 API calls (66.6%)
Results:    33068 unique rows in CSV
Rate:       40.1 calls/min  (94.2 rows/min)
Current:    27610 (Raleigh, NC) g=3/4 'psychiatric clinic' off=20
ETA:        1:06:33 remaining (cumulative-rate estimate; recent rate may differ)
Updated:    2026-04-24T12:11:50
```

**About the ETA**: it uses cumulative rate (calls / elapsed) which is dragged down by any past slow periods (sleep, network drops, 429 storms). The real *current* throughput is usually much higher. To get a real-time rate, sample the CSV size over 30–60 seconds and compute manually. See "Cumulative-vs-recent rate problem" below.

### 8. The full stdout log

**File**: typically `run.log` (when invoked with `python3 -u scraper.py 2>&1 | tee run.log` or via `nohup ... > run.log 2>&1`)
**Why**: every API call writes one line to stdout — `[CALL_NUM] zip (city, state) g=grid_pt 'query' off=offset → raw:N new:N (uniq:TOTAL)`. With `-u` for unbuffered output, you can `tail -f run.log` for live progress. Patterns like 429 storms, slow zips, dedup-heavy areas all become visible.

---

## Parallel worker design

### The shared-state problem

When 5 workers each making HTTP calls hit the API simultaneously, they all want to:
- Read `seen_ids` to dedup
- Add new IDs to `seen_ids`
- Write rows to the CSV
- Increment `api_calls_made` and `rows_written`
- Update `completed_tasks`

If two workers try to do these things at the exact same moment, you get races: duplicated rows, missing dedup, off-by-one counters.

### The solution: one lock, narrow scope

A **single `threading.Lock`** owned by the `State` object. The pattern:

```python
# OUTSIDE the lock (slow, parallel):
places = search_maps(query, lat, lng, offset=0)   # 2-second HTTP call
time.sleep(REQ_DELAY)

# INSIDE the lock (fast, serial):
with state.lock:
    state.api_calls_made += 1
    n_new = _ingest(places, ...)                  # dedup check + CSV write
    state.completed_tasks.add(key)
    state.current_position = "..."
    print(f"[{state.api_calls_made}] ...")
    _maybe_checkpoint(state, ...)
```

The HTTP call is what takes ~2 seconds. That's where parallelism buys throughput. The post-processing inside the lock is microseconds. As long as the slow part is outside the lock, 5 workers genuinely get 5× speedup.

### One worker = one (location, grid_point, query) pair

Each worker takes a pair from the `ThreadPoolExecutor.submit()` queue and handles **both** offsets (0 and 20) sequentially. This is why the early-exit rule still works: if `offset=0` returns <20, the same worker decides to skip `offset=20` — no inter-worker coordination needed.

Tasks (offset=0 *and* offset=20 if applicable) are the unit of work. There are ~`locations × grid_points × queries` tasks. Each takes 1–2 API calls. At 5 workers and ~2s/call, you get roughly 2.5 task pairs per second — i.e. about 4–5 API calls per second sustained.

### Why not async (asyncio)?

Three reasons:
1. Threading was sufficient (the bottleneck is I/O wait, not CPU).
2. Simpler to reason about — `threading.Lock` is well-understood; async ordering gets weird.
3. The `requests` library is sync. Switching to `aiohttp` for marginal speedup wasn't worth the rewrite.

If a future version needs to scale to 50+ workers (probably never — the API rate-limits before then), async would be the right call.

---

## The caffeinate / system sleep gotcha

The single biggest cause of slow scrapes on macOS isn't the API or the code — **it's the OS putting the system to sleep**.

### What `caffeinate -dis` does

| Flag | Prevents |
|---|---|
| `-d` | Display sleep |
| `-i` | Idle sleep (no input for X minutes) |
| `-s` | System sleep — **only on AC power**. Ignored on battery. |
| `-m` | Disk idle sleep |

The recommended wrapper:

```bash
nohup caffeinate -dis python3 -u scraper.py > run.log 2>&1 &
disown
```

### Why `-s` doesn't help on battery

When the laptop is on battery, macOS enters "Sleep Service Back to Sleep" and "Maintenance Sleep" cycles even with `caffeinate -is`. The OS keeps TCP connections alive (TCPKeepAlive=active) but suspends most other work, including network throughput. The scraper's threads block on socket reads until the next wake.

**You'll see**: cumulative calls/min drops dramatically (from ~130 to ~10–15) for hours at a time, then recovers when you wake the laptop.

### The fixes

1. **Plug into AC** — `-s` activates, no more idle sleeps. The simplest fix and usually sufficient.
2. **External monitor + lid closed** — Mac stays "awake" because external display counts as an active display.
3. **Accept the slow patches** — the scraper handles them gracefully via `--resume`. Multi-day runs on a personal Mac work fine; you'll just see uneven cumulative throughput.
4. **Cloud VM** (optional, see below) — for fully hands-off runs where you don't want to think about the laptop at all. Not necessary, but listed for completeness.

### Detecting if sleep is your problem

```bash
# Power state history (last few days)
pmset -g log | grep -E "Sleep|Wake" | grep -v "Assertion"

# Current power source
pmset -g batt
```

If you see lots of "Entering Sleep state due to 'Sleep Service Back to Sleep' ... Using Batt", you're sleeping despite caffeinate.

---

## The cumulative-vs-recent rate problem

The status file's ETA uses cumulative rate: `total_calls / total_elapsed_seconds`. This is dragged down by any past slow periods — sleep, network drops, 429 storms. Right after recovery, the cumulative rate is artificially low.

### How to get a real rate

```bash
# Sample CSV size over 60 seconds, infer real throughput
CALLS1=$(grep -c "^\[" run.log)
sleep 60
CALLS2=$(grep -c "^\[" run.log)
echo "API calls/min right now: $((CALLS2 - CALLS1))"
```

A healthy 5-worker scrape sustains 100–150 calls/min. If your cumulative says 15/min but the 60-second sample says 130/min, you've had slow patches but you're fine now.

---

## Recovery from network outages

If you lose network during a long-running scrape (laptop sleep, wifi drop, ISP blip), the workers may not fully recover when the connection comes back. Symptom: throughput stays at roughly 1/5 of baseline (e.g. ~17 cpm vs the usual ~100 cpm with 5 workers) and doesn't climb even after the network is healthy. Cause: workers stuck cycling through the long backoff ladder ([5, 15, 30, 60] seconds) from failed requests during the outage — the retries pile up and each worker keeps eating long sleeps.

**Fix:** kill the process and restart with `--resume`.

```bash
kill <pid>             # SIGTERM — the signal handler flushes state cleanly
# (verify the process exited; lock file should be gone)
nohup caffeinate -dis python3 -u scraper.py --resume > run.log 2>&1 &
disown
```

Fresh worker pool, no leftover backoff state. Within ~60 seconds you should see the live rate (measured via the snippet above) back at baseline. State is durable — `completed_tasks` saves every 50 calls and the CSV is per-row flushed — so you lose at most ~30 calls of work.

This is also the right move if you ever see the rate degrade gradually for no obvious reason. A clean restart costs almost nothing.

---

## Resume mechanics

`--resume` reads:

1. **`{project}_progress.json`** → loads `completed_tasks` set
2. **`{project}_seen_ids.txt`** → loads `seen_ids` set
3. **`{project}_results.csv`** → opens for **append** (`mode='a'`), no header re-written

Then when iterating tasks:
```python
if task_key in state.completed_tasks:
    return   # skip — already done
```

So a resume:
- Never re-makes an API call for an already-done task
- Never re-writes a row for an already-seen `business_id`
- Continues incrementing call/row counters from the saved values
- Picks up partial offset=0/offset=20 pairs correctly (offset=20 task is marked done either when its own call is made OR when offset=0 triggered the early-exit)

**One edge case to be aware of**: if the scraper crashes between writing to the CSV and updating `completed_tasks`, the next resume will re-call the API and the dedup filter will catch the duplicate. Slight inefficiency, no data corruption.

---

## Optional: Cloud VM deployment

**You don't need this.** Running on a personal Mac (plugged in) handles multi-day scrapes fine. The scraper's resume mechanics survive Wi-Fi drops, brief sleeps, and even hard reboots.

This section is kept as a reference for the day you want fully hands-off runs — no laptop lid to worry about, no battery to manage, no home Wi-Fi to depend on. A basic VM runs ~$5/month and a multi-day scrape costs maybe a dollar in pro-rated VM time.

The setup, when you're ready:

```bash
# On a fresh Ubuntu droplet (DigitalOcean, AWS Lightsail, Linode, etc.)
sudo apt update && sudo apt install -y python3-pip tmux
pip3 install requests
git clone https://github.com/<your-username>/google-maps-deep-scrape
cd google-maps-deep-scrape/scripts
cp scraper.py ../my-project/      # copy template to project folder
cd ../my-project/
# edit CONFIG block, drop in locations.json

export RAPIDAPI_KEY=...
tmux new -s scrape
python3 -u scraper.py | tee run.log
# Ctrl+B, then D = detach. Reattach later with: tmux attach -t scrape
```

Useful if/when you ever want it. Otherwise, ignore this section.

---

## When you actually need to modify the engine

Almost never. But if you must:

1. **Adding a new column** — edit `FIELDNAMES` AND `to_row()`. Run with `--reset` so the new column appears in a fresh CSV (else you'll have inconsistent headers across resume sessions).
2. **Changing CHECKPOINT_EVERY** — fine, no migration needed.
3. **Changing the backoff ladder** — edit `backoffs` in `search_maps()`. Going more aggressive (shorter waits) risks getting permanently rate-limited.
4. **Adding a new safety feature** — please document it here when you do.

**What you should NOT do**:
- Remove the per-row `flush()` (catastrophic for crash recovery)
- Remove the lock or relax its scope (race conditions corrupt state)
- Change the file paths halfway through a run (resume breaks)
- Use SIGKILL to stop (state isn't flushed — wastes the next resume's first few hundred dedup checks)

---

## Debugging checklist when something's wrong

| Symptom | First check | Likely cause |
|---|---|---|
| "another scraper is already running" but you don't think one is | `ps -ef \| grep scraper` | Real other instance OR stale lock file (PID dead, just delete) |
| No new rows for >5 min, no errors in log | `pmset -g batt` and `pmset -g log \| grep Sleep` | System sleep on battery |
| Rate dropped from 130 to 30 cpm | `tail -100 run.log` for 429 patterns | API throttling, will recover |
| Crashed mid-run | `--resume` | Should just work; everything is recoverable |
| `--resume` says 0 completed tasks | Are you in the right directory? `OUTPUT_DIR` correct? | Looking for state files in the wrong dir |
| Worker exceptions in log | Read the traceback | Usually a transient HTTP error; the worker dies but others continue |
| CSV looks corrupt | Don't! Check `wc -l` | Probably fine; just an editor having trouble with size |

---

## Why this is overkill for small scrapes (and that's fine)

A 10-zip pilot doesn't need lock files, atomic checkpoints, signal handlers, or any of this. But:

- The cost of the safety stack on a small run is **<1 second of overhead total**
- The cost of *not* having it on a 12-hour run is **the whole run**
- Maintaining two versions (lite + full) doubles maintenance and lets bugs diverge

So we always use the full stack. The pilot files just have a `pilot_` prefix and a small input — same code, same safety.

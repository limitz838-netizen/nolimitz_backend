# Turning on sharding — step by step

Your worker already supports running several processes in parallel.
It's just set to 1. This turns it on.

Do the steps in order. Don't skip step 1 — without a baseline you
won't know whether it worked.

---

## Step 0 — Understand what you're doing

Right now: **one** MT5 terminal logs into account 1, trades, logs out,
logs into account 2, trades, logs out... Each login takes 1–3 seconds.

After this: **two** terminals do it side by side. Account 1 and account
2 get handled at the same time instead of one after the other.

That's it. No strategy change. No new logic. Same code, run twice.

---

## Step 1 — Measure what you have now

Open `app/ai/ai_execution_worker.py`. Near the very bottom, find:

```python
        elapsed = time.time() - cycle_start
        time.sleep(max(0.5, cfg.LOOP_DELAY - elapsed))
```

Add ONE line in the middle:

```python
        elapsed = time.time() - cycle_start
        logger.info("TIMING | cycle %d | %.1fs total | %d accounts | %.2fs each",
                    cycle_num, elapsed, len(process_list),
                    elapsed / max(len(process_list), 1))
        time.sleep(max(0.5, cfg.LOOP_DELAY - elapsed))
```

Save. Restart your worker. Let it run 10 minutes.

**Write down the numbers you see.** Something like:

```
TIMING | cycle 12 | 34.2s total | 11 accounts | 3.11s each
```

That "3.11s each" is your baseline. Everything below is measured against it.

---

## Step 2 — Check you have the RAM

Each terminal needs roughly 300MB–1GB.

```powershell
Get-CimInstance Win32_OperatingSystem |
  Select-Object @{n='TotalGB';e={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}},
                @{n='FreeGB';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}}
```

Need at least 2GB free to add a second terminal. If you don't have it,
stop here — sharding will just make everything swap and get slower.

---

## Step 3 — Clone the terminal

**Close every MT5 window first.** The script will refuse otherwise.

```powershell
cd sharding
.\clone_terminals.ps1 -Count 2
```

If your MT5 isn't at `C:\Users\user\Desktop\TraderMT5`:

```powershell
.\clone_terminals.ps1 -Count 2 -Source "C:\your\actual\path"
```

You should end up with `C:\MT5\shard0\` and `C:\MT5\shard1\`.

---

## Step 4 — STOP the old worker

**This is the one step that can cost you money if you skip it.**

If the old single worker keeps running alongside the shards, the same
account gets traded by both — double lot size on every signal.

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine | Format-List
```

Close the window running `ai_execution_worker`, or kill it by PID.
Confirm the list is empty before continuing.

---

## Step 5 — Start the shards

```
start_shards.bat
```

Two windows open, 25 seconds apart. In each, watch for:

```
Trader MT5 connected: path=C:\MT5\shard0\terminal64.exe
```

Then wait for the TIMING line you added in step 1.

---

## Step 6 — Compare

You should see roughly **half** the accounts in each window, and the
per-cycle total roughly halved.

| | before | after (2 shards) |
|---|---|---|
| accounts per cycle | 11 | ~5 and ~6 |
| cycle total | 34s | ~17s |
| per account | 3.1s | 3.1s (unchanged) |

Note "per account" does NOT improve — that's expected. You haven't made
each account faster; you've made two happen at once. The **cycle total**
is the number that matters, because that's how long your last user waits.

---

## Step 7 — Only now, scale up

If 2 works cleanly for a full day, edit `start_shards.bat`:

```
set SHARDS=4
```

Then re-run `clone_terminals.ps1 -Count 4` and restart.

Stop increasing when RAM runs low or the cycle total stops improving.

---

## If something goes wrong

**"MT5 init failed" in a shard window**
That shard's terminal didn't start. Open
`C:\MT5\shard0\terminal64.exe` manually once, let it load fully, close
it, try again.

**"Login failed" for every account on one shard**
The cloned terminal doesn't know that broker's server yet. Open it
manually, File → Open an Account, search the broker name so it caches
the server list. Close, retry.

**Both shards processing the SAME accounts**
`SHARD_TOTAL` isn't being read. Check both windows print
`shard 0 of 2` and `shard 1 of 2` in their startup banner. If either
says `of 1`, the env var isn't reaching Python.

**Everything got slower**
You're out of RAM and Windows is swapping. Drop back to fewer shards.

---

## Rolling back

There's nothing to undo in the code. Just:

```
stop_shards.bat -all
```

then start your single worker the way you always did. The TIMING line
you added is harmless — keep it, it's useful forever.

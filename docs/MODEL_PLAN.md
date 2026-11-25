# Model plan for the hosted app (2026-11-25)

The older key (AQ.) reaches Flash-Lite, the Decisions API and the agent, but Gemini 4 Neon times out on it
(40 s contract, 30 s checker). So the hosted app needs a contract writer and a checker that aren't Neon.

## What we tested (real runs, 2026-11-25)

**Flash-Lite replay.** 44 past requests that Neon compiled at the hackathon (32 with a photo, 31 with voice), replayed
with the same audio, photo and prompt. Scripts and raw output were in the session scratchpad (not committed).

| | Result |
|---|---|
| 3 requests at a time | 30 of 44 compiled, **14 hung for 120 s** (photo and text alike) |
| Retrying 8 of those hung requests one at a time | **8 of 8 compiled**, 1.7 to 35 s |
| Latency when it answers | median 4.1 s (Neon at the event: 9.4 s), but some take 35 to 110 s |

So the hangs come from requests running at the same time on this key (free-tier limits), not from photos.

**Quality vs Neon (the 22 photo requests that compiled):**
- Right product every time: brand, flavor and size matched Neon's (Red Bull 8.4 oz, Premier Protein chocolate
  11 oz 30 g, That's it apple strawberry mini, Sharpie fine black, Pioneer DDJ-400, Tissot PRX green, Cheez-It 1.5 oz).
- Fewer, looser clauses: it drops details like "no sugar added", "rolled rim", "plain, unprinted", and twice wrote
  a clause that can't be checked ("match the item requested").
- Better at product links: it copied the Monday Cartoons URL from the photo 4 times, where Neon left `product_url` empty.
- One mis-hearing: "8 ounces, white, plain, under $15" became "get a pack of 100" (the budget was lost).
- When you don't say a budget, it sets a lower one ($5 for a Red Bull, where Neon set $10 to $15). With shipping, that
  means more "raise the budget?" prompts.

**Qwen3.8-27B on the Spark** (vLLM, thinking off, Neon's transcript as text, since it can't hear audio). 3 photo requests:
- It reads images. Quality is close to Neon's: for the paper cups it got 8 oz, plain, white, multi-pack and the $15
  budget. It also read the sticker URL.
- One mistake: for the fruit bar it took the box weight (3.5 oz) as the bar size.
- 50 to 61 s per contract.

## Recommendation

**1. Use Flash-Lite for the hosted app now** (contract, checker and agent), with these fixes:
- One model call at a time (a lock), a real per-call timeout and retries, in place of a 120 s hang.
  This matches what worked one at a time. (Built values are in "Step 1 done" below: 45 s, 3 retries.)
- Prompt changes: every must clause names a concrete attribute visible in the photo (brand, flavor, size, color);
  never write "matches the item"; when no budget was said, the default covers typical shipping (at least $10).
- Checker: `CHECKER_MODEL=gemini-3.5-flash-lite`. If it doesn't answer, the guard asks the shopper, as it does now.
- Test: replay the same 44 requests one at a time, and run the red-team eval with the Flash-Lite checker.
  Tanushree owns `guard/` and `evals/`, so send her the checker numbers.

**2. Keep the Spark as the backup, not the main path.** Qwen3.8 can read photos, so we don't need a new open-source model. But:
- **Vercel can't reach it.** The gateway is tailnet only. Reaching it from Vercel needs a public exposure (Funnel, or
  a tunnel, with its own key). That's a new network exposure, so it's Akaash's decision.
- **It can't hear audio.** Fix that on the phone: switch the Shortcut's "Record Audio" step to iOS "Dictate Text" and
  send the text. That also makes Flash-Lite cheaper and faster.
- **At 50 to 60 s per contract, a whole run comes to 1.5 to 2 minutes.** That's still under the 250 s cap.
- **Where it fits:** the laptop or Spark mode, or a fallback when Flash-Lite fails twice (once exposure is decided).

**3. Don't add a new model to the Spark now.** Memory is tight: a `vllm-fast` start already fails 1 time in 3 on KV
cache. A smaller vision model only makes sense if Qwen3.8's speed becomes the problem.

## Step 1 done (2026-11-25)

**Built.** `common/modelcall.py` is now the one door for every Gemini call on the key (contract, checker, agent turns,
Decisions). It serializes calls (a process lock; hosted also a Redis semaphore, `SET NX` with a TTL a few seconds longer
than the call, through `RedisStore.lock_acquire/lock_release`, injected from `server/app.py`), puts a real HTTP timeout on
each request (`HttpOptions.timeout`, 45 s; 60 s for agent turns; 10 s for Decisions), retries up to 3 times on timeout, 429 or 5xx
(waits 3, 6, 9 s plus jitter; Decisions 1 retry), and logs every attempt with `log_usage` (a 429 shows as `error="429"`; the logged latency includes the wait for
the lock). The lock is released by the worker thread when the underlying call really ends, never when the watchdog gives up.
Defaults: `CONTRACT_MODEL` and `CHECKER_MODEL` are `gemini-3.5-flash-lite` (env vars still override; `thinking_level` is
still sent only to gemini-4 models). Prompt: every must clause names a checkable attribute, never "matches the item", and an
unstated budget covers item plus shipping and tax. Code floor: unstated budget under $1000 cents becomes 1000, with an
assumption line.

**SDK finding (google-genai 3.6.1).** For the Interactions and Decisions clients the SDK retries by default: 5 attempts with
0.5 to 8 s backoff, capped at about 30 s in total, on 408/429/5xx. That is the "~34 s" cluster. We now pass
`HttpRetryOptions(attempts=1)`, so our own retries are the only ones. A 50 ms test timeout produced `APITimeoutError` and exactly
two attempts.

**Run deadline.** Hosted runs keep `RUN_DEADLINE_S = 190`. The old "90 s per model call" cap is replaced by: every call made
on the run's thread (agent turns plus the checker and Decisions calls inside tools) is clipped to the deadline (lock wait
never goes past it, no retry starts within 10 s of it, an attempt gets at least 10 s). Worst case is now about 190 + 10 s
plus the watchdog grace, instead of 190 + 90.

**Measured (real runs, 2026-11-25, one call at a time).**
- Google is returning `503 "gemini-3.5-flash-lite is currently experiencing high demand"` on roughly half of calls right now,
  and the same for gemini-3.8-flash and gemini-4-hydrogen. That is not our concurrency. With the default one retry, the
  44-request replay compiled **19 of 44**. With `MODEL_RETRIES=3` the 25 failed ones compiled 23 of 25 (combined 42 of 44).
- Replay latency (combined 42 ok, includes retries and 3 s waits): min 1.6, median 6.1, p90 13.2, max 21.4 s.
- Vague clauses ("match the item" and similar): **0 of 42** (earlier replay: 2 of 22).
- Unstated budgets: 25 of 42; the code floor raised 1 of them (the new prompt already writes $10 or more).
- Product identity on the 31 photo requests that compiled (29 with a product): brand, flavor and size matched Neon's in
  every case; the clauses are shorter ("rolled rim", "no sugar added", "plain" are still dropped for paper cups and fruit bars).
  Photos Neon called "unidentified" (4): Flash-Lite wrote "generic item" with a $25 cap, "unspecified item" $50, and once guessed
  "laundry detergent". These need a human look.
- 3 contracts at once from threads: all 3 compiled, one at a time (finished at 2.7, 6.4 and 9.6 s, which includes one 503 and
  its retry).
- Red-team eval with the Flash-Lite checker (35 stored cases, 2 runs, same cached Neon contracts): attacks through **0/23**
  (baseline 0/23); legit blocked **0/12** (baseline 1/12); legit sent to a human **7/12** (baseline 7/12). Stability between the
  two runs 31 of 35 (baseline 35 of 35): `att_public_goodside` and `att_public_greshake` flipped between ask_human and block,
  and two legit cases flipped. 28 checker attempts failed (20 x 503, 4 x 429, 4 timeouts; some succeeded on the retry, the rest became ask_human).
- End to end on the laptop, "one can of original Red Bull", with 3 retries (3, 6, 9 s plus jitter) and a 60 s agent timeout:
  run A: `ask_human`, Red Bull 8.4 oz from Top Notch Treats, $3.35 delivered, 72.7 s (agent turns 4.3, 20.8, 5.0, 6.3 s; one
  cart blocked as an enamel pin).
  Run B (first try): no verdict after 322 s; I stopped it. One agent turn took 214 s (3 timeouts of 60 s plus retries, laptop has no run deadline).
  Run C: `ask_human`, Red Bull 8.4 oz from J&R Tack & Feed, $8.00 delivered ($5 shipping), 300 s (agent turns 23, 70, 21, 143 s; one cart blocked as a 4-pack).
  Every ask_human reason was the checker's real text ("matches all contract requirements", "matches the flavor, type and size"), i.e. a
  genuine escalation, not a 503: checker 503s (1 to 2 per run) were retried and then answered.
  Errors in usage.jsonl for these runs: A: checker 503 x2. B: agent timeout x3, checker 503 x1. C: contract 503 x2,
  agent timeout x3, checker 503 x1. Agent turns hit 60 s timeouts about once per run.

**Retries.** Default is now 3 retries with growing waits (`MODEL_RETRIES`, `MODEL_RETRY_DELAY_S`); Decisions keeps 1. A retry never starts
within 10 s of the hosted run deadline, but a laptop run has no deadline, so a stuck agent turn can take 4 x 60 s.

**Open.** Hosted: one global semaphore means concurrent shoppers queue (lock wait is capped at 40 s).

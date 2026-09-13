---
name: aipass-auto-router
version: 1.0.0
description: Route a prompt to the right model on an AIPass-style multi-model chat site, using a Brave (or any Chromium) browser you are already logged into, over the Chrome DevTools Protocol. Picks Claude 3.5 Sonnet / DeepSeek-V3 for coding, DeepSeek-R1 for maths and deep research, Typhoon-2 for Thai writing, Gemini 3.1 Flash Lite for quick questions. When a model hits a rate limit it parks that model for 15 minutes and fails over to the next one by itself, then pulls the answer back to the local machine as text plus JSON. Use when the user says "route this to the best model", "auto switch model", "สลับโมเดลอัตโนมัติ", "โมเดลติด rate limit", "ยิงผ่านเบราว์เซอร์ที่ล็อกอินไว้", or asks to drive a logged-in chat site from the terminal. Not for calling vendor APIs with an API key, not for general web scraping, and not for driving sites other than the configured chat site.
---

# aipass-auto-router

Turn a logged-in browser tab into a routed, self-healing model backend.
เปลี่ยนแท็บเบราว์เซอร์ที่ล็อกอินค้างไว้ ให้กลายเป็นตัวเลือกโมเดลอัตโนมัติ

Three jobs, all done by `scripts/router.py`:

1. **Route by task** — read the prompt, pick the model that suits it.
2. **Fail over on rate limits** — park the blocked model for 15 minutes, move to the next one, no clicking.
3. **Bring the answer home** — print it to stdout and save a JSON record locally for further processing.

No API key. The skill reuses the session already sitting in your browser profile.

## Before anything: start the browser with a debug port

The browser must be launched with `--remote-debugging-port`. **Quit Brave completely first**, then:

```bash
# macOS
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222

# Windows (PowerShell)
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222

# Linux
brave-browser --remote-debugging-port=9222
```

Then log in to the chat site in that window, once. Details and the profile-flag variants: [references/setup-brave.md](references/setup-brave.md).

Check the link before trusting anything else:

```bash
python3 scripts/router.py doctor
```

`input_found: true` means the page is drivable. Anything `false` is a selector to fix — see [references/tuning-selectors.md](references/tuning-selectors.md).

## Usage

```bash
# Which model would this go to, and why?
python3 scripts/router.py route "เขียนฟังก์ชัน Python อ่านไฟล์ CSV"

# Ask for real: route, switch model in the UI, wait, fail over if blocked
python3 scripts/router.py ask "แต่งแคปชั่นขายกาแฟสด 5 แบบ"

# Full machine-readable record instead of plain text
python3 scripts/router.py ask "prove sqrt(2) is irrational" --json

# Force the task class, or try one model first
python3 scripts/router.py ask "..." --task reasoning
python3 scripts/router.py ask "..." --model deepseek-r1

# Save the answer straight to a file for the next step
python3 scripts/router.py ask "..." --out draft.md

# What is parked right now, and until when
python3 scripts/router.py status

# What the site actually offers (names must match config)
python3 scripts/router.py models

# Unpark
python3 scripts/router.py reset            # everything
python3 scripts/router.py reset typhoon-2  # one model
```

Exit codes: `0` answered · `1` browser or page problem · `3` every model in the chain is cooling · `4` tried them all, none answered.

## 1. Routing table

| Task | Detected from | Chain (first choice first) |
|------|---------------|----------------------------|
| `code` | code words, code fences, language names, `เขียนโค้ด` `แก้บั๊ก` `ฟังก์ชัน` | Claude 3.5 Sonnet → DeepSeek-V3 → DeepSeek-R1 → Gemini 3.1 Flash Lite |
| `reasoning` | maths, proofs, research, `คำนวณ` `พิสูจน์` `วิจัย` `วิเคราะห์เชิงลึก` | DeepSeek-R1 → Claude 3.5 Sonnet → DeepSeek-V3 → Gemini 3.1 Flash Lite |
| `thai` | Thai writing verbs: `แต่ง` `เรียบเรียง` `แคปชั่น` `แปลเป็นไทย` | Typhoon-2 → Claude 3.5 Sonnet → Gemini 3.1 Flash Lite → DeepSeek-V3 |
| `quick` | short prompts, `คืออะไร` `สั้นๆ` `what is` | Gemini 3.1 Flash Lite → DeepSeek-V3 → Claude 3.5 Sonnet → DeepSeek-R1 |

A Thai prompt that asks for code is `code`, not `thai` — the classifier scores technical intent above the language it is written in. Everything is data in `config/models.json`; edit the chains and keyword lists there rather than the code.

## 2. Rate-limit failover

A model counts as rate limited when either signal fires **in content that appeared after this send**:

- an HTTP `429` or `402` response observed on the page, or
- a phrase from `rate_limit.text_patterns` (`rate limit`, `quota exceeded`, `เกินโควต้า`, …).

Then: park it for 15 minutes in `~/.aipass-router/state.json`, switch the UI to the next model in the chain, resend, all without asking. A parked model is skipped by every later run until its clock runs out. Change the duration with `cooldown_minutes`.

If a model answers nothing at all within the timeout, that also triggers a move to the next model — but no cooldown, since silence is not a limit.

## 3. Answers come back locally

Every successful `ask` prints the answer to stdout and writes `~/.aipass-router/runs/<timestamp>-<model>.json`:

```json
{"ok": true, "task": "thai", "model": "typhoon-2", "prompt": "...", "answer": "...",
 "elapsed_s": 12.4, "attempts": [{"model": "typhoon-2", "result": "ok"}]}
```

So the normal pattern is: `ask --out draft.md`, then keep working on `draft.md` locally — diff it, edit it, feed it to the next step.

## Acceptance criteria

Offline part, no browser needed — `python3 scripts/selftest.py` must print `SELFTEST PASS`
(14 routing cases, chain integrity, cooldown park/skip/clear).

- [ ] `router.py doctor` reports `browser_reachable: true` and `input_found: true`.
- [ ] `router.py models` lists the site's model names, and every name in `config/models.json` appears among them.
- [ ] `router.py route` returns the expected task for a coding, a maths, a Thai-writing and a short general prompt.
- [ ] A rate-limited model shows up in `router.py status` with minutes remaining, and the same run still returns an answer from the next model.
- [ ] The answer file under `~/.aipass-router/runs/` matches what was printed.

## Boundaries

| Do | Don't — hand off instead |
|----|--------------------------|
| Drive the one chat site named in `config/models.json` | General web scraping or automating other sites — use a browser-automation tool |
| Use the browser session already logged in | Handle passwords, solve CAPTCHAs, or create accounts — the human logs in once, by hand |
| Route between models the site already gives you | Call vendor APIs directly — if you have an API key, use the vendor SDK, it is faster and more reliable |
| Return the answer for local processing | Judge whether the answer is correct — that is the caller's job |

Anything requiring a login, a payment, or accepting terms stops and asks the human.

## Pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| `cannot reach the browser on port 9222` | Brave was started without the flag, or an old instance is still running | Quit Brave fully, relaunch with `--remote-debugging-port=9222` |
| `doctor` connects but `input_found: false` | The site changed its markup | Add the real selector to `selectors.input` in `config/models.json` |
| `model X not offered. Visible options: (none)` | The dropdown toggled shut instead of open | Already handled by a retry; if it persists, fix `selectors.model_option` |
| Every model reports a rate limit at once | An old limit notice sitting in the transcript | Already scoped to new content only; start a fresh chat if the site pins a banner |
| Answer text contains the question, or two answers glued together | Assistant-message selector matched nothing, so the text-diff fallback ran | Set `selectors.assistant_message` to the real one; `doctor` shows whether it matches |
| Timed out on a long answer | Slow model | Raise `answer.max_wait_s` in the config |
| Cooldowns feel wrong after testing | State persists on disk | `router.py reset` |

## Configuration

`config/models.json` holds everything: site URL, model labels and their aliases, routing chains, classifier keywords, rate-limit patterns, CSS selectors, timeouts. To customise without touching the repo, copy it to `~/.aipass-router/models.json` — that copy wins.

Environment: `AIPASS_CDP_PORT` (default `9222`), `AIPASS_ROUTER_HOME` (default `~/.aipass-router`).

## Install

```bash
cp -r lab/Geek-skills-aipass-auto-router ~/.claude/skills/aipass-auto-router
```

## Safety

The scripts talk to `127.0.0.1:<debug port>` only — no outbound calls of their own. But a debug port is full control of that browser: anything running on your machine can drive the same session. Open it when you need it, close the browser when you are done, and do not leave it open on a shared machine. The skill stores no passwords and no API keys; it writes only under `~/.aipass-router/`, and never deletes anything.

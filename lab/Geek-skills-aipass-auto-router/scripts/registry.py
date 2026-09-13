"""Task classification, model routing chains and rate-limit cooldown state."""

import json
import os
import re
import time

HOME_DIR = os.path.expanduser(os.environ.get("AIPASS_ROUTER_HOME", "~/.aipass-router"))
STATE_PATH = os.path.join(HOME_DIR, "state.json")
RUNS_DIR = os.path.join(HOME_DIR, "runs")

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "models.json")
_USER_CONFIG = os.path.join(HOME_DIR, "models.json")

THAI_RE = re.compile(r"[฀-๿]")
CODE_FENCE_RE = re.compile(r"```|\bdef \w+\(|\bclass \w+|\bfunction \w+\(|=>|;\s*$", re.M)


def load_config(path=None):
    """User copy at ~/.aipass-router/models.json wins over the bundled default."""
    for candidate in (path, _USER_CONFIG, _DEFAULT_CONFIG):
        if candidate and os.path.exists(candidate):
            with open(candidate, encoding="utf-8") as fh:
                cfg = json.load(fh)
            cfg["_path"] = os.path.abspath(candidate)
            return cfg
    raise FileNotFoundError("no models.json found")


# ---------------------------------------------------------------- classify
def classify(prompt, config):
    """Return (task, scores). Task is one of code / reasoning / thai / quick."""
    text = prompt.lower()
    kw = config["classifier"]
    scores = {k: 0.0 for k in ("code", "reasoning", "thai", "quick")}

    for task, words in kw.items():
        for word in words:
            if word.lower() in text:
                scores[task] += 1.0

    thai_chars = len(THAI_RE.findall(prompt))
    thai_ratio = thai_chars / max(len(prompt), 1)
    if thai_ratio > 0.25:
        scores["thai"] += 1.0          # written in Thai, but not automatically a Thai-writing job
    if CODE_FENCE_RE.search(prompt):
        scores["code"] += 2.0
    words_n = len(prompt.split())
    if words_n <= 12 and scores["code"] == 0 and scores["reasoning"] == 0:
        scores["quick"] += 1.0
    if words_n > 60:
        scores["quick"] -= 1.0
        scores["reasoning"] += 0.5

    # Technical intent outranks "it happens to be Thai".
    if scores["code"] >= 1 and scores["thai"] <= 1:
        scores["thai"] = 0.0
    if scores["reasoning"] >= 1 and scores["thai"] <= 1:
        scores["thai"] = 0.0

    priority = ["code", "reasoning", "thai", "quick"]
    best = max(priority, key=lambda t: (scores[t], -priority.index(t)))
    if scores[best] <= 0:
        best = "quick"
    return best, scores


def chain_for(task, config):
    route = config["routes"].get(task) or config["routes"]["quick"]
    return list(route["chain"])


# ------------------------------------------------------------------ state
def _read_state():
    if not os.path.exists(STATE_PATH):
        return {"cooldowns": {}, "history": []}
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"cooldowns": {}, "history": []}
    state.setdefault("cooldowns", {})
    state.setdefault("history", [])
    return state


def _write_state(state):
    os.makedirs(HOME_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def cooldowns(now=None):
    """Active cooldowns as {model: seconds_remaining}. Expired ones are dropped."""
    now = now or time.time()
    state = _read_state()
    live = {m: until for m, until in state["cooldowns"].items() if until > now}
    if len(live) != len(state["cooldowns"]):
        state["cooldowns"] = live
        _write_state(state)
    return {m: int(until - now) for m, until in live.items()}


def is_cooling(model):
    return model in cooldowns()


def start_cooldown(model, minutes, reason=""):
    state = _read_state()
    until = time.time() + minutes * 60
    state["cooldowns"][model] = until
    state["history"].append(
        {"ts": time.time(), "event": "cooldown", "model": model,
         "minutes": minutes, "reason": reason}
    )
    state["history"] = state["history"][-200:]
    _write_state(state)
    return until


def clear_cooldown(model=None):
    state = _read_state()
    if model:
        state["cooldowns"].pop(model, None)
    else:
        state["cooldowns"] = {}
    _write_state(state)


def record(entry):
    state = _read_state()
    entry = dict(entry)
    entry.setdefault("ts", time.time())
    state["history"].append(entry)
    state["history"] = state["history"][-200:]
    _write_state(state)


def history(limit=20):
    return _read_state()["history"][-limit:]


def available_chain(task, config):
    """Chain split into (ready, cooling) preserving priority order."""
    cool = cooldowns()
    full = chain_for(task, config)
    ready = [m for m in full if m not in cool]
    cooling = [(m, cool[m]) for m in full if m in cool]
    return ready, cooling

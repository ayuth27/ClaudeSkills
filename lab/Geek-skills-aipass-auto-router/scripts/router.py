#!/usr/bin/env python3
"""aipass-auto-router - task-aware model routing with automatic failover.

Drives an already-logged-in Brave tab over the Chrome DevTools Protocol.

  router.py route  "<prompt>"        # show which model would be used
  router.py ask    "<prompt>"        # route, ask, fail over, print the answer
  router.py status                   # cooldowns and recent runs
  router.py models                   # model names the page actually offers
  router.py doctor                   # check browser link and selectors
  router.py reset [model]            # clear cooldowns
  router.py cooldown <model> [-m N]  # park a model by hand
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import registry  # noqa: E402
from cdp import Browser, CDPError  # noqa: E402
from page import ChatPage  # noqa: E402


def log(msg):
    print("[router] %s" % msg, file=sys.stderr, flush=True)


def open_page(cfg, port, quiet=False):
    site = cfg["site"]
    browser = Browser(port=port).attach(site["url_contains"], open_url=site["url"])
    chat = ChatPage(browser, cfg, log=(lambda *_: None) if quiet else log)
    chat.boot()
    chat.wait_ready(site["load_timeout_s"])
    return browser, chat


# ---------------------------------------------------------------- commands
def cmd_route(args, cfg):
    guess, scores = registry.classify(args.prompt, cfg)
    task = args.task or guess
    ready, cooling = registry.available_chain(task, cfg)
    out = {
        "task": task,
        "why": cfg["routes"][task]["why"],
        "scores": {k: round(v, 1) for k, v in scores.items()},
        "chain": registry.chain_for(task, cfg),
        "ready": ready,
        "cooling": [{"model": m, "seconds_left": s} for m, s in cooling],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args, cfg):
    cool = registry.cooldowns()
    out = {
        "config": cfg["_path"],
        "cooldown_minutes": cfg["cooldown_minutes"],
        "cooling": [
            {"model": m, "seconds_left": s, "minutes_left": round(s / 60, 1)}
            for m, s in sorted(cool.items(), key=lambda kv: -kv[1])
        ],
        "ready": [m for m in cfg["models"] if m not in cool],
        "recent": registry.history(args.limit),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_reset(args, cfg):
    registry.clear_cooldown(args.model)
    print(json.dumps({"cleared": args.model or "all"}, ensure_ascii=False))
    return 0


def cmd_cooldown(args, cfg):
    if args.model not in cfg["models"]:
        log("unknown model %r; known: %s" % (args.model, ", ".join(cfg["models"])))
        return 2
    until = registry.start_cooldown(args.model, args.minutes, reason="manual")
    print(json.dumps(
        {"model": args.model, "minutes": args.minutes,
         "until": time.strftime("%H:%M:%S", time.localtime(until))},
        ensure_ascii=False))
    return 0


def cmd_models(args, cfg):
    browser, chat = open_page(cfg, args.port)
    try:
        opened = chat.open_picker()
        options = chat.call("listOptions", chat.sel["model_option"]) or []
        chat.close_picker()
        print(json.dumps(
            {"picker_found": bool(opened), "current": chat.current_model(),
             "options": options, "configured": list(cfg["models"])},
            ensure_ascii=False, indent=2))
    finally:
        browser.close()
    return 0


def cmd_doctor(args, cfg):
    report = {"port": args.port, "config": cfg["_path"]}
    try:
        b = Browser(port=args.port)
        pages = b.list_pages()
        report["browser_reachable"] = True
        report["tabs"] = [p.get("url", "")[:100] for p in pages][:15]
    except CDPError as exc:
        report["browser_reachable"] = False
        report["error"] = str(exc)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    try:
        browser, chat = open_page(cfg, args.port)
        report["site_tab"] = browser.page_url[:120]
        report["input_found"] = bool(chat.call("has", chat.sel["input"]))
        report["send_button_found"] = bool(chat.call("has", chat.sel["send_button"]))
        report["model_picker_found"] = bool(chat.call("has", chat.sel["model_picker"]))
        report["assistant_message_selector_matches"] = bool(
            chat.call("has", chat.sel["assistant_message"]))
        report["current_model"] = chat.current_model()
        browser.close()
    except CDPError as exc:
        report["page_error"] = str(exc)
    report["cooling"] = registry.cooldowns()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("input_found") else 1


def cmd_ask(args, cfg):
    task = args.task or registry.classify(args.prompt, cfg)[0]
    if args.model:
        candidates = [args.model] + [m for m in registry.chain_for(task, cfg) if m != args.model]
    else:
        candidates = registry.chain_for(task, cfg)

    cool = registry.cooldowns()
    ready = [m for m in candidates if m not in cool]
    if not ready:
        soonest = min(cool[m] for m in candidates if m in cool)
        log("every model for task %r is cooling; next free in %ds" % (task, soonest))
        if not args.wait:
            print(json.dumps({"ok": False, "task": task, "reason": "all_cooling",
                              "seconds_until_next": soonest}, ensure_ascii=False, indent=2))
            return 3
        log("waiting %ds" % (soonest + 2))
        time.sleep(soonest + 2)
        ready = [m for m in candidates if m not in registry.cooldowns()]

    log("task=%s chain=%s" % (task, " > ".join(ready)))
    browser, chat = open_page(cfg, args.port)
    attempts = []
    try:
        browser.bring_to_front()
        for model in ready:
            ok, detail = (True, "switch skipped") if args.no_switch else chat.select_model(model)
            if not ok:
                log("cannot select %s: %s" % (model, detail))
                attempts.append({"model": model, "result": "unavailable", "detail": detail})
                continue
            log("asking %s (%s)" % (model, detail))
            answer, meta = chat.ask(args.prompt)
            limited = chat.rate_limited(answer, meta)
            if limited:
                mins = cfg["cooldown_minutes"]
                registry.start_cooldown(model, mins, reason=limited)
                log("%s is rate limited (%s) -> parked %d min, failing over" % (model, limited, mins))
                attempts.append({"model": model, "result": "rate_limited", "detail": limited})
                continue
            if meta["empty"] or not answer:
                log("%s produced nothing in %ss, trying the next model" % (model, meta["elapsed_s"]))
                attempts.append({"model": model, "result": "empty", "detail": meta})
                continue
            attempts.append({"model": model, "result": "ok", "detail": meta})
            return _emit(args, cfg, task, model, answer, meta, attempts)
    finally:
        browser.close()

    log("no model answered")
    print(json.dumps({"ok": False, "task": task, "attempts": attempts},
                     ensure_ascii=False, indent=2))
    return 4


def _emit(args, cfg, task, model, answer, meta, attempts):
    run = {
        "ok": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": task,
        "model": model,
        "model_label": cfg["models"][model]["label"],
        "prompt": args.prompt,
        "answer": answer,
        "elapsed_s": meta["elapsed_s"],
        "attempts": attempts,
    }
    os.makedirs(registry.RUNS_DIR, exist_ok=True)
    run_path = os.path.join(
        registry.RUNS_DIR, "%s-%s.json" % (time.strftime("%Y%m%d-%H%M%S"), model))
    with open(run_path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
    run["saved_to"] = run_path
    registry.record({"event": "answer", "task": task, "model": model,
                     "elapsed_s": meta["elapsed_s"], "saved_to": run_path})

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(answer + "\n")
        run["written_to"] = args.out

    if args.json:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    else:
        print(answer)
        log("model=%s task=%s %ss saved=%s" % (model, task, meta["elapsed_s"], run_path))
    return 0


# ------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="router.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=int(os.environ.get("AIPASS_CDP_PORT", 9222)),
                   help="Brave remote-debugging port (default 9222)")
    p.add_argument("--config", help="path to models.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("route", help="show the routing decision only")
    r.add_argument("prompt")
    r.add_argument("--task", choices=["code", "reasoning", "thai", "quick"])
    r.set_defaults(fn=cmd_route)

    a = sub.add_parser("ask", help="route, ask, fail over, return the answer")
    a.add_argument("prompt")
    a.add_argument("--task", choices=["code", "reasoning", "thai", "quick"],
                   help="override the classifier")
    a.add_argument("--model", help="try this model first")
    a.add_argument("--no-switch", action="store_true",
                   help="use whatever model the page is already on")
    a.add_argument("--wait", action="store_true",
                   help="if every model is cooling, sleep until one frees up")
    a.add_argument("--json", action="store_true", help="print the full run record")
    a.add_argument("--out", help="also write the answer to this file")
    a.set_defaults(fn=cmd_ask)

    s = sub.add_parser("status", help="cooldowns and recent runs")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(fn=cmd_status)

    m = sub.add_parser("models", help="list the model names the page offers")
    m.set_defaults(fn=cmd_models)

    d = sub.add_parser("doctor", help="check the browser link and selectors")
    d.set_defaults(fn=cmd_doctor)

    x = sub.add_parser("reset", help="clear cooldowns")
    x.add_argument("model", nargs="?")
    x.set_defaults(fn=cmd_reset)

    c = sub.add_parser("cooldown", help="park a model by hand")
    c.add_argument("model")
    c.add_argument("-m", "--minutes", type=int, default=15)
    c.set_defaults(fn=cmd_cooldown)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = registry.load_config(args.config)
    try:
        return args.fn(args, cfg)
    except CDPError as exc:
        log("browser error: %s" % exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

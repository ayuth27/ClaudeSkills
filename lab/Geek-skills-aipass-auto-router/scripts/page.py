"""Drives the AIPass chat page over CDP: pick a model, ask, read the answer."""

import json
import time

from cdp import CDPError

# Injected once per run. Everything the driver needs lives under window.__aipass.
BOOTSTRAP = r"""
(() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const pick = (sels) => {
    for (const s of sels) {
      let els;
      try { els = [...document.querySelectorAll(s)]; } catch (e) { continue; }
      els = els.filter(visible);
      if (els.length) return els[els.length - 1];
    }
    return null;
  };
  const all = (sels) => {
    for (const s of sels) {
      let els;
      try { els = [...document.querySelectorAll(s)]; } catch (e) { continue; }
      els = els.filter(visible);
      if (els.length) return els;
    }
    return [];
  };
  const txt = (el) => (el ? (el.innerText || el.textContent || '').trim() : '');

  window.__aipass = {
    visible, pick, all, txt,

    has(sels) { return !!pick(sels); },

    setInput(sels, text) {
      const el = pick(sels);
      if (!el) return false;
      el.scrollIntoView({ block: 'center' });
      el.focus();
      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
        const proto = el.tagName === 'TEXTAREA'
          ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(el, '');
        el.dispatchEvent(new Event('input', { bubbles: true }));
        setter.call(el, text);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('delete');
        document.execCommand('insertText', false, text);
        el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
      }
      return true;
    },

    inputValue(sels) {
      const el = pick(sels);
      if (!el) return null;
      return el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' ? el.value : txt(el);
    },

    clickSend(sels) {
      const el = pick(sels);
      if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
      el.click();
      return true;
    },

    streaming(stopSels) { return !!pick(stopSels); },

    snapshot(msgSels) {
      const msgs = all(msgSels);
      if (msgs.length) {
        return { mode: 'sel', count: msgs.length, text: txt(msgs[msgs.length - 1]) };
      }
      return { mode: 'body', count: 0, text: (document.body.innerText || '') };
    },

    bodyText() { return (document.body.innerText || '').slice(-40000); },

    currentModel(pickerSels) { return txt(pick(pickerSels)) || null; },

    openPicker(pickerSels) {
      const el = pick(pickerSels);
      if (!el) return false;
      el.scrollIntoView({ block: 'center' });
      el.click();
      return true;
    },

    listOptions(optSels) { return all(optSels).map(txt).filter(Boolean); },

    chooseOption(optSels, needles) {
      const opts = all(optSels);
      for (const needle of needles) {
        for (const o of opts) {
          if (txt(o).toLowerCase().includes(needle.toLowerCase())) {
            o.click();
            return txt(o);
          }
        }
      }
      return null;
    },

    closePicker() {
      document.body.click();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }
  };
  return true;
})()
"""


def _js_args(*values):
    return ", ".join(json.dumps(v, ensure_ascii=False) for v in values)


class ChatPage:
    def __init__(self, browser, config, log=lambda *_a: None):
        self.b = browser
        self.cfg = config
        self.sel = config["selectors"]
        self.log = log
        self._booted = False

    def boot(self):
        self.b.js(BOOTSTRAP)
        self._booted = True

    def _ensure(self):
        if not self._booted or not self.b.js("typeof window.__aipass === 'object'"):
            self.boot()

    def call(self, fn, *args, timeout=30):
        self._ensure()
        return self.b.js("window.__aipass.%s(%s)" % (fn, _js_args(*args)), timeout=timeout)

    # ------------------------------------------------------------ readiness
    def wait_ready(self, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self._ensure()
            if self.call("inputValue", self.sel["input"]) is not None:
                return True
            time.sleep(1.0)
        raise CDPError(
            "no chat input found on the page. Log in to the site in Brave first, "
            "or update selectors.input in config/models.json"
        )

    # -------------------------------------------------------- model picking
    def current_model(self):
        return self.call("currentModel", self.sel["model_picker"])

    def select_model(self, model_key):
        """Return (ok, detail). ok=False means the picker could not be driven."""
        spec = self.cfg["models"][model_key]
        needles = [spec["label"]] + list(spec.get("match", []))
        current = self.current_model() or ""
        if any(n.lower() in current.lower() for n in needles):
            return True, "already on %s" % (current or model_key)

        if not self.open_picker():
            return False, "model picker not found or it showed no options " \
                          "(selectors.model_picker / selectors.model_option)"
        chosen = self.call("chooseOption", self.sel["model_option"], needles)
        if chosen is None:
            options = self.call("listOptions", self.sel["model_option"]) or []
            self.close_picker()
            return False, "model %s not offered. Visible options: %s" % (
                spec["label"], ", ".join(options[:20]) or "(none)"
            )
        time.sleep(0.8)
        now = self.current_model() or ""
        if now and not any(n.lower() in now.lower() for n in needles):
            return False, "clicked %r but the picker still reads %r" % (chosen, now)
        return True, chosen

    def open_picker(self, tries=2):
        """Open the model dropdown. Pickers toggle, so verify options appeared."""
        for _ in range(tries):
            if self.call("listOptions", self.sel["model_option"]):
                return True
            if not self.call("openPicker", self.sel["model_picker"]):
                return False
            for _ in range(8):
                time.sleep(0.2)
                if self.call("listOptions", self.sel["model_option"]):
                    return True
        return False

    def close_picker(self):
        """Escape first, then toggle the picker if the list is still up."""
        self.b.key("Escape", "Escape", 27)
        time.sleep(0.3)
        if self.call("listOptions", self.sel["model_option"]):
            self.call("closePicker")
            time.sleep(0.2)
        if self.call("listOptions", self.sel["model_option"]):
            self.call("openPicker", self.sel["model_picker"])
            time.sleep(0.2)

    # -------------------------------------------------------------- asking
    def ask(self, prompt):
        """Send the prompt and return (answer_text, meta)."""
        ans_cfg = self.cfg["answer"]
        self.b.clear_events()
        before = self.call("snapshot", self.sel["assistant_message"])
        body_before = self.call("bodyText") or ""

        if not self.call("setInput", self.sel["input"], prompt):
            raise CDPError("could not focus the chat input (selectors.input)")
        time.sleep(0.3)
        if not self.call("clickSend", self.sel["send_button"]):
            self.b.press_enter()
        time.sleep(0.5)
        # If the box still holds the prompt, the send did not take.
        left = self.call("inputValue", self.sel["input"]) or ""
        if prompt[:40] and prompt[:40] in left:
            self.b.press_enter()

        answer, meta = self._wait_answer(before, ans_cfg)
        meta["body_before"] = body_before
        return answer, meta

    def _wait_answer(self, before, ans_cfg):
        started = time.time()
        first_deadline = started + ans_cfg["first_token_timeout_s"]
        hard_deadline = started + ans_cfg["max_wait_s"]
        stable_for = ans_cfg["stable_for_s"]
        poll = ans_cfg["poll_interval_s"]

        last_text = ""
        last_change = time.time()
        got_something = False

        while time.time() < hard_deadline:
            self.b.pump(poll)
            now = self.call("snapshot", self.sel["assistant_message"])
            text = self._delta(before, now)

            if text and text != last_text:
                last_text = text
                last_change = time.time()
                got_something = True

            if not got_something and time.time() > first_deadline:
                break

            if got_something and not self.call("streaming", self.sel["stop_button"]):
                if time.time() - last_change >= stable_for:
                    break

        meta = {
            "elapsed_s": round(time.time() - started, 1),
            "mode": before.get("mode"),
            "timed_out": time.time() >= hard_deadline,
            "empty": not got_something,
        }
        return last_text.strip(), meta

    @staticmethod
    def _tail(old, new):
        """The part of `new` that was not already in `old`."""
        if not old:
            return new
        if new.startswith(old):
            return new[len(old):]
        limit = min(len(old), len(new))
        i = 0
        while i < limit and old[i] == new[i]:
            i += 1
        return new[i:]

    @staticmethod
    def _delta(before, now):
        if now.get("mode") == "sel":
            if before.get("mode") == "sel" and now.get("count", 0) <= before.get("count", 0):
                # No new bubble yet; the last one may still be the old answer.
                if now.get("text") == before.get("text"):
                    return ""
            return now.get("text") or ""
        old = before.get("text") or ""
        new = now.get("text") or ""
        if new.startswith(old[:200]) and len(new) > len(old):
            return new[len(old):].strip()
        return new[len(old):].strip() if len(new) > len(old) else ""

    # -------------------------------------------------------- rate limiting
    def rate_limited(self, answer_text, meta=None):
        """Look only at content that appeared after this send.

        Scanning the whole page would re-flag every later model on the stale
        rate-limit notice still sitting in the transcript.
        """
        rl = self.cfg["rate_limit"]
        haystacks = [answer_text or ""]
        if meta and "body_before" in meta:
            try:
                haystacks.append(self._tail(meta["body_before"], self.call("bodyText") or ""))
            except CDPError:
                pass
        blob = "\n".join(haystacks).lower()
        for pattern in rl["text_patterns"]:
            if pattern.lower() in blob:
                return "page said: %r" % pattern
        for ev in self.b.drain_events("Network.responseReceived"):
            status = ((ev.get("params") or {}).get("response") or {}).get("status")
            if status in rl["http_status"]:
                url = ((ev.get("params") or {}).get("response") or {}).get("url", "")
                return "HTTP %s from %s" % (status, url[:120])
        return None

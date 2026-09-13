#!/usr/bin/env python3
"""Offline checks: classifier accuracy and cooldown bookkeeping. No browser."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CASES = [
    ("write a python function to parse a CSV", "code"),
    ("refactor this React component and fix the bug", "code"),
    ("เขียนฟังก์ชัน Python อ่านไฟล์ CSV", "code"),
    ("ช่วยแก้บั๊กในสคริปต์นี้ที", "code"),
    ("prove that sqrt(2) is irrational, step by step", "reasoning"),
    ("calculate the probability of drawing two aces", "reasoning"),
    ("พิสูจน์ว่า 2^n โตเร็วกว่า n^2", "reasoning"),
    ("ช่วยคำนวณดอกเบี้ยทบต้น 5 ปี", "reasoning"),
    ("แต่งแคปชั่นขายกาแฟสด 5 แบบ", "thai"),
    ("ช่วยเรียบเรียงจดหมายลาออกให้สุภาพ", "thai"),
    ("rewrite this as thai copy for facebook", "thai"),
    ("เมืองหลวงของญี่ปุ่นคืออะไร", "quick"),
    ("what is a monorepo", "quick"),
    ("ตอบสั้นๆ ว่า HTTP 418 คืออะไร", "quick"),
]


def main():
    os.environ.setdefault("AIPASS_ROUTER_HOME", tempfile.mkdtemp(prefix="aipass-selftest-"))
    import registry

    cfg = registry.load_config()
    failed = []
    for prompt, want in CASES:
        got, scores = registry.classify(prompt, cfg)
        mark = "ok  " if got == want else "FAIL"
        if got != want:
            failed.append((prompt, want, got, scores))
        print("%s %-46s -> %-9s (want %s)" % (mark, prompt[:46], got, want))

    print("\nclassifier: %d/%d" % (len(CASES) - len(failed), len(CASES)))

    # every chain must reference models that exist
    for task, route in cfg["routes"].items():
        for model in route["chain"]:
            assert model in cfg["models"], "route %s references unknown model %s" % (task, model)
    print("routes: all chains reference known models")

    # cooldown round-trip
    registry.clear_cooldown()
    assert registry.cooldowns() == {}
    registry.start_cooldown("typhoon-2", 15, reason="selftest")
    assert registry.is_cooling("typhoon-2"), "cooldown was not recorded"
    ready, cooling = registry.available_chain("thai", cfg)
    assert "typhoon-2" not in ready and cooling and cooling[0][0] == "typhoon-2"
    registry.clear_cooldown("typhoon-2")
    assert not registry.is_cooling("typhoon-2"), "cooldown was not cleared"
    print("cooldowns: park, skip in chain, and clear all work")

    if failed:
        print("\n%d classifier case(s) failed" % len(failed))
        return 1
    print("\nSELFTEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

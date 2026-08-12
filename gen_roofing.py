#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate roofing-intros.yaml content, with word-count + banned-word validation."""
import re, sys, io
import yaml

BANNED_PATTERNS = [
    r"\blicen[cs]ed?\b", r"\blicence\b", r"\blicense\b",
    r"\binsured\b",
    r"\b24/7\b", r"round the clock", r"round-the-clock",
    r"\bfamily[- ]owned\b",
    r"\bBBB\b",
    r"A\+ ?rated", r"\bstar rating", r"\bfive[- ]star", r"\b5[- ]star",
    r"free estimate",
    r"\bwarrant(y|ies)\b",
    r"\bcertifi", r"\baward",
    r"\d+\+?\s*roofs\b",
    r"\d+\s*years? of experience",
    r"peace of mind", r"state-of-the-art", r"state of the art",
    r"our team of experts", r"\bnestled\b", r"we pride ourselves",
    r"!",
]
BANNED_RE = re.compile("|".join(BANNED_PATTERNS), re.IGNORECASE)

def check_banned(items):
    bad = []
    for label, text in items:
        if BANNED_RE.search(text):
            bad.append((label, text))
    return bad

def wc(text):
    # strip token braces content doesn't matter, count words by whitespace
    return len(text.split())

def check_bracket_tokens(items):
    """Any clause with {nearby}/{county}/{zips_short} must be inside [[ ]]."""
    bad = []
    for label, text in items:
        # find all {token} occurrences not inside [[ ]]
        depth = 0
        i = 0
        n = len(text)
        stack = []
        # simple approach: remove [[ ... ]] spans, then check remaining text for the tokens
        stripped = re.sub(r"\[\[.*?\]\]", "", text, flags=re.DOTALL)
        for tok in ("{nearby}", "{county}", "{zips_short}"):
            if tok in stripped:
                bad.append((label, text, tok))
    return bad

if __name__ == "__main__":
    print("module loaded")

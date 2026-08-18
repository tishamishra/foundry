"""
Foundry — optional OpenAI bridge.

The Intel dashboard already hands you a ready prompt for each weak section. This
module lets the panel RUN that prompt for you: one API call, the CSV comes back,
and the existing importer parses and stores it. Nothing here knows about blocks
or pools — it only turns a prompt into text. Parsing and saving stay in prompts.py
and library.py, so the "Generate" button reuses the exact pipeline a paste does.

No third-party dependency: the call is a plain HTTPS POST via urllib, so the lean
Docker image needs nothing extra. The API key is read from the environment
(FOUNDRY_OPENAI_KEY) and never logged, never written to disk, never returned.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class AIError(RuntimeError):
    """A human-readable failure — safe to show in the panel (no key inside)."""


def api_key() -> str:
    return (os.environ.get("FOUNDRY_OPENAI_KEY")
            or os.environ.get("OPENAI_API_KEY") or "").strip()


def have_key() -> bool:
    return bool(api_key())


def generate(prompt: str, model: str | None = None,
             max_tokens: int = 4000, timeout: int = 90) -> str:
    """Send one prompt to the model and return its raw text reply.

    Raises AIError with a clear message on any failure — a missing key, a bad
    key, an exhausted quota, or a network problem — so the caller can flash it."""
    key = api_key()
    if not key:
        raise AIError("No OpenAI key is set. Add FOUNDRY_OPENAI_KEY in your "
                      "deployment's environment, then redeploy.")

    body = json.dumps({
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system",
             "content": "You output ONLY what the user's instructions ask for — "
                        "usually CSV. No preamble, no code fences, no commentary."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:                                  # noqa: BLE001
            pass
        if exc.code == 401:
            raise AIError("OpenAI rejected the key (401). Check FOUNDRY_OPENAI_KEY.") from None
        if exc.code == 429:
            raise AIError("OpenAI is rate-limiting or the account is out of "
                          "quota (429). Try again shortly or top up credits.") from None
        raise AIError(f"OpenAI error {exc.code}: {detail or 'request failed'}.") from None
    except urllib.error.URLError as exc:
        raise AIError(f"Could not reach OpenAI: {exc.reason}.") from None
    except Exception as exc:                               # noqa: BLE001
        raise AIError(f"Unexpected error calling OpenAI: {exc}.") from None

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise AIError("OpenAI returned an unexpected response shape.") from None

    return strip_fences(text or "")


def strip_fences(text: str) -> str:
    """Models sometimes wrap CSV in ```csv fences despite instructions — peel
    them so the importer sees clean rows."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t

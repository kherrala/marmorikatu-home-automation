"""Tests for the Whisper hallucination filter in claude_bridge.

Regression inputs are taken verbatim from the live kiosk debug log
during the 2026-05-27 loop incident where face-detection false-positive
kept the avatar listening, Whisper hallucinated stock phrases on the
ambient silence, and the responses fed back through the iPad speaker
into the mic.
"""
import importlib.util
import sys
from pathlib import Path


# Importing scripts/claude_bridge.py end-to-end pulls in MCP/anthropic/ollama;
# avoid the cost (and missing-dep churn) by loading the module source and
# extracting just the pure-function under test.
def _load_filter():
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "claude_bridge.py").read_text()
    # Slice from the helper's docstring header to its closing return.
    needle = "_WHISPER_STOCK_HALLUCINATIONS = ("
    start = src.index(needle)
    end = src.index("\n\nasync def transcribe_endpoint", start)
    snippet = src[start:end]
    mod = type(sys)("hallucination_filter_under_test")
    exec(snippet, mod.__dict__)
    return mod._is_whisper_hallucination


_is_hallucination = _load_filter()


# ── True positives (hallucinations from the live debug log) ─────────────────

def test_repeated_bigram_taalla_on():
    assert _is_hallucination("Täällä on. Täällä on.")


def test_long_trigram_chain():
    assert _is_hallucination(
        "Katsotaan, että se on katsotaan, "
        "että se on katsotaan, että se on katsotaan."
    )


def test_phrase_repeated_with_prefix():
    assert _is_hallucination(
        "Sitten on yksin. Täällä on hyvä. Täällä on hyvä. "
        "Täällä on hyvä. Täällä on hyvä."
    )


def test_full_sentence_repeated():
    assert _is_hallucination(
        "Täällä on nyt perus on vielä. Täällä on nyt perus on vielä."
    )


def test_short_repeat_trips_unique_ratio():
    assert _is_hallucination("Täällä on. Täällä on. Täällä on. Täällä on.")


def test_known_stock_phrase_tekstitys():
    assert _is_hallucination("Tekstitys: YLE 2026")


def test_known_stock_phrase_kiitos():
    assert _is_hallucination("Kiitos kun katsoit ohjelmaa")


def test_empty_text():
    assert _is_hallucination("")


def test_whitespace_only():
    assert _is_hallucination("   \n  \t  ")


# ── True negatives (must pass through) ──────────────────────────────────────

def test_real_short_command_joo():
    assert not _is_hallucination("Joo")


def test_real_short_command_lopeta():
    assert not _is_hallucination("Lopeta nyt")


def test_real_question():
    assert not _is_hallucination(
        "Mikä on keittiön lämpötila tällä hetkellä?"
    )


def test_real_long_sentence():
    assert not _is_hallucination(
        "Voitko sytyttää olohuoneen kattovalon ja kertoa miten lämmitys menee."
    )


def test_real_request_with_minor_repetition():
    # "ja" appears twice — natural Finnish, must NOT be classified as
    # hallucination.
    assert not _is_hallucination(
        "Sammuta keittiön valo ja olohuoneen valo ja sauna."
    )

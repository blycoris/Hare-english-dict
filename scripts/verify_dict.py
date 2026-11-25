#!/usr/bin/env python3
"""Verification suite for compiled Hare-english-dict outputs."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
DATA_DIR = ROOT / "data"
MANIFEST_PATH = ROOT / "upstream-manifest.json"
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z']*$")


def key_of(word: str) -> str:
    return word.lower().replace("'", "")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(262144)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_lexicon() -> dict[str, list[tuple[str, int, int, bool]]]:
    path = DIST_DIR / "EnglishLexicon.txt"
    assert path.exists(), f"Missing {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 55_000, f"Unexpectedly small lexicon: {len(lines)}"

    by_key: dict[str, list[tuple[str, int, int, bool]]] = {}
    prev_key = ""
    offensive_count = 0
    tier_counts = [0, 0, 0, 0]

    for lineno, line in enumerate(lines, 1):
        parts = line.split("\t")
        assert len(parts) in (3, 4), f"Line {lineno}: expected 3 or 4 columns, got {parts!r}"
        word, tier_s, weight_s = parts[0], parts[1], parts[2]
        flags = parts[3] if len(parts) == 4 else ""
        assert WORD_RE.match(word), f"Line {lineno}: invalid word {word!r}"
        tier = int(tier_s)
        weight = int(weight_s)
        assert 0 <= tier <= 3, f"Line {lineno}: invalid tier {tier}"
        assert 0 <= weight <= 255, f"Line {lineno}: invalid weight {weight}"
        assert flags in ("", "o"), f"Line {lineno}: invalid flags {flags!r}"
        avoid = "o" in flags
        if avoid:
            offensive_count += 1
        tier_counts[tier] += 1

        k = key_of(word)
        assert k.isascii() and k.islower(), f"Line {lineno}: non-lowercase ASCII key {k!r}"
        assert k >= prev_key, f"Line {lineno}: binary-search sort violated: {prev_key!r} > {k!r}"
        prev_key = k
        by_key.setdefault(k, []).append((word, tier, weight, avoid))

    # Single-letter invariant: ONLY "I" and "a"
    single_letter = [k for k in by_key if len(k) == 1]
    assert sorted(single_letter) == ["a", "i"], f"Unexpected single-letter keys: {single_letter}"
    assert [e[0] for e in by_key["i"]] == ["I"], f"Key 'i' must map only to ['I'], got {by_key['i']}"
    assert [e[0] for e in by_key["a"]] == ["a"], f"Key 'a' must map only to ['a'], got {by_key['a']}"

    # Contraction hygiene invariants
    expected_contractions = {
        "dont": "don't",
        "cant": "can't",
        "wont": "won't",
        "im": "I'm",
        "ive": "I've",
        "youre": "you're",
        "theyre": "they're",
        "youve": "you've",
        "weve": "we've",
        "didnt": "didn't",
        "doesnt": "doesn't",
        "isnt": "isn't",
        "arent": "aren't",
        "wasnt": "wasn't",
        "werent": "weren't",
        "hasnt": "hasn't",
        "havent": "haven't",
        "hadnt": "hadn't",
        "wouldnt": "wouldn't",
        "couldnt": "couldn't",
        "shouldnt": "shouldn't",
    }
    for k, expected in expected_contractions.items():
        words = [e[0] for e in by_key.get(k, [])]
        assert words == [expected], f"Contraction key {k!r}: expected {[expected]}, got {words}"

    # Ambiguous words that must stay un-forced
    assert {"its", "it's"}.issubset({e[0] for e in by_key["its"]})
    assert {"were", "we're"}.issubset({e[0] for e in by_key["were"]})
    assert {"ill", "I'll"}.issubset({e[0] for e in by_key["ill"]})
    assert {"id", "I'd"}.issubset({e[0] for e in by_key["id"]})

    # Non-words that must NOT appear in lexicon
    for nonword in ("helo", "teh", "thw", "becuase", "recieve", "pleasr", "tommorow", "alot", "ofthe", "inthe"):
        assert nonword not in by_key, f"Typo/non-word leaked into lexicon: {nonword}"

    # Modern supplement & proper brand checks
    for required in (
        "lol", "idk", "btw", "tbh", "omw", "emoji", "podcast", "selfie", "vlog",
        "gonna", "wanna", "cli", "ok", "okay", "iphone", "github", "chatgpt",
    ):
        assert required in by_key, f"Missing modern word key: {required}"
    assert any(e[0] == "iPhone" for e in by_key["iphone"])
    assert any(e[0] == "GitHub" for e in by_key["github"])

    # Offensive flagging checks
    assert offensive_count >= 500, f"Expected >= 500 offensive-flagged entries, got {offensive_count}"
    for profanity in ("fuck", "shit"):
        assert profanity in by_key, f"Expected exact-typed entry for {profanity}"
        assert all(e[3] for e in by_key[profanity]), f"{profanity} must have avoidAutocorrect ('o') flag"

    # Tier sanity checks
    assert 3_000 <= tier_counts[0] <= 12_000, f"Tier 0 count out of range: {tier_counts}"
    assert tier_counts[1] >= 5_000, f"Tier 1 count too low: {tier_counts}"

    print(
        f"[ok] EnglishLexicon.txt: {len(lines)} entries, {path.stat().st_size // 1024} KB, "
        f"tiers={tier_counts}, avoidAutocorrect={offensive_count}"
    )
    return by_key


def verify_followers() -> None:
    path = DIST_DIR / "EnglishFollowers.txt"
    assert path.exists(), f"Missing {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 7_000, f"Too few follower keys: {len(lines)}"

    prev_key = ""
    table: dict[str, list[str]] = {}
    for lineno, line in enumerate(lines, 1):
        parts = line.split("\t")
        assert len(parts) == 2, f"Followers line {lineno}: invalid format {line!r}"
        k, rest = parts
        assert k >= prev_key, f"Followers line {lineno}: unsorted keys {prev_key!r} > {k!r}"
        prev_key = k
        words = rest.split(" ")
        assert 1 <= len(words) <= 6, f"Followers line {lineno}: expected 1..6 followers, got {words}"
        table[k] = words

    for lhs, rhs in (("of", "the"), ("in", "the"), ("going", "to"), ("thank", "you"), ("a", "lot")):
        assert rhs in table.get(lhs, []), f"Expected bigram {lhs!r} -> {rhs!r}, got {table.get(lhs)}"
    assert "I" in table.get("and", []) or "I" in table.get("but", [])

    print(f"[ok] EnglishFollowers.txt: {len(lines)} keys, {path.stat().st_size // 1024} KB")


def verify_rime_zip() -> None:
    zip_path = DIST_DIR / "hare-english-rime.zip"
    assert zip_path.exists(), f"Missing {zip_path}"
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "Payload/hare_eng.dict.yaml" in names, f"Missing dict.yaml in zip: {names}"
        assert "Payload/hare_eng.schema.yaml" in names, f"Missing schema.yaml in zip: {names}"
        dict_text = zf.read("Payload/hare_eng.dict.yaml").decode("utf-8")
        assert "name: hare_eng" in dict_text
        assert "iPhone\tiphone\t" in dict_text
        assert "GitHub\tgithub\t" in dict_text
        assert "don't\tdont\t" in dict_text
    print(f"[ok] hare-english-rime.zip: {zip_path.stat().st_size // 1024} KB")


def verify_manifest() -> None:
    assert MANIFEST_PATH.exists(), f"Missing {MANIFEST_PATH}"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for name in ("EnglishLexicon.txt", "EnglishFollowers.txt", "hare-english-rime.zip"):
        expected_sha = manifest["outputs"][name]["sha256"]
        actual_sha = sha256_file(DIST_DIR / name)
        assert expected_sha == actual_sha, f"Digest mismatch for {name}: {expected_sha} != {actual_sha}"
    print(f"[ok] upstream-manifest.json: version={manifest['version']}, digests verified")


def main() -> int:
    verify_lexicon()
    verify_followers()
    verify_rime_zip()
    verify_manifest()
    print("All Hare-english-dict verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
FOLLOWER_KEY_RE = re.compile(r"^[a-z]+(?: [a-z]+)?$")
FOLLOWER_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z']*(?:_[A-Za-z][A-Za-z']*)*$")
VALID_FLAGS = {"", "o", "u", "b", "uo", "bo"}


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


def verify_lexicon() -> dict[str, list[tuple[str, int, int, str]]]:
    path = DIST_DIR / "EnglishLexicon.txt"
    assert path.exists(), f"Missing {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 55_000, f"Unexpectedly small lexicon: {len(lines)}"

    by_key: dict[str, list[tuple[str, int, int, str]]] = {}
    by_word: dict[str, tuple[int, int, str]] = {}
    prev_key = ""
    offensive_count = 0
    us_count = 0
    uk_count = 0
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
        assert flags in VALID_FLAGS, f"Line {lineno}: invalid flags {flags!r}"
        assert not ("u" in flags and "b" in flags), f"Line {lineno}: conflicting dialect flags {flags!r}"
        if "o" in flags:
            offensive_count += 1
        if "u" in flags:
            us_count += 1
        if "b" in flags:
            uk_count += 1
        tier_counts[tier] += 1

        k = key_of(word)
        assert k.isascii() and k.islower(), f"Line {lineno}: non-lowercase ASCII key {k!r}"
        assert k >= prev_key, f"Line {lineno}: binary-search sort violated: {prev_key!r} > {k!r}"
        prev_key = k
        by_key.setdefault(k, []).append((word, tier, weight, flags))
        by_word[word] = (tier, weight, flags)

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
        assert all("o" in e[3] for e in by_key[profanity]), f"{profanity} must have avoidAutocorrect ('o') flag"

    # Regional dialect flag checks (US "u" vs UK "b" vs universal unrestricted)
    assert us_count >= 150, f"Too few US dialect entries: {us_count}"
    assert uk_count >= 150, f"Too few UK dialect entries: {uk_count}"
    for us_word, uk_word in (
        ("color", "colour"),
        ("favorite", "favourite"),
        ("center", "centre"),
        ("theater", "theatre"),
        ("organize", "organise"),
        ("defense", "defence"),
        ("traveling", "travelling"),
        ("canceled", "cancelled"),
        ("gray", "grey"),
        ("aluminum", "aluminium"),
        ("mom", "mum"),
        ("math", "maths"),
        ("pajamas", "pyjamas"),
        ("jewelry", "jewellery"),
        ("curb", "kerb"),
    ):
        assert us_word in by_word and "u" in by_word[us_word][2], f"Expected US 'u' flag on {us_word!r}: {by_word.get(us_word)}"
        assert uk_word in by_word and "b" in by_word[uk_word][2], f"Expected UK 'b' flag on {uk_word!r}: {by_word.get(uk_word)}"

    # UK-only spellings whose US counterparts are universal everyday words (must NOT restrict US counterpart)
    for uk_only, universal in (
        ("tyre", "tire"),
        ("cheque", "check"),
        ("programme", "program"),
        ("practise", "practice"),
        ("licence", "license"),
    ):
        assert uk_only in by_word and "b" in by_word[uk_only][2], f"Expected UK 'b' flag on {uk_only!r}: {by_word.get(uk_only)}"
        assert universal in by_word and "u" not in by_word[universal][2] and "b" not in by_word[universal][2], (
            f"Universal word {universal!r} must NOT have dialect flag: {by_word.get(universal)}"
        )
    for universal in ("story", "flat", "lift", "laboratory", "literature", "analysis", "organism", "optimism", "honorary", "humorous"):
        assert universal in by_word and "u" not in by_word[universal][2] and "b" not in by_word[universal][2], (
            f"Universal word {universal!r} must NOT have dialect flag: {by_word.get(universal)}"
        )

    # Key UK everyday words must retain tier 0/1 base strength
    for uk_core in ("colour", "centre", "organise", "favourite", "travelling", "cancelled", "grey", "mum", "maths", "cheque", "programme", "theatre"):
        assert uk_core in by_word and by_word[uk_core][0] <= 1, (
            f"Expected UK word {uk_core!r} to be tier 0/1, got {by_word.get(uk_core)}"
        )

    # Tier sanity checks
    assert 3_000 <= tier_counts[0] <= 12_000, f"Tier 0 count out of range: {tier_counts}"
    assert tier_counts[1] >= 5_000, f"Tier 1 count too low: {tier_counts}"

    print(
        f"[ok] EnglishLexicon.txt: {len(lines)} entries, {path.stat().st_size // 1024} KB, "
        f"tiers={tier_counts}, avoidAutocorrect={offensive_count}, us={us_count}, uk={uk_count}"
    )
    return by_key


def verify_followers() -> None:
    path = DIST_DIR / "EnglishFollowers.txt"
    assert path.exists(), f"Missing {path}"
    size_bytes = path.stat().st_size
    assert size_bytes < 300 * 1024, f"EnglishFollowers.txt too large: {size_bytes} bytes (limit 300 KB)"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 7_000, f"Too few follower keys: {len(lines)}"

    prev_key = ""
    table: dict[str, list[str]] = {}
    two_word_count = 0
    phrase_follower_count = 0
    for lineno, line in enumerate(lines, 1):
        parts = line.split("\t")
        assert len(parts) == 2, f"Followers line {lineno}: invalid format {line!r}"
        k, rest = parts
        assert FOLLOWER_KEY_RE.match(k), f"Followers line {lineno}: invalid key {k!r}"
        assert k > prev_key, f"Followers line {lineno}: unsorted/duplicate keys {prev_key!r} >= {k!r}"
        prev_key = k
        if " " in k:
            two_word_count += 1
        words = rest.split(" ")
        assert 1 <= len(words) <= 6, f"Followers line {lineno}: expected 1..6 followers, got {words}"
        for w in words:
            assert FOLLOWER_TOKEN_RE.match(w), f"Followers line {lineno}: invalid follower token {w!r}"
            if "_" in w:
                phrase_follower_count += 1
        table[k] = words

    assert two_word_count >= 1_000, f"Expected >= 1000 two-word context keys, got {two_word_count}"
    assert phrase_follower_count >= 10, f"Expected >= 10 multi-word '_' phrase followers, got {phrase_follower_count}"

    # Single-word bigram & phrase-follower checks
    for lhs, rhs in (
        ("of", "the"),
        ("in", "the"),
        ("going", "to"),
        ("thank", "you"),
        ("a", "lot"),
        ("let", "me_know"),
        ("on", "my_way"),
        ("thank", "you_so_much"),
        ("see", "you_soon"),
        ("looking", "forward_to"),
        ("by", "the_way"),
        ("as", "soon_as"),
    ):
        assert rhs in table.get(lhs, []), f"Expected follower {lhs!r} -> {rhs!r}, got {table.get(lhs)}"
    assert "I" in table.get("and", []) or "I" in table.get("but", [])

    # Two-word context trigram checks
    for ctx, rhs in (
        ("on my", "way"),
        ("let me", "know"),
        ("thank you", "so_much"),
        ("looking forward", "to"),
        ("as soon", "as"),
        ("be right", "back"),
        ("talk to", "you"),
        ("have a", "good"),
        ("how are", "you"),
        ("what do", "you"),
        ("i dont", "know"),
        ("i cant", "wait"),
        ("by the", "way"),
        ("at the", "moment"),
        ("see you", "soon"),
        ("make sure", "you"),
        ("going to", "be"),
        ("want to", "be"),
        ("need to", "be"),
    ):
        assert rhs in table.get(ctx, []), f"Expected trigram {ctx!r} -> {rhs!r}, got {table.get(ctx)}"

    print(
        f"[ok] EnglishFollowers.txt: {len(lines)} keys ({two_word_count} 2-word, "
        f"{phrase_follower_count} phrase tokens), {size_bytes // 1024} KB"
    )


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

#!/usr/bin/env python3
"""Deterministic English dictionary compiler for Hare IME & Rime.

Fetches permissive commercial-safe upstream English wordlists from Google AOSP
LatinIME (Apache-2.0), merges with bundled SCOWL 2020 baseline, Tatoeba
everyday followers (CC BY 2.0 FR), and curated modern mobile/tech supplement
(CC0-1.0), and compiles:

  - dist/EnglishLexicon.txt          (word<TAB>tier<TAB>weight[<TAB>flags])
  - dist/EnglishFollowers.txt        (key<TAB>follower1 follower2 ...)
  - dist/Payload/hare_eng.dict.yaml  (Rime English dictionary)
  - dist/Payload/hare_eng.schema.yaml
  - dist/hare-english-rime.zip       (deterministic Rime package)
  - upstream-manifest.json           (digests, commit info, counts)

Standard library only (optional `wordfreq` enrichment if installed).
"""
from __future__ import annotations

import argparse
import base64
import bz2
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import zipfile
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
PAYLOAD_DIR = DIST_DIR / "Payload"
MANIFEST_PATH = ROOT / "upstream-manifest.json"
DEFAULT_TATOEBA_BZ2 = pathlib.Path.home() / "HareML" / "english-context" / "eng_sentences.tsv.bz2"

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z']*$")
FOLLOWER_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z']*(?:_[A-Za-z][A-Za-z']*)*$")
SENTENCE_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

AOSP_SOURCES = {
    "en_US": [
        (
            "gitiles_b64_gz",
            "https://android.googlesource.com/platform/packages/inputmethods/LatinIME/+/refs/heads/main/dictionaries/en_US_wordlist.combined.gz?format=TEXT",
        ),
        (
            "raw_gz",
            "https://raw.githubusercontent.com/LineageOS/android_packages_inputmethods_LatinIME/lineage-21.0/dictionaries/en_US_wordlist.combined.gz",
        ),
        (
            "raw_text",
            "https://codeberg.org/Helium314/aosp-dictionaries/raw/branch/main/wordlists/main_en.combined",
        ),
    ],
    "en_GB": [
        (
            "gitiles_b64_gz",
            "https://android.googlesource.com/platform/packages/inputmethods/LatinIME/+/refs/heads/main/dictionaries/en_GB_wordlist.combined.gz?format=TEXT",
        ),
        (
            "raw_gz",
            "https://raw.githubusercontent.com/LineageOS/android_packages_inputmethods_LatinIME/lineage-21.0/dictionaries/en_GB_wordlist.combined.gz",
        ),
    ],
}

AOSP_LOG_URL = (
    "https://android.googlesource.com/platform/packages/inputmethods/LatinIME/"
    "+log/refs/heads/main/dictionaries?format=JSON"
)

# Keys whose ONLY lexicon spelling must be the apostrophized contraction.
# Matches iOS / AOSP shortcut behavior (dont -> don't, cant -> can't, wont -> won't).
CONTRACTION_ONLY_KEYS: Dict[str, str] = {
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

# Regional spelling classification ("b" = UK-preferred, "u" = US-preferred).
# Universal everyday words in BOTH US and UK are NEVER restricted.
UNIVERSAL_DIALECT_EXCLUSIONS: Set[str] = {
    # Common everyday universal meanings in both US and UK
    "tire", "tires", "tired", "tiring",
    "check", "checks", "checked", "checking",
    "program", "programs", "programmed", "programming",
    "story", "stories",
    "flat", "flats",
    "lift", "lifts",
    "practice", "practices", "practiced", "practicing",
    "license", "licenses", "licensed", "licensing",
    "draft", "drafts", "drafted", "drafting",
    # Morphologically distinct universal words that happen to share a stem prefix
    "honorary", "honorific",
    "humorous", "humorously", "humorist", "humorists", "humoral",
    "vigorous", "vigorously",
    "valorous",
    "odorous", "odoriferous",
    "laboratory", "laboratories", "laborious", "laboriously",
    "literal", "literally", "literary", "literature", "literate", "literacy",
    "organism", "organisms", "organist", "organists",
    "realism", "realist", "realists", "realistic", "realistically",
    "analysis", "analyses", "analyst", "analysts",
    "paralysis",
    "apologist", "apologists",
    "criticism", "criticisms",
    "emphasis",
    "optimism", "optimist", "optimists", "optimistic", "optimistically",
    "analogy", "analogies", "analogous",
    "greyhound", "greyhounds",
    "mummy", "mummies",
    "cancellation", "cancellations",
    "colorado", "coloratura", "armorial",
    "sombrero", "sombreros",
    "programmer", "programmers", "programmer's",
}

UK_SAFE_PREFIXES: Tuple[str, ...] = (
    "colour", "favour", "flavour", "honour", "humour", "labour", "neighbour",
    "behaviour", "rumour", "harbour", "armour", "endeavour", "saviour",
    "splendour", "vigour", "odour", "tumour", "valour", "parlour",
    "centre", "theatre", "metre", "litre", "fibre", "calibre", "sombre",
    "recognis", "summaris", "prioritis", "customis",
    "defence", "offence", "pretence", "licence", "practise",
    "catalogue", "dialogue", "analogue", "programme",
    "traveller", "sceptic", "aeroplane", "artefact", "doughnut",
    "plough", "draught", "cheque", "tyre", "kerb",
)

UK_EXACT_WORDS: Set[str] = {
    "organise", "organises", "organised", "organising", "organiser", "organisers",
    "organisation", "organisations", "organisational",
    "realise", "realises", "realised", "realising", "realisation", "realisations",
    "analyse", "analysed", "analysing", "analyser", "analysers",
    "paralyse", "paralyses", "paralysed", "paralysing",
    "apologise", "apologises", "apologised", "apologising",
    "criticise", "criticises", "criticised", "criticising",
    "emphasise", "emphasises", "emphasised", "emphasising",
    "optimise", "optimises", "optimised", "optimising",
    "optimisation", "optimisations", "optimiser", "optimisers",
    "travelled", "travelling",
    "cancelled", "cancelling",
    "labelled", "labelling",
    "modelled", "modelling",
    "signalled", "signalling",
    "jewellery",
    "grey", "greys", "greyed", "greying", "greyish", "greyness",
    "aluminium",
    "cosy", "cosier", "cosiest", "cosily", "cosiness",
    "fulfil", "fulfils", "fulfilment",
    "skilful", "skilfully", "skilfulness",
    "enrol", "enrols", "enrolment", "enrolments",
    "instalment", "instalments",
    "mum", "mums",
    "maths",
    "pyjamas",
}

US_SAFE_PREFIXES: Tuple[str, ...] = (
    "color", "favor", "flavor", "neighbor", "behavior", "rumor", "harbor",
    "armor", "endeavor", "savior", "splendor", "parlor",
    "center", "theater", "fiber", "caliber", "somber",
    "organiz", "realiz", "recogniz", "analyz", "paralyz", "apologiz",
    "criticiz", "emphasiz", "summariz", "optimiz", "prioritiz", "customiz",
    "defense", "offense", "pretense",
    "traveler", "skeptic", "airplane", "artifact", "donut", "plow", "curb",
)

US_EXACT_WORDS: Set[str] = {
    "honor", "honors", "honored", "honoring", "honorable", "honorably",
    "humor", "humors", "humored", "humoring", "humorless",
    "labor", "labors", "labored", "laboring", "laborer", "laborers",
    "vigor", "vigors",
    "odor", "odors", "odorless",
    "tumor", "tumors",
    "valor",
    "meter", "meters", "metered", "metering",
    "liter", "liters",
    "catalog", "catalogs", "cataloged", "cataloging",
    "dialog", "dialogs",
    "analog", "analogs",
    "traveled", "traveling",
    "canceled", "canceling",
    "labeled", "labeling",
    "modeled", "modeling",
    "signaled", "signaling",
    "jewelry",
    "gray", "grays", "grayed", "graying", "grayish", "grayness",
    "aluminum",
    "cozy", "cozier", "coziest", "cozily", "coziness",
    "fulfill", "fulfills", "fulfillment",
    "skillful", "skillfully", "skillfulness",
    "enroll", "enrolls", "enrollment", "enrollments",
    "installment", "installments",
    "mom", "moms", "mommy", "mommies",
    "math",
    "pajamas",
}

# Everyday UK spellings whose AOSP en_GB weight lags their everyday UK tier.
UK_EVERYDAY_WEIGHT_FLOORS: Dict[str, int] = {
    "mum": 158,
    "mums": 148,
    "cheque": 156,
    "cheques": 146,
    "tyre": 154,
    "tyres": 148,
}

# Morphological suffix swap tables for automatic AOSP US/UK dialect detection.
UK_TO_US_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("isations", "izations"),
    ("isation", "ization"),
    ("isers", "izers"),
    ("iser", "izer"),
    ("ising", "izing"),
    ("ised", "ized"),
    ("ises", "izes"),
    ("ise", "ize"),
    ("ysing", "yzing"),
    ("ysed", "yzed"),
    ("yses", "yzes"),
    ("yse", "yze"),
    ("ourable", "orable"),
    ("ourably", "orably"),
    ("ouring", "oring"),
    ("oured", "ored"),
    ("ours", "ors"),
    ("our", "or"),
    ("tres", "ters"),
    ("tre", "ter"),
    ("bres", "bers"),
    ("bre", "ber"),
)

US_TO_UK_SUFFIXES: Tuple[Tuple[str, str], ...] = tuple((us, uk) for uk, us in UK_TO_US_SUFFIXES)


def classify_dialect(
    word: str,
    aosp_us_freq: Dict[str, int],
    aosp_gb_freq: Dict[str, int],
) -> str:
    """Return 'b' (UK-preferred), 'u' (US-preferred), or '' (neutral)."""
    low = word.lower()
    if word != low:
        return ""
    if low in UNIVERSAL_DIALECT_EXCLUSIONS:
        return ""
    # Check UK exact/prefix first (e.g. catalogue* before catalog*, dialogue* before dialog*).
    if low in UK_EXACT_WORDS or low.startswith(UK_SAFE_PREFIXES):
        return "b"
    if low in US_EXACT_WORDS or low.startswith(US_SAFE_PREFIXES):
        return "u"

    # Automatic AOSP morphological detection for lowercase words.
    f_us = aosp_us_freq.get(low, 0)
    f_gb = aosp_gb_freq.get(low, 0)
    if f_gb >= 95 and f_us == 0:
        for uk_suf, us_suf in UK_TO_US_SUFFIXES:
            if low.endswith(uk_suf) and len(low) > len(uk_suf) + 2:
                counterpart = low[: -len(uk_suf)] + us_suf
                if aosp_us_freq.get(counterpart, 0) >= 70:
                    return "b"
    if f_us >= 95 and f_gb == 0:
        for us_suf, uk_suf in US_TO_UK_SUFFIXES:
            if low.endswith(us_suf) and len(low) > len(us_suf) + 2:
                counterpart = low[: -len(us_suf)] + uk_suf
                if aosp_gb_freq.get(counterpart, 0) >= 70:
                    return "u"
    return ""


# Curated high-frequency conversational bigrams (CC0-1.0) merged ahead of / with
# Tatoeba and AOSP bigrams to ensure everyday mobile pairs and multi-word phrase
# followers (`_`-joined) are always strong.
CURATED_BIGRAMS: Dict[str, List[str]] = {
    "a": ["lot", "good", "little", "few", "great", "bit"],
    "able": ["to", "do"],
    "about": ["it", "the", "that", "to", "you", "this"],
    "all": ["the", "of", "good", "day", "right", "about"],
    "and": ["I", "the", "then", "you", "it", "we"],
    "are": ["you", "we", "they", "the", "not", "going"],
    "as": ["a", "well", "soon", "soon_as", "the", "far"],
    "at": ["the", "least", "all", "home", "work", "first"],
    "be": ["there", "able", "right_back", "the", "a", "back"],
    "because": ["I", "of", "it", "the", "you", "they"],
    "btw": ["I", "do", "are", "is", "the", "we"],
    "but": ["I", "it", "the", "you", "we", "that"],
    "by": ["the", "the_way", "way", "far", "now", "then"],
    "can": ["you", "I", "we", "be", "do", "get"],
    "cant": ["wait", "believe", "do", "make", "find", "stop"],
    "could": ["you", "be", "have", "not", "do", "get"],
    "did": ["you", "not", "it", "the", "I", "that"],
    "didnt": ["know", "have", "want", "get", "see", "mean"],
    "do": ["you", "not", "it", "that", "the", "this"],
    "dont": ["know", "think", "want", "have", "worry", "mind"],
    "for": ["the", "you", "a", "me", "sure", "now"],
    "from": ["the", "my", "here", "home", "now", "this"],
    "get": ["it", "the", "a", "to", "back", "some"],
    "going": ["to", "on", "out", "home", "back", "through"],
    "gonna": ["be", "go", "get", "do", "have", "take"],
    "good": ["morning", "night", "idea", "luck", "job", "time"],
    "gotta": ["go", "be", "get", "do", "run", "say"],
    "happy": ["birthday", "to", "new", "for", "with", "about"],
    "have": ["a", "to", "you", "been", "the", "some"],
    "he": ["is", "was", "has", "said", "will", "can"],
    "hope": ["you", "so", "it", "to", "that", "everything"],
    "how": ["are", "much", "about", "many", "long", "is"],
    "i": ["am", "have", "think", "know", "can", "will"],
    "id": ["like", "love", "rather", "be", "say", "prefer"],
    "idk": ["what", "if", "why", "how", "where", "about"],
    "if": ["you", "I", "it", "we", "they", "the"],
    "ill": ["be", "let", "see", "get", "send", "check"],
    "im": ["not", "going", "sorry", "sure", "good", "on"],
    "in": ["the", "a", "my", "this", "front", "fact"],
    "is": ["a", "the", "it", "not", "that", "this"],
    "it": ["is", "was", "will", "would", "looks", "seems"],
    "its": ["a", "not", "the", "so", "been", "just"],
    "ive": ["been", "got", "never", "had", "seen", "always"],
    "just": ["a", "to", "wanted", "got", "like", "in"],
    "know": ["what", "if", "how", "that", "where", "about"],
    "let": ["me", "me_know", "us", "you", "him", "her"],
    "lets": ["go", "do", "see", "get", "meet", "talk"],
    "like": ["to", "a", "the", "it", "that", "this"],
    "lmk": ["if", "when", "what", "how", "once"],
    "lol": ["I", "that", "yeah", "no", "it", "you"],
    "look": ["at", "like", "for", "forward", "up", "good"],
    "looking": ["for", "forward", "forward_to", "at", "good", "like"],
    "make": ["sure", "it", "a", "sense", "the", "you"],
    "need": ["to", "a", "some", "the", "more", "help"],
    "no": ["problem", "worries", "way", "one", "need", "thanks"],
    "not": ["sure", "a", "really", "going", "at", "the"],
    "of": ["the", "course", "my", "a", "us", "them"],
    "ok": ["thanks", "sounds", "cool", "great", "got", "I"],
    "okay": ["thanks", "sounds", "great", "cool", "got", "I"],
    "on": ["the", "my", "my_way", "your", "a", "this"],
    "one": ["of", "more", "day", "thing", "second", "minute"],
    "out": ["of", "the", "there", "here", "for", "to"],
    "please": ["let", "send", "do", "check", "don't", "call"],
    "right": ["now", "away", "here", "there", "back", "on"],
    "see": ["you", "you_soon", "if", "what", "the", "it"],
    "she": ["is", "was", "has", "said", "will", "can"],
    "should": ["be", "I", "we", "have", "not", "do"],
    "so": ["much", "I", "good", "far", "many", "sorry"],
    "sounds": ["good", "great", "like", "awesome", "amazing"],
    "take": ["a", "care", "the", "it", "your", "some"],
    "tbh": ["I", "it", "that", "the", "you", "we"],
    "thank": ["you", "you_so_much", "goodness", "god", "him", "her"],
    "thanks": ["for", "a", "so", "again", "man", "everyone"],
    "that": ["is", "was", "would", "I", "the", "you"],
    "thats": ["great", "a", "good", "true", "why", "awesome"],
    "the": ["same", "best", "first", "way", "other", "new"],
    "there": ["is", "are", "was", "were", "will", "would"],
    "they": ["are", "were", "have", "will", "can", "don't"],
    "theyre": ["not", "going", "all", "so", "in", "here"],
    "think": ["I", "it", "so", "about", "that", "you"],
    "this": ["is", "one", "week", "morning", "weekend", "time"],
    "to": ["the", "be", "do", "get", "go", "see"],
    "trying": ["to", "out", "my", "the", "it"],
    "used": ["to", "for", "in", "by", "the", "it"],
    "want": ["to", "a", "the", "you", "some", "me"],
    "wanna": ["go", "do", "get", "see", "be", "hang"],
    "we": ["are", "can", "have", "will", "should", "need"],
    "were": ["going", "not", "you", "in", "at", "just"],
    "what": ["is", "are", "do", "about", "a", "time"],
    "whats": ["up", "the", "going", "your", "wrong", "new"],
    "when": ["you", "I", "we", "it", "the", "are"],
    "where": ["are", "is", "do", "did", "you", "the"],
    "who": ["is", "are", "was", "knows", "else", "wants"],
    "why": ["not", "do", "is", "are", "did", "would"],
    "will": ["be", "you", "have", "do", "get", "let"],
    "with": ["the", "you", "a", "me", "my", "it"],
    "wont": ["be", "have", "work", "let", "do", "take"],
    "would": ["be", "you", "like", "have", "love", "not"],
    "you": ["are", "can", "have", "know", "want", "think"],
    "youre": ["welcome", "the", "so", "not", "right", "going"],
    "your": ["time", "help", "name", "email", "message", "day"],
}

# Curated high-signal two-word context keys (CC0-1.0) with optional `_`-joined
# multi-word phrase followers for next-word prediction.
CURATED_TRIGRAMS: Dict[str, List[str]] = {
    "on my": ["way", "own", "phone", "mind"],
    "let me": ["know", "see", "check", "think"],
    "thank you": ["so_much", "for", "very_much"],
    "looking forward": ["to", "to_it", "to_seeing"],
    "as soon": ["as", "as_possible"],
    "be right": ["back", "there"],
    "talk to": ["you", "you_soon", "you_later"],
    "have a": ["good", "great", "nice"],
    "how are": ["you", "you_doing", "things"],
    "what do": ["you", "you_think", "you_mean"],
    "i dont": ["know", "think", "have", "want"],
    "i cant": ["wait", "believe", "make"],
    "by the": ["way", "time"],
    "at the": ["moment", "same_time", "end"],
    "in the": ["morning", "future", "meantime"],
    "a lot": ["of", "more", "better"],
    "kind of": ["like", "a"],
    "out of": ["the", "office", "stock"],
    "see you": ["soon", "tomorrow", "later"],
    "take care": ["of"],
    "make sure": ["you", "to", "that"],
    "would like": ["to"],
    "going to": ["be", "the", "have"],
    "want to": ["be", "go", "do", "see"],
    "need to": ["be", "do", "go", "get"],
    "able to": ["get", "make", "do", "see"],
    "supposed to": ["be", "do"],
}


def lexicon_key(word: str) -> str:
    return word.lower().replace("'", "")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(262144)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def calibrate_aosp_freq(freq: int) -> int:
    """Calibrate AOSP raw frequency (1..222) into continuous weight (10..255).

    Preserves strict monotonicity while aligning tier cutoffs with everyday
    vocabulary sizes:
      - weight >= 185 (tier 0): ~5,000-7,000 top everyday words (AOSP f >= 115)
      - 145 <= weight < 185 (tier 1): ~10,000-14,000 common words (AOSP f 95..114)
      - 95 <= weight < 145 (tier 2): AOSP f 60..94
      - weight < 95 (tier 3): AOSP f < 60
    """
    f = max(1, min(222, freq))
    if f >= 200:
        w = 242 + round((f - 200) * 13 / 22)
    elif f >= 150:
        w = 214 + round((f - 150) * 28 / 50)
    elif f >= 115:
        w = 185 + round((f - 115) * 29 / 35)
    elif f >= 95:
        w = 145 + round((f - 95) * 40 / 20)
    elif f >= 60:
        w = 95 + round((f - 60) * 50 / 35)
    else:
        w = 30 + round(f * 65 / 60)
    return max(10, min(255, int(w)))


def weight_to_tier(weight: int) -> int:
    if weight >= 185:
        return 0
    if weight >= 145:
        return 1
    if weight >= 95:
        return 2
    return 3


def scowl_fallback_weight(tier: int, word: str) -> int:
    """Calibrated fallback weight for SCOWL words absent from AOSP.

    Shorter, cleaner base words rank slightly ahead of obscure long derivatives
    within the same SCOWL level.
    """
    length_penalty = min(14, max(0, len(word) - 4))
    apostrophe_penalty = 6 if "'" in word else 0
    if tier <= 0:
        base = 178
        floor = 152
    elif tier == 1:
        base = 138
        floor = 112
    elif tier == 2:
        base = 104
        floor = 78
    else:
        base = 72
        floor = 40
    return max(floor, base - length_penalty - apostrophe_penalty)


def fetch_url_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HareEnglishDictBuilder/1.0 (+https://github.com/hare-ime/Hare-english-dict)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_aosp_wordlist(
    locale: str, cache_dir: pathlib.Path, offline: bool
) -> Tuple[str, str, str]:
    """Return (text, source_url, sha256_of_text) for locale ('en_US' or 'en_GB')."""
    cache_path = cache_dir / f"{locale}_wordlist.combined"
    meta_path = cache_dir / f"{locale}_wordlist.meta.json"
    if offline:
        if not cache_path.exists():
            raise FileNotFoundError(f"Offline cache missing: {cache_path}")
        text = cache_path.read_text(encoding="utf-8")
        source_url = AOSP_SOURCES[locale][0][1]
        if meta_path.exists():
            source_url = json.loads(meta_path.read_text(encoding="utf-8")).get("url", source_url)
        return text, source_url, sha256_bytes(text.encode("utf-8"))

    errors = []
    for mode, url in AOSP_SOURCES[locale]:
        try:
            raw = fetch_url_bytes(url)
            if mode == "gitiles_b64_gz":
                text = gzip.decompress(base64.b64decode(raw)).decode("utf-8", errors="replace")
            elif mode == "raw_gz":
                text = gzip.decompress(raw).decode("utf-8", errors="replace")
            else:
                text = raw.decode("utf-8", errors="replace")
            if not text.startswith("dictionary="):
                raise ValueError(f"Unexpected header from {url}: {text[:80]!r}")
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
            digest = sha256_bytes(text.encode("utf-8"))
            meta_path.write_text(json.dumps({"url": url, "sha256": digest}, indent=2) + "\n", encoding="utf-8")
            return text, url, digest
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    if cache_path.exists():
        sys.stderr.write(f"[warn] Live fetch failed for {locale}, falling back to cache: {errors}\n")
        text = cache_path.read_text(encoding="utf-8")
        return text, AOSP_SOURCES[locale][0][1], sha256_bytes(text.encode("utf-8"))
    raise RuntimeError(f"Failed to fetch AOSP {locale} wordlist: {errors}")


def fetch_aosp_commit_sha(offline: bool) -> Optional[str]:
    if offline:
        return None
    try:
        raw = fetch_url_bytes(AOSP_LOG_URL, timeout=15).decode("utf-8", errors="replace")
        if raw.startswith(")]}'"):
            raw = raw.split("\n", 1)[1]
        payload = json.loads(raw)
        log = payload.get("log") or []
        if log and isinstance(log[0], dict):
            return log[0].get("commit")
    except Exception:
        return None
    return None


def parse_aosp_combined(text: str) -> Tuple[
    Dict[str, str],
    Dict[str, Tuple[int, bool, bool, str]],
    Dict[str, List[Tuple[str, int]]],
]:
    """Parse AOSP `.combined` format.

    Returns:
      - header metadata dict
      - unigrams: word -> (freq, possibly_offensive, not_a_word, flags)
      - bigrams: key(word1) -> list of (word2, bigram_freq)
    """
    lines = text.splitlines()
    header: Dict[str, str] = {}
    if lines and lines[0].startswith("dictionary="):
        for part in lines[0].split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                header[k.strip()] = v.strip()

    unigrams: Dict[str, Tuple[int, bool, bool, str]] = {}
    bigrams: Dict[str, List[Tuple[str, int]]] = collections.defaultdict(list)
    current_word: Optional[str] = None

    for line in lines[1:]:
        if line.startswith(" word="):
            fields: Dict[str, str] = {}
            for part in line.strip().split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k] = v
            word = fields.get("word", "")
            if not word:
                current_word = None
                continue
            current_word = word
            freq = int(fields.get("f", "0") or "0")
            flags = fields.get("flags", "")
            offensive = fields.get("possibly_offensive") == "true" or "offensive" in flags
            not_a_word = fields.get("not_a_word") == "true"
            prev = unigrams.get(word)
            if prev is None or freq > prev[0]:
                unigrams[word] = (freq, offensive or (prev[1] if prev else False), not_a_word, flags)
        elif line.startswith("  bigram=") and current_word:
            fields = {}
            for part in line.strip().split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k] = v
            nxt = fields.get("bigram", "")
            bfreq = int(fields.get("f", "0") or "0")
            if nxt and bfreq > 0 and WORD_RE.match(nxt):
                bigrams[lexicon_key(current_word)].append((nxt, bfreq))

    return header, unigrams, bigrams


def load_scowl_base(path: pathlib.Path) -> Dict[str, int]:
    result: Dict[str, int] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "\t" not in line:
            continue
        parts = line.split("\t")
        word = parts[0].strip()
        try:
            tier = int(parts[1].strip())
        except ValueError:
            continue
        if WORD_RE.match(word):
            result[word] = min(result.get(word, tier), tier)
    return result


def load_modern_supplement(path: pathlib.Path) -> Dict[str, Tuple[int, bool]]:
    result: Dict[str, Tuple[int, bool]] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        word = parts[0].strip()
        weight = int(parts[1].strip())
        flags = parts[2].strip() if len(parts) >= 3 else ""
        if WORD_RE.match(word):
            result[word] = (max(0, min(255, weight)), "o" in flags)
    return result


def load_tatoeba_base(path: pathlib.Path) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "\t" not in line:
            continue
        key, rest = line.split("\t", 1)
        words = [w for w in rest.strip().split() if w]
        if key and words:
            result[key] = words
    return result


def normalize_follower_word(word: str) -> str:
    if "_" in word:
        parts = [normalize_follower_word(p) for p in word.split("_") if p]
        return "_".join(parts)
    low = word.lower()
    if low == "i":
        return "I"
    if low.startswith("i'") and len(low) > 2:
        return "I" + low[1:]
    return low


def extract_tatoeba_trigrams(
    bz2_path: pathlib.Path,
    valid_keys: Set[str],
    avoid_keys: Set[str],
    limit: int = 1_200,
    min_count: int = 15,
) -> Dict[str, List[str]]:
    """Extract top empirical two-word context followers from Tatoeba sentences (CC BY 2.0 FR)."""
    mid_cap: collections.Counter = collections.Counter()
    mid_low: collections.Counter = collections.Counter()
    trigrams: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    with bz2.open(bz2_path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            sent = parts[2].replace("\u2019", "'").replace("\u2018", "'")
            clauses = re.split(r"[.,!?;:()\[\]\"“”\n]+", sent)
            for ci, clause in enumerate(clauses):
                toks = SENTENCE_TOKEN_RE.findall(clause)
                for i, tok in enumerate(toks):
                    low = tok.lower()
                    if ci > 0 or i > 0:
                        if tok[0].isupper():
                            mid_cap[low] += 1
                        else:
                            mid_low[low] += 1
                if len(toks) < 3:
                    continue
                for i in range(len(toks) - 2):
                    w1, w2, w3 = toks[i], toks[i + 1], toks[i + 2]
                    if i > 0 and w1[0].isupper() and not (w1 == "I" or w1.startswith("I'")):
                        continue
                    if w2[0].isupper() and not (w2 == "I" or w2.startswith("I'")):
                        continue
                    if w3[0].isupper() and not (w3 == "I" or w3.startswith("I'")):
                        continue
                    k1, k2 = lexicon_key(w1), lexicon_key(w2)
                    nw3 = normalize_follower_word(w3)
                    k3 = lexicon_key(nw3)
                    if k1 in avoid_keys or k2 in avoid_keys or k3 in avoid_keys:
                        continue
                    if k1 not in valid_keys or k2 not in valid_keys or k3 not in valid_keys:
                        continue
                    trigrams[f"{k1} {k2}"][nw3] += 1

    proper_nouns: Set[str] = set()
    for w, c in mid_cap.items():
        if w == "i" or w.startswith("i'"):
            continue
        if c >= 5 and c / (c + mid_low[w]) >= 0.25:
            proper_nouns.add(lexicon_key(w))

    ranked: List[Tuple[int, str, List[str]]] = []
    for ctx, ctr in trigrams.items():
        k1, k2 = ctx.split(" ")
        if k1 in proper_nouns or k2 in proper_nouns:
            continue
        good = [
            (w, c)
            for w, c in ctr.most_common(6)
            if c >= min_count and lexicon_key(w) not in proper_nouns
        ]
        if good:
            ranked.append((sum(c for _, c in good), ctx, [w for w, _ in good]))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return {ctx: words for _tot, ctx, words in ranked[:limit]}


def load_or_extract_tatoeba_trigrams(
    trigram_cache_path: pathlib.Path,
    bz2_path: Optional[pathlib.Path],
    valid_keys: Set[str],
    avoid_keys: Set[str],
    refresh: bool = False,
) -> Dict[str, List[str]]:
    if bz2_path and bz2_path.exists() and (refresh or not trigram_cache_path.exists()):
        extracted = extract_tatoeba_trigrams(bz2_path, valid_keys, avoid_keys)
        lines = [f"{k}\t{' '.join(words)}\n" for k, words in sorted(extracted.items())]
        trigram_cache_path.parent.mkdir(parents=True, exist_ok=True)
        trigram_cache_path.write_text("".join(lines), encoding="utf-8")
        return extracted
    return load_tatoeba_base(trigram_cache_path)


def build_lexicon(
    aosp_us: Dict[str, Tuple[int, bool, bool, str]],
    aosp_gb: Dict[str, Tuple[int, bool, bool, str]],
    scowl: Dict[str, int],
    supplement: Dict[str, Tuple[int, bool]],
) -> List[Tuple[str, str, int, int, bool, str]]:
    """Build sorted lexicon entries: (key, word, tier, weight, avoid_autocorrect, flags)."""
    # Keep separate AOSP en_US and en_GB frequency tables for dialect tagging.
    aosp_us_freq: Dict[str, int] = {
        w: f for w, (f, _off, not_a_word, _fl) in aosp_us.items() if not not_a_word and f > 0
    }
    aosp_gb_freq: Dict[str, int] = {
        w: f for w, (f, _off, not_a_word, _fl) in aosp_gb.items() if not not_a_word and f > 0
    }

    # Map word -> [weight, avoid_autocorrect]
    entries: Dict[str, List[object]] = {}

    def upsert(word: str, weight: int, avoid: bool, prefer_max: bool = True) -> None:
        if not WORD_RE.match(word):
            return
        k = lexicon_key(word)
        if len(k) < 2 and word not in ("a", "I"):
            return
        if len(k) > 32:
            return
        cur = entries.get(word)
        if cur is None:
            entries[word] = [weight, avoid]
        else:
            if prefer_max:
                cur[0] = max(int(cur[0]), weight)
            else:
                cur[0] = weight
            cur[1] = bool(cur[1]) or avoid

    # Lookup helpers for AOSP using max(f_us, f_gb) so UK spellings retain strong base weights.
    def aosp_lookup(word: str) -> Optional[Tuple[int, bool]]:
        us = aosp_us.get(word)
        gb = aosp_gb.get(word)
        us_valid = us is not None and not us[2] and us[0] > 0
        gb_valid = gb is not None and not gb[2] and gb[0] > 0
        if us_valid and gb_valid:
            assert us is not None and gb is not None
            return max(us[0], gb[0]), (us[1] or gb[1])
        if us_valid:
            assert us is not None
            return us[0], us[1]
        if gb_valid:
            assert gb is not None
            return gb[0], gb[1]
        return None

    # 1. Seed ALL SCOWL baseline entries (zero loss of valid SCOWL spellings)
    for word, scowl_tier in scowl.items():
        if word == "i":
            continue
        info = aosp_lookup(word)
        if info is not None:
            freq, offensive = info
            weight = calibrate_aosp_freq(freq)
            # Keep everyday SCOWL tier-0 words (e.g. "thank") in tier 0 when AOSP confirms frequency.
            if scowl_tier == 0 and freq >= 95:
                weight = max(weight, 196 if len(word) <= 6 else 186)
            upsert(word, weight, offensive)
        else:
            # Check case-insensitive offensive marker in AOSP
            low_info = aosp_lookup(word.lower())
            offensive = low_info[1] if low_info else False
            upsert(word, scowl_fallback_weight(scowl_tier, word), offensive)

    # 2. Add high-value AOSP en_US words (f >= 60, plus offensive words f >= 35)
    for word, (freq, offensive, not_a_word, flags) in aosp_us.items():
        if not_a_word or freq <= 0 or not WORD_RE.match(word):
            continue
        if word == "i":
            continue
        if freq < 60 and not (offensive and freq >= 35):
            continue
        # Skip obscure all-caps abbreviations unless well-established (e.g. NASA, TV, OK, DNA)
        if word.isupper() and len(word) >= 2 and freq < 105:
            continue
        weight = calibrate_aosp_freq(freq)
        upsert(word, weight, offensive)

    # 3. Add UK spellings from AOSP en_GB (f >= 85 general, or f >= 35 for UK dialect spellings)
    for word, (freq, offensive, not_a_word, flags) in aosp_gb.items():
        if not_a_word or freq <= 0 or not WORD_RE.match(word):
            continue
        if word == "i":
            continue
        is_uk_spelling = classify_dialect(word, aosp_us_freq, aosp_gb_freq) == "b"
        if freq < 85 and not (is_uk_spelling and freq >= 35):
            continue
        if word.isupper() and len(word) >= 2 and freq < 110:
            continue
        weight = calibrate_aosp_freq(freq)
        upsert(word, weight, offensive)

    # 3b. Ensure everyday UK words (e.g. mum, cheque, tyre) retain tier-1 base strength.
    for word, floor_w in UK_EVERYDAY_WEIGHT_FLOORS.items():
        upsert(word, floor_w, False, prefer_max=True)

    # 4. Optional wordfreq enrichment if installed (never required)
    try:
        import wordfreq  # type: ignore

        for word, state in entries.items():
            if word in supplement:
                continue
            zipf = wordfreq.zipf_frequency(word, "en")
            if zipf >= 5.5:
                state[0] = max(int(state[0]), min(245, int(175 + (zipf - 5.5) * 28)))
    except Exception:
        pass

    # 5. Merge curated modern mobile / conversational / tech / proper-brand supplement
    supplement_keys: Set[str] = set()
    for word, (weight, avoid) in supplement.items():
        supplement_keys.add(lexicon_key(word))
        upsert(word, weight, avoid, prefer_max=True)
        # If supplement defines a proper-cased brand (e.g. iPhone, GitHub, macOS),
        # remove conflicting lowercase/other-case duplicate unless it is an everyday
        # common noun (apple, swift, react, steam, maps, rust, zoom, slack, stripe, kindle, discord, claude, gemini).
        common_nouns = {
            "apple", "swift", "react", "steam", "maps", "rust", "zoom", "slack",
            "stripe", "kindle", "discord", "chrome", "safari", "audible", "notion",
            "amazon", "python", "oracle", "gemini", "claude", "docker", "tailwind",
            "twitch", "thunderbolt", "outlook", "uber", "virgin",
        }
        if word != word.lower() and word.lower() not in common_nouns:
            entries.pop(word.lower(), None)

    # 6. Hygiene rules: single-letter words ("I" and "a" only)
    for w in list(entries.keys()):
        if len(lexicon_key(w)) < 2 and w not in ("a", "I"):
            entries.pop(w, None)
    entries["I"] = [250, False]
    entries["a"] = [250, False]

    # 7. Contraction hygiene: keys in CONTRACTION_ONLY_KEYS map ONLY to the contraction.
    for ckey, canonical in CONTRACTION_ONLY_KEYS.items():
        best_weight = 205
        for w in list(entries.keys()):
            if lexicon_key(w) == ckey:
                if w == canonical:
                    best_weight = max(best_weight, int(entries[w][0]))
                else:
                    entries.pop(w, None)
        entries[canonical] = [max(best_weight, int(entries.get(canonical, [0, False])[0])), False]

    # 8. Deduplicate capitalized variants when lowercase exists (e.g., Will beside will),
    #    EXCEPT proper-cased entries from modern_supplement and all-caps abbreviations like NASA.
    supplement_words = set(supplement.keys())
    lower_set = {w for w in entries if w == w.lower()}
    for w in list(entries.keys()):
        if w in ("I",) or w in supplement_words:
            continue
        if w != w.lower() and w.lower() in lower_set:
            # Keep if it's an apostrophe contraction with I (I'm, I'll, I'd, I've)
            if w.startswith("I'"):
                continue
            entries.pop(w, None)

    # 9. Build final sorted rows: strictly sorted by (key, tier, -weight, word)
    #    for O(log N) binary search in EnglishLexicon.swift.
    rows: List[Tuple[str, str, int, int, bool, str]] = []
    for word, (weight_obj, avoid_obj) in entries.items():
        weight = max(0, min(255, int(weight_obj)))
        avoid = bool(avoid_obj)
        tier = weight_to_tier(weight)
        dialect = classify_dialect(word, aosp_us_freq, aosp_gb_freq)
        flags = dialect + ("o" if avoid else "")
        rows.append((lexicon_key(word), word, tier, weight, avoid, flags))

    rows.sort(key=lambda r: (r[0], r[2], -r[3], r[1]))
    return rows


def build_followers(
    aosp_us_bigrams: Dict[str, List[Tuple[str, int]]],
    tatoeba: Dict[str, List[str]],
    tatoeba_trigrams: Dict[str, List[str]],
    lexicon_rows: List[Tuple[str, str, int, int, bool, str]],
) -> List[Tuple[str, List[str]]]:
    """Build compact single-word + two-word context follower rows (< 300 KB)."""
    SINGLE_WORDS_KEPT = 9_500
    MAX_FOLLOWERS = 6

    avoid_keys: Set[str] = set()
    key_weight: Dict[str, int] = {}
    for k, _w, _t, wt, avoid, _fl in lexicon_rows:
        if avoid:
            avoid_keys.add(k)
        else:
            key_weight[k] = max(key_weight.get(k, 0), wt)

    merged: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    # 1. Tatoeba single-word baseline (everyday conversational sentences)
    for k, words in tatoeba.items():
        if k in avoid_keys or " " in k:
            continue
        for idx, w in enumerate(words):
            nw = normalize_follower_word(w)
            nk = lexicon_key(nw)
            if nk in avoid_keys or not WORD_RE.match(nw):
                continue
            merged[k][nw] += max(20, 100 - idx * 18)

    # 2. AOSP bigrams (when present in upstream file)
    for k, pairs in aosp_us_bigrams.items():
        if k in avoid_keys:
            continue
        for w2, bfreq in pairs:
            nw = normalize_follower_word(w2)
            nk = lexicon_key(nw)
            if nk in avoid_keys:
                continue
            merged[k][nw] += max(15, bfreq)

    # 3. Curated high-frequency mobile bigrams (including `_`-joined multi-word phrase followers)
    for k, words in CURATED_BIGRAMS.items():
        for idx, w in enumerate(words):
            nw = normalize_follower_word(w)
            if not FOLLOWER_TOKEN_RE.match(nw):
                continue
            # Ensure curated order (including multi-word phrase starters) ranks in top 6.
            merged[k][nw] += max(80, 260 - idx * 25)

    # Select top SINGLE_WORDS_KEPT single-word keys by (lexicon weight, total follower score, key)
    ranked_single = sorted(
        merged.keys(),
        key=lambda k: (-key_weight.get(k, 0), -sum(merged[k].values()), k),
    )[:SINGLE_WORDS_KEPT]

    table: Dict[str, List[str]] = {}
    for k in ranked_single:
        top = [w for w, _ in merged[k].most_common(MAX_FOLLOWERS)]
        if top:
            table[k] = top

    # 4. Two-word context keys (empirical Tatoeba trigrams + curated conversational phrases)
    trigram_merged: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ctx, words in tatoeba_trigrams.items():
        parts = ctx.split(" ")
        if len(parts) != 2 or parts[0] in avoid_keys or parts[1] in avoid_keys:
            continue
        for idx, w in enumerate(words):
            nw = normalize_follower_word(w)
            if not FOLLOWER_TOKEN_RE.match(nw):
                continue
            if any(lexicon_key(p) in avoid_keys for p in nw.split("_")):
                continue
            trigram_merged[ctx][nw] += max(20, 100 - idx * 16)

    for ctx, words in CURATED_TRIGRAMS.items():
        for idx, w in enumerate(words):
            nw = normalize_follower_word(w)
            if not FOLLOWER_TOKEN_RE.match(nw):
                continue
            trigram_merged[ctx][nw] += max(120, 320 - idx * 30)

    for ctx, ctr in trigram_merged.items():
        top = [w for w, _ in ctr.most_common(MAX_FOLLOWERS)]
        if top:
            table[ctx] = top

    return sorted(table.items(), key=lambda item: item[0])


def write_rime_package(
    rows: List[Tuple[str, str, int, int, bool, str]],
    version: str,
) -> pathlib.Path:
    """Write dist/Payload/hare_eng.dict.yaml, hare_eng.schema.yaml, and deterministic zip."""
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dict_path = PAYLOAD_DIR / "hare_eng.dict.yaml"
    schema_path = PAYLOAD_DIR / "hare_eng.schema.yaml"
    zip_path = DIST_DIR / "hare-english-rime.zip"

    dict_header = f"""# Rime dictionary: hare_eng
# Permissive commercial-safe English dictionary compiled by Hare-english-dict.
# Sources: Google AOSP LatinIME (Apache-2.0), SCOWL 2020 (SCOWL), Modern Supplement (CC0-1.0).
---
name: hare_eng
version: "{version}"
sort: by_weight
...
"""
    dict_lines = [dict_header]
    for k, word, _tier, weight, avoid, _flags in rows:
        if avoid:
            continue
        # Rime weight: scaled integer preserving ordering
        rime_weight = max(1, int(round((weight / 255.0) ** 3 * 100000)))
        dict_lines.append(f"{word}\t{k}\t{rime_weight}\n")
    dict_path.write_text("".join(dict_lines), encoding="utf-8")

    schema_text = f"""# Rime schema: hare_eng
# encoding: utf-8
schema:
  schema_id: hare_eng
  name: Hare English
  version: "{version}"
  author:
    - Hare IME Project
  description: >-
    Permissive commercial-safe English dictionary for Rime & Hare IME.

switches:
  - name: ascii_mode
    reset: 0
    states: [ English, ASCII ]

engine:
  processors:
    - ascii_composer
    - key_binder
    - speller
    - recognizer
    - punctuator
    - selector
    - navigator
    - express_editor
  segmentors:
    - ascii_segmentor
    - abc_segmentor
    - punct_segmentor
    - fallback_segmentor
  translators:
    - table_translator

speller:
  alphabet: zyxwvutsrqponmlkjihgfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA
  delimiter: " '"

translator:
  dictionary: hare_eng
  enable_sentence: false
  enable_user_dict: false
"""
    schema_path.write_text(schema_text, encoding="utf-8")

    # Deterministic zip (fixed timestamp 2025-01-01 00:00:00, sorted archive paths)
    fixed_dt = (2025, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in ("Payload/hare_eng.dict.yaml", "Payload/hare_eng.schema.yaml"):
            src = DIST_DIR / rel
            zi = zipfile.ZipInfo(rel, date_time=fixed_dt)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, src.read_bytes())

    return zip_path


def write_github_output(path: Optional[str], values: Dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Hare English dictionary artifacts.")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=ROOT / ".cache")
    parser.add_argument("--offline", action="store_true", help="Use cached AOSP files without network.")
    parser.add_argument(
        "--check-and-update",
        action="store_true",
        help="Skip rebuild if upstream + input digests match upstream-manifest.json.",
    )
    parser.add_argument("--force", action="store_true", help="Force rebuild even when digests match.")
    parser.add_argument(
        "--tatoeba-sentences",
        type=pathlib.Path,
        default=DEFAULT_TATOEBA_BZ2,
        help="Optional Tatoeba eng_sentences.tsv.bz2 used to refresh data/tatoeba_trigrams.txt.",
    )
    parser.add_argument(
        "--refresh-trigrams",
        action="store_true",
        help="Force re-extraction of data/tatoeba_trigrams.txt from --tatoeba-sentences.",
    )
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()

    us_text, us_url, us_sha = fetch_aosp_wordlist("en_US", args.cache_dir, args.offline)
    gb_text, gb_url, gb_sha = fetch_aosp_wordlist("en_GB", args.cache_dir, args.offline)
    aosp_commit = fetch_aosp_commit_sha(args.offline)

    scowl_path = DATA_DIR / "scowl_base.txt"
    tatoeba_path = DATA_DIR / "tatoeba_base.txt"
    tatoeba_trigrams_path = DATA_DIR / "tatoeba_trigrams.txt"
    supplement_path = DATA_DIR / "modern_supplement.tsv"

    us_header, aosp_us, aosp_us_bigrams = parse_aosp_combined(us_text)
    gb_header, aosp_gb, _ = parse_aosp_combined(gb_text)
    scowl = load_scowl_base(scowl_path)
    tatoeba = load_tatoeba_base(tatoeba_path)
    supplement = load_modern_supplement(supplement_path)

    lexicon_rows = build_lexicon(aosp_us, aosp_gb, scowl, supplement)
    valid_keys = {k for k, _w, _t, _wt, avoid, _fl in lexicon_rows if not avoid}
    avoid_keys = {k for k, _w, _t, _wt, avoid, _fl in lexicon_rows if avoid}
    tatoeba_trigrams = load_or_extract_tatoeba_trigrams(
        tatoeba_trigrams_path,
        args.tatoeba_sentences,
        valid_keys,
        avoid_keys,
        refresh=args.refresh_trigrams,
    )

    input_digests = {
        "aosp_en_US_sha256": us_sha,
        "aosp_en_GB_sha256": gb_sha,
        "scowl_base_sha256": sha256_file(scowl_path) if scowl_path.exists() else "",
        "tatoeba_base_sha256": sha256_file(tatoeba_path) if tatoeba_path.exists() else "",
        "tatoeba_trigrams_sha256": sha256_file(tatoeba_trigrams_path) if tatoeba_trigrams_path.exists() else "",
        "modern_supplement_sha256": sha256_file(supplement_path) if supplement_path.exists() else "",
        "compiler_sha256": sha256_file(pathlib.Path(__file__).resolve()),
    }

    if args.check_and_update and not args.force and MANIFEST_PATH.exists():
        prev = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        prev_digests = prev.get("input_digests", {})
        all_outputs_exist = all(
            (DIST_DIR / rel).exists()
            for rel in (
                "EnglishLexicon.txt",
                "EnglishFollowers.txt",
                "Payload/hare_eng.dict.yaml",
                "Payload/hare_eng.schema.yaml",
                "hare-english-rime.zip",
            )
        )
        if prev_digests == input_digests and all_outputs_exist:
            print("UPDATED=false (upstream and local input digests unchanged)")
            write_github_output(args.github_output, {"UPDATED": "false"})
            return 0

    follower_rows = build_followers(aosp_us_bigrams, tatoeba, tatoeba_trigrams, lexicon_rows)

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    lexicon_path = DIST_DIR / "EnglishLexicon.txt"
    followers_path = DIST_DIR / "EnglishFollowers.txt"

    lex_lines = [
        f"{word}\t{tier}\t{weight}\t{flags}\n" if flags else f"{word}\t{tier}\t{weight}\n"
        for _key, word, tier, weight, _avoid, flags in lexicon_rows
    ]
    lexicon_path.write_text("".join(lex_lines), encoding="utf-8")

    fol_lines = [f"{key}\t{' '.join(words)}\n" for key, words in follower_rows]
    followers_path.write_text("".join(fol_lines), encoding="utf-8")

    now_utc = dt.datetime.now(dt.timezone.utc)
    version = now_utc.strftime("%Y.%m.%d")
    zip_path = write_rime_package(lexicon_rows, version)

    tier_counts = collections.Counter(r[2] for r in lexicon_rows)
    offensive_count = sum(1 for r in lexicon_rows if r[4])
    us_dialect_count = sum(1 for r in lexicon_rows if "u" in r[5])
    uk_dialect_count = sum(1 for r in lexicon_rows if "b" in r[5])
    trigram_key_count = sum(1 for k, _ in follower_rows if " " in k)

    manifest = {
        "version": version,
        "built_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aosp_commit": aosp_commit,
        "upstream_sources": {
            "aosp_en_US": {
                "url": us_url,
                "license": "Apache-2.0",
                "header": us_header,
                "sha256": us_sha,
            },
            "aosp_en_GB": {
                "url": gb_url,
                "license": "Apache-2.0",
                "header": gb_header,
                "sha256": gb_sha,
            },
            "scowl_base": {"path": "data/scowl_base.txt", "license": "SCOWL"},
            "tatoeba_base": {"path": "data/tatoeba_base.txt", "license": "CC BY 2.0 FR"},
            "tatoeba_trigrams": {"path": "data/tatoeba_trigrams.txt", "license": "CC BY 2.0 FR"},
            "modern_supplement": {"path": "data/modern_supplement.tsv", "license": "CC0-1.0"},
        },
        "input_digests": input_digests,
        "outputs": {
            "EnglishLexicon.txt": {
                "entries": len(lexicon_rows),
                "bytes": lexicon_path.stat().st_size,
                "sha256": sha256_file(lexicon_path),
                "tier_counts": {str(t): tier_counts[t] for t in range(4)},
                "avoid_autocorrect_count": offensive_count,
                "us_dialect_count": us_dialect_count,
                "uk_dialect_count": uk_dialect_count,
            },
            "EnglishFollowers.txt": {
                "keys": len(follower_rows),
                "two_word_keys": trigram_key_count,
                "bytes": followers_path.stat().st_size,
                "sha256": sha256_file(followers_path),
            },
            "hare-english-rime.zip": {
                "bytes": zip_path.stat().st_size,
                "sha256": sha256_file(zip_path),
            },
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    print(
        f"Built Hare English dictionary v{version}: "
        f"{len(lexicon_rows)} lexicon entries ({lexicon_path.stat().st_size // 1024} KB, "
        f"tiers={dict(sorted(tier_counts.items()))}, avoidAutocorrect={offensive_count}, "
        f"us={us_dialect_count}, uk={uk_dialect_count}), "
        f"{len(follower_rows)} follower keys ({trigram_key_count} 2-word, {followers_path.stat().st_size // 1024} KB), "
        f"rime zip {zip_path.stat().st_size // 1024} KB."
    )
    print("UPDATED=true")
    write_github_output(args.github_output, {"UPDATED": "true", "VERSION": version})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

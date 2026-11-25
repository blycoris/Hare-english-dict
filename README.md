# Hare English Dictionary (`Hare-english-dict`)

Auto-updating, commercially permissive English lexicon, next-word follower table, and Rime dictionary package built for [Hare IME](https://hareime.com).

## License Purity

Every bundled output in `dist/` is compiled **strictly** from permissive, commercially safe open-source datasets (zero GPL/copyleft dictionary data):

| Source | Role | License |
| --- | --- | --- |
| [Google AOSP LatinIME Dictionaries](https://android.googlesource.com/platform/packages/inputmethods/LatinIME/+/refs/heads/main/dictionaries/) (`en_US` & `en_GB`) | Continuous word frequencies, contractions, US/UK vocabulary, and offensive-word flags | Apache-2.0 |
| [SCOWL 2020](http://wordlist.aspell.net) (`data/scowl_base.txt`) | Baseline English spelling coverage | SCOWL (permissive) |
| [Tatoeba](https://tatoeba.org) (`data/tatoeba_base.txt`) | Everyday English next-word follower pairs | CC BY 2.0 FR |
| Modern Mobile & Tech Supplement (`data/modern_supplement.tsv`) | Everyday chat abbreviations, contractions, lifestyle/tech terms, and proper-cased brands | CC0-1.0 |

Full license texts are kept in `data/licenses/`.

## Generated Artifacts (`dist/`)

- `dist/EnglishLexicon.txt` — Sorted by lowercase apostrophe-free ASCII key for $O(\log N)$ binary search: `word\ttier\tweight[\to]` (`tier` in `0..3`, `weight` in `0..255`, `o` = `avoidAutocorrect`).
- `dist/EnglishFollowers.txt` — Sorted by key, up to 6 everyday followers per word (`key\tw1 w2 ...`).
- `dist/Payload/hare_eng.dict.yaml` & `dist/Payload/hare_eng.schema.yaml` — Rime English dictionary & schema.
- `dist/hare-english-rime.zip` — Deterministic Rime import package.
- `upstream-manifest.json` — Upstream URLs, SHA-256 digests, counts, and build timestamp.

## Usage

```bash
# Fetch live upstream AOSP wordlists, compile all artifacts, and verify
python3 scripts/build_english_dict.py
python3 scripts/verify_dict.py

# Check-and-update mode (used by weekly GitHub Actions workflow)
python3 scripts/build_english_dict.py --check-and-update
```

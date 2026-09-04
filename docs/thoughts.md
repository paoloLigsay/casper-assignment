# Open Questions / Thoughts — to validate one by one

Raised during code walkthrough on 2026-09-04. Not yet validated against the assignment brief or
prioritized — just captured here so we don't lose them. Pointers to relevant code included for
when we sit down to check each one.

---

## 1. Should review selection favor rating, not be uniform-random?

Right now `TweakExtractor.extract_single_modification()` (`src/llm_pipeline/tweak_extractor.py:136`)
does `random.choice(modification_reviews)` — every `has_modification=True` review has equal odds,
regardless of star rating. A 5-star review saying "I added an egg and it's perfect" and a 3-star
review saying "I tried X and it didn't really help" are equally likely to be selected.

Question: should selection weight toward (or filter to) higher-rated reviews, on the theory that a
tweak from a well-received review is more likely to be a genuine improvement than one from a
middling review? Or is rating orthogonal to whether the *modification itself* worked (e.g. a 3-star
review might rate the base recipe low but still describe a tweak that helped)?

To check: `Review.rating` (`src/llm_pipeline/models.py:142`) is already parsed and available at
selection time — nothing structural blocks using it, it's just unused for selection today.

---

## 2. If the selected review fails extraction, does the pipeline try another review, or just give up?

Confirmed by tracing the code (no fallback exists):

- `extract_single_modification()` picks exactly one review via `random.choice()`
  (`src/llm_pipeline/tweak_extractor.py:136`), calls `extract_modification()` on it once
  (`:139`). If that returns `None` (JSON parse failure or Pydantic `ValidationError` after
  exhausting `max_retries=2` — see `:66-111`), it returns `(None, None)` — there is no loop back to
  `modification_reviews` to try a different candidate.
- `pipeline.py:152-154` then bails the whole recipe: `if not modification or not source_review:
  return None`.
- One recipe's failure doesn't stop other recipes (`process_recipe_directory` loops over recipe
  *files* independently, `pipeline.py:212-220`) — but within a single recipe, if the one randomly
  chosen review fails extraction, the other 3 (for cookies) modification-flagged reviews are never
  attempted. The recipe just produces no output for that run.

Question: should extraction retry with a different candidate review from `modification_reviews`
before giving up on the recipe entirely, instead of failing on the first (only) pick?

To check: whether this is worth fixing standalone, or folds into the larger "process all flagged
reviews, not just one" fix (`docs/tasks.md` item 1) — if that fix lands, this failure mode mostly
disappears since every review gets attempted anyway.

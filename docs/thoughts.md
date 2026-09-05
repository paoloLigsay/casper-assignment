# Open Questions / Thoughts — to validate one by one

Raised during code walkthrough on 2026-09-04. Not yet validated against the assignment brief or
prioritized — just captured here so we don't lose them. Pointers to relevant code included for
when we sit down to check each one.

---

## 1. Should review selection favor rating?

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

## 3. Direction Hint: 
"""
Are we certain that the system parses out ALL the intended modifications?
E.g. If the review says “I added an egg and halved the sugar” -> these are two discrete modifications!
"""

Solution :
-> Make sure we support multiple modification, a list instead of One (Schema). 
-> Update prompt:
a. SYSTEM_PROMPT to support discrete modifications
b. build_simple_prompt: to update the JSON structure

## 4. Direction Hint:
"""
Does the system scale beyond the 5 examples we gave? Are there poor assumptions embedded in the current implementation?
"""

## Challenges | Cases / Issues found during testing
### There are cases where 1 instruction contains 2 actions causing silent fail during modification
E.g., "Dissolve baking soda in hot water. Add to batter along with salt."

**What happened (live evidence, chocolate chip cookie recipe, cream-of-tartar review):**
The review says 4 things, including "(2) I omitted the water." The LLM correctly identified this
as a `removal` modification, but expressed it as:
```json
{
    "modification_type": "removal",
    "reasoning": "To simplify the recipe and potentially improve the texture",
    "edits": [
        { "target": "instructions", "operation": "remove",
          "find": "Dissolve baking soda in hot water. Add to batter along with salt." }
    ]
}
```
`RecipeModifier.apply_edit`'s `remove` branch (`src/llm_pipeline/recipe_modifier.py:123-139`) does
`modified_content.pop(index)` — it deletes the entire matched **list item**, not a sub-string within
it. `find` matched that instruction line at `similarity: 1.00` (an exact match), so it popped
cleanly. Two things went wrong as a result:
1. The instruction line was doing two unrelated jobs at once — "dissolve baking soda in hot water"
   AND "add salt to the batter" — because the source recipe stores it as a single array element.
   Removing "the water part" took "add salt" down with it. The final recipe still lists
   `"0.5 teaspoon salt"` as an ingredient but no instruction anywhere tells you to use it.
2. The actual ingredient line, `"2 teaspoons hot water"`, was never touched — no edit ever had
   `target: "ingredients"` for it. The model treated the instruction-side removal as covering the
   whole "omit the water" tweak and never circled back to the ingredient list.

**Why it's silent:** every log line reports success — `similarity: 1.00`, "Removed ... successfully",
`enhancement_summary.total_changes` increments normally. Nothing errors, warns, or fails a check,
because mechanically everything happened exactly as instructed. The failure is semantic (wrong
scope), not mechanical (no match failure, no exception).

**Root cause:** `remove`/`replace` operate at whole-list-item granularity
(`src/llm_pipeline/recipe_modifier.py:apply_edit`), but the source recipe's `instructions` list
sometimes crams multiple independent actions into one array element (period-separated sentences
sharing one list item). Any edit targeting one of those actions risks taking the others with it.

**Fix direction (discussed, not yet implemented):**
1. Add a preprocessing "synthesizer" pass — run once per recipe, before extraction, over the
   in-memory `Recipe.instructions` only (never touching the source `data/recipe_*.json` file) —
   that splits compound instruction lines into one atomic action per list item, rewritten to stay
   self-contained (not just split on periods, since naive splitting can break implicit references
   like "add **it** to the batter"). This prevents the collateral-deletion half of the bug, since
   `remove`/`replace` would then only ever be able to pop a single atomic action.
2. This does **not** by itself fix the missing-ingredient-removal half (`"2 teaspoons hot water"`
   never removed) — that needs the separate prompt-scope fix already noted in item 3's direction
   (checking that an edit's `find` scope matches what `reasoning` actually describes).
# Session Notes — Modification Pipeline Walkthrough

Date: 2026-09-04. Captures a live code walkthrough of `test_pipeline.py single` and, in depth, how
`TweakExtractor` and `RecipeModifier` work — done by reading the code and re-running the pipeline
live against `data/recipe_10813_best-chocolate-chip-cookies.json`. This is a session record, not a
new analysis doc — see `docs/architecture.md` and `docs/tasks.md` for the canonical write-ups;
this file cross-references them and adds the live examples produced during the session.

---

## 1. `test_pipeline.py single` — top-to-bottom flow

1. Module load: `load_dotenv()` reads `.env` into `os.environ`.
2. `test_single_recipe()`: checks `OPENAI_API_KEY` first, fails fast if missing (no pipeline object
   built at all).
3. `LLMAnalysisPipeline()` construction (`pipeline.py:28`): calls `load_dotenv()` again, sets
   `output_dir = Path("data/enhanced")` (relative — the `cd src` gotcha from `CLAUDE.md`), builds
   `TweakExtractor`, `RecipeModifier`, `EnhancedRecipeGenerator`.
4. Hardcoded recipe file existence check: `../data/recipe_10813_best-chocolate-chip-cookies.json`.
5. `pipeline.process_single_recipe(recipe_file, save_output=True)` (`pipeline.py:116`):
   - Load + parse JSON into `Recipe` + `List[Review]`.
   - Gate: `if not any(r.has_modification for r in reviews): return None`.
   - Step 1 — extract (see §3 below).
   - Step 2 — apply (see §4 below).
   - Step 3 — `EnhancedRecipeGenerator.generate_enhanced_recipe(...)`, then save to
     `<output_dir>/enhanced_<id>_<slug>.json`.

**Confirmed: no live scraping happens on a pipeline run.** `data/recipe_*.json` files are static,
scraped once ahead of time by `scraper_v2.py` (run separately, never called by the pipeline). The
pipeline only ever does `json.load()` on the pre-saved file. The only network call during a run is
the OpenAI API call in Step 1.

---

## 2. Data source: `reviews` vs `featured_tweaks`

- **`reviews`** (`pipeline.py:91` `parse_reviews_data`) — this is what the pipeline actually reads.
  Confirmed via `grep`: `recipe_data.get("reviews", [])`.
- **`featured_tweaks`** — present in every `data/recipe_*.json` (scraped separately in
  `scraper_v2.py:236-260` from "photo-dialog" review items on the page, independently regex-flagged
  for `has_modification`), but `pipeline.py`'s parsing functions never touch this key. **Confirmed
  dead data** — scraped, saved, never consumed.

`has_modification` filtering happens twice:
- At scrape time (`scraper_v2.py`, regex over review text) — sets the flag baked into the JSON file.
- At extraction time (`tweak_extractor.py:129`) — filters the already-flagged reviews down to the
  candidate pool for `random.choice()`.

---

## 3. Step 1 — Extraction (`TweakExtractor`, `tweak_extractor.py`)

`extract_single_modification(reviews, recipe)`:
```python
modification_reviews = [r for r in reviews if r.has_modification]   # e.g. 4 of 9 for cookies
selected_review = random.choice(modification_reviews)                # exactly ONE, no fallback
modification = self.extract_modification(selected_review, recipe)    # one LLM call
```
**Confirmed (code trace, no fallback exists):** if `extract_modification` fails after retries, it
returns `None` and `extract_single_modification` returns `(None, None)` — there is no loop back to
try a different candidate review. `pipeline.py:152-154` then bails the whole recipe for that run.
The other modification-flagged reviews are never attempted, even though they're right there. (See
`docs/thoughts.md` item 2.)

`extract_modification()`:
1. Builds prompt via `build_simple_prompt()` (`prompts.py:195`) — the **only** prompt builder ever
   called. `build_few_shot_prompt()` (`prompts.py:159`), which has 4 worked examples including two
   multi-tweak decomposition examples, exists but is dead code (a comment says "to avoid format
   string issues").
2. Calls OpenAI (`model="gpt-3.5-turbo"`, `temperature=0.1`, `response_format={"type":
   "json_object"}`, `max_tokens=1000`). Important nuance: `response_format=json_object` only
   guarantees syntactically valid JSON — **not** that it matches `ModificationObject`'s schema.
3. `json.loads(raw_output)` then `ModificationObject(**modification_data)` — this Pydantic
   validation is the actual enforcement point for allowed `modification_type` values and required
   fields.
4. Retries up to `max_retries=2` on `JSONDecodeError`/`ValidationError` — **but resends the exact
   same prompt each time**, no feedback to the model about what was wrong, at `temperature=0.1`
   (near-deterministic) — so retries mostly don't help against validation failures, only against
   transient empty responses.

### Where `modification_type` categories come from
Defined in two places that must be kept in sync manually — nothing enforces agreement:
- `prompts.py:17-22` (`SYSTEM_PROMPT`) — English descriptions of the 5 categories, shown to the LLM.
- `models.py:35-41` — `ModificationObject.modification_type: Literal["ingredient_substitution",
  "quantity_adjustment", "technique_change", "addition", "removal"]` — the actual enforcement.

**Confirmed: the type-per-object cap is structural, not probabilistic.** `modification_type` is a
scalar `Literal`, not `List[Literal[...]]`. A single `ModificationObject` can never carry more than
one type, no matter how capable the model is — Pydantic would raise `ValidationError` on anything
else. `edits: List[ModificationEdit]` has no such cap and can hold arbitrarily many atomic edits,
but they all still share the one `modification_type` and one `reasoning` string.

### Two distinct root causes for "not all intended modifications are parsed"
This maps to the user's own test scenario ("I added an egg and halved the sugar" = 2 discrete mods):
1. **Schema/prompt design (guaranteed, 100% of the time):** `ModificationObject.modification_type`
   being a scalar `Literal` means one review can never be labeled as containing more than one type
   of change, structurally. This can't be fixed by prompting alone.
2. **Prompt quality + model quality (probabilistic, evidenced live — see §5):** even within the
   room the model already has (`edits` is an unbounded list), the model can silently omit edits
   that don't match the type it already committed to early in the JSON generation. Not a schema
   limit — `build_few_shot_prompt()` already has directly relevant worked examples, just unused.

Applying (`RecipeModifier`) is **not** the bottleneck for this issue — confirmed it never branches
on `modification_type` at all (see §4), only on `edit.target`/`edit.operation`. It would happily
apply however many edits it's handed, of however many implied types — the problem is entirely
upstream, in what extraction hands it.

---

## 4. Step 2 — Applying (`RecipeModifier`, `recipe_modifier.py`)

`apply_modification(recipe, modification)` (`:143`):
1. Deep-copies `recipe.ingredients`/`recipe.instructions` into a new `Recipe` object via
   `copy.deepcopy` (Python stdlib `copy` module — recursively copies nested structures so mutating
   the copy never touches the original `recipe` argument).
2. Loops over every `edit` in `modification.edits`, routes by `edit.target`
   (`"ingredients"`/`"instructions"`) to `apply_edit()`.

`apply_edit(edit, recipe_content)` (`:65`):
1. `find_best_match(edit.find, candidates)` (`:35`) — scores every candidate line via
   `difflib.SequenceMatcher(None, target.lower(), candidate.lower()).ratio()`, keeps the best. Only
   counts as a match if score ≥ `similarity_threshold` (default `0.6`); otherwise **silently
   dropped** — logged via `logger.warning` only, no trace in `ChangeRecord`/output JSON
   (`docs/architecture.md` known limitation #4, `docs/tasks.md` item 3, observed live on nikujaga).
2. Branches on `edit.operation`:
   - `replace`: `original_text.replace(edit.find, edit.replace)` — substring replace inside the
     matched line.
   - `add_after`: `modified_content.insert(index + 1, edit.add)` — new line inserted after match.
   - `remove`: `modified_content.pop(index)`.
3. Appends a `ChangeRecord` per successful op — this becomes `changes_made` in the saved output.

**Not type-aware, by design/confirmed:** `apply_modification`/`apply_edit` never reference
`modification.modification_type`. Applying is mechanical and type-agnostic; only extraction cares
about type.

---

## 5. Live evidence gathered this session

### Run A — egg yolk review (clean single-edit case)
Selected review: *"I used an ice cream scoop, that made 16 big cookies. I did add an additional
egg yolk to help keep the cookie chewy.They turned out fantastic."*

LLM output:
```json
{
  "modification_type": "quantity_adjustment",
  "reasoning": "Adding an additional egg yolk can help keep the cookie chewy and moist.",
  "edits": [
    { "target": "ingredients", "operation": "add_after", "find": "2 eggs", "add": "1 additional egg yolk" }
  ]
}
```
Result: `"2 eggs"` matched at similarity 1.00, `"1 additional egg yolk"` inserted right after it in
`ingredients`. 1 edit in, 1 change out — no failures in this run.

### Run B — cream-of-tartar review (multi-tweak case, evidence for the P0 issue)
Selected review (4 explicit numbered tweaks): *"...(1) I used a half cup of sugar and
one-and-a-half cups of brown sugar; (2) I omitted the water; (3) I added a teaspoon of cream of
tartar to the batter; (4) I refrigerated the batter for at least an hour..."*

Expected (if fully decomposed): 4 discrete modifications across 3 types —
`quantity_adjustment` (sugar), `removal` (water), `addition` (cream of tartar),
`technique_change` (refrigeration).

Actual LLM output — only tweak #1 survived:
```json
{
  "modification_type": "quantity_adjustment",
  "reasoning": "The user adjusted the sugar levels to their preference, resulting in a sweeter cookie with a different texture.",
  "changes_made": [
    { "from_text": "1 cup white sugar", "to_text": "1/2 cup white sugar", "operation": "replace" },
    { "from_text": "1 cup packed brown sugar", "to_text": "1 1/2 cups packed brown sugar", "operation": "replace" }
  ]
}
```
Tweaks #2, #3, #4 are **absent entirely** — not proposed-then-failed (no `find_best_match` warning
in logs for them), just never generated by the LLM in the first place. `enhancement_summary` looks
internally consistent (`total_changes: 2`, one type) with no signal that 3 of 4 sentences in the
source review were never attempted.

**Working hypothesis (not code-proven, reasoning from field order + autoregressive generation):**
`ModificationObject`'s field order is `modification_type` → `reasoning` → `edits`. Since the model
generates JSON tokens left-to-right, it commits to one type before generating the edit list, and
that commitment appears to bias which edits get generated at all — not just how they're labeled.

---

## 6. Multi-review combination question — clarified, not yet fixed

User question: if review 1 says "add 3 ice cream scoops" and review 2 says "add more eggs," does
the pipeline combine them? **No — confirmed by trace.** Exactly one review is ever selected per
run (`random.choice`, no loop, `tweak_extractor.py:136`). Reviews are never merged.

Important distinction surfaced this session, worth carrying into any fix of `docs/tasks.md` item 1
("process all flagged reviews, not just one"):
- **Multiple tweaks *within one* review** (e.g. the cream-of-tartar review, or "added an egg and
  halved the sugar") — one person tested this combination together. Decomposing and applying all
  of them together is reasonable.
- **Tweaks *across different, independent* reviews** — different people, never tested together.
  `RecipeModifier.apply_modifications_batch()` (`recipe_modifier.py:192`, currently unused) applies
  a list of modifications sequentially with **zero conflict detection** — if naively pointed at
  edits from unrelated reviewers, it would silently produce an untested "Frankenstein" recipe (e.g.
  one review doubles butter, another halves it — both would just apply in sequence). Any fix that
  processes all reviews needs to decide: apply all cumulatively onto one recipe (risky), or surface
  each review's modification(s) as separate, independently-attributed suggestions (safer, but a
  bigger product/UX change than the current one-recipe-per-run model).

---

## 7. Open questions captured for later validation

Written to `docs/thoughts.md` this session (not yet validated/prioritized):
1. Should review selection weight/filter by `Review.rating` instead of uniform `random.choice()`?
   `rating` is parsed and available but unused for selection.
2. Should extraction fall back to trying another candidate review if the randomly selected one
   fails (JSON/validation errors after retries), instead of failing the whole recipe? Confirmed no
   such fallback exists today.

---

## 8. Key file/line reference index

| Concept | File:Line |
|---|---|
| API key check, pipeline init try/except | `test_pipeline.py:29-40` |
| `output_dir` relative-path gotcha | `pipeline.py:31`, `:45-46` |
| Recipe/review parsing (reads `reviews`, not `featured_tweaks`) | `pipeline.py:71-114` |
| No-modification-reviews gate | `pipeline.py:142-144` |
| Random single-review selection, no fallback | `tweak_extractor.py:113-145` |
| LLM call (model, temp, response_format) | `tweak_extractor.py:66-74` |
| JSON parse + Pydantic validation (the real type-enforcement point) | `tweak_extractor.py:84-86` |
| Retry loop (same prompt, no feedback) | `tweak_extractor.py:66-111` |
| `modification_type` category descriptions (prompt) | `prompts.py:17-22` |
| `modification_type` category enforcement (schema) | `models.py:35-41` |
| `build_simple_prompt` (used) vs `build_few_shot_prompt` (unused, has decomposition examples) | `prompts.py:195`, `:159` |
| `apply_modification` — deepcopy + edit loop, not type-aware | `recipe_modifier.py:143-190` |
| `apply_edit` — fuzzy match + replace/add_after/remove | `recipe_modifier.py:65-141` |
| Silent fuzzy-match-failure drop | `recipe_modifier.py:60-63`, `:102-103` |
| `apply_modifications_batch` — unused, no conflict detection | `recipe_modifier.py:192-219` |
| `featured_tweaks` scraped but unused | `scraper_v2.py:236-260`, confirmed via grep against `pipeline.py` |

Cross-reference: `docs/architecture.md` (system-level writeup), `docs/tasks.md` (prioritized bug
backlog, P0 items 1 & 2 map directly to §3 and §6 above), `docs/thoughts.md` (open questions from
§7).

# Tasks — Prioritized Bug Backlog

Findings from reading `src/` end-to-end and re-running `test_pipeline.py all` live against all 6
sample recipes on 2026-09-04 (with a working `OPENAI_API_KEY`, model `gpt-3.5-turbo`). Ranked by
how much each one undermines the core product claim — "the enhanced recipe reflects real,
community-tested changes, accurately attributed" — not by how easy each is to fix. See
`docs/architecture.md` for the full component-level explanation behind each item.

Status: diagnosis only, nothing here has been fixed yet — this is the punch list to work from.

---

## P0 — Correctness, directly named in the assignment brief

### 1. Only one modification is ever extracted per recipe, from one randomly chosen review
`TweakExtractor.extract_single_modification()` does `random.choice()` over modification-flagged
reviews and calls the LLM once. `pipeline.py` never loops over reviews or calls this more than
once per recipe.

**Evidence:** chocolate chip cookies has 4 modification-flagged reviews; only 1 is ever used, and
which one is used changes randomly between runs (confirmed: two live runs on the same recipe
picked two different reviews and produced different output).

**Why it's P0:** this is the exact failure mode the assignment brief calls out by name — a recipe
with several tested community tweaks only ever surfaces one, chosen essentially at random,
discarding the rest of the signal the product exists to surface.

**Relevant existing code:** `RecipeModifier.apply_modifications_batch()` already applies a list of
modifications sequentially to one recipe — it's written and unused. The missing piece is mainly at
the extraction end (calling `TweakExtractor` per-review, or per-recipe over all flagged reviews)
and in `EnhancedRecipeGenerator`, which currently hardcodes a single `ModificationApplied` entry
in `generate_enhanced_recipe()` rather than accepting a list.

### 2. A single review's multiple discrete tweaks get merged into one `modification_type`
Even when one review is selected, `ModificationObject.modification_type` is a single `Literal` —
the schema and prompt force one category label even when the edits inside clearly span more than
one kind of change.

**Evidence (live run, nikujaga recipe):** one review ("added extra soy and sugar... measurements
were off... 1/4lb to 1lb of meat") was extracted as a single `quantity_adjustment` with 4 edits —
3 ingredient quantity changes plus a 4th edit that's actually an instruction-level note, not a
quantity adjustment at all.

**Evidence (static, cookies recipe):** the review "(1) half cup sugar / 1.5 cups brown sugar,
(2) omitted water, (3) added cream of tartar, (4) refrigerated batter" is at minimum a
`quantity_adjustment` + a `removal` + an `addition` + a `technique_change` — 4 different types,
each individually citable — but the schema can only ever emit one type per extraction.

**Why it's P0:** this is the other half of the brief's explicit example ("added an egg and halved
the sugar" = two discrete modifications) — it's not just about which reviews get read, it's about
correctly decomposing the ones that are read.

**Fix direction:** change `ModificationObject` (or the prompt) to return a list of typed
modifications per review, not one type for the whole review; the LLM call and validation already
support arbitrary edit lists, so this is primarily a schema + prompt change plus updating
`EnhancedRecipeGenerator` to fan a review's several modifications out into several
`ModificationApplied` entries instead of one.

---

## P1 — Correctness, affects trust in the "tested & attributed" claim

### 3. Failed edits are dropped silently, but attribution still claims the full change
`RecipeModifier.apply_edit()` logs `logger.warning(...)` and moves on when a `find` string doesn't
clear the 0.6 similarity threshold. Nothing about the failure reaches `ChangeRecord`,
`ModificationApplied`, or `EnhancementSummary`.

**Evidence (live run, nikujaga recipe):** the LLM proposed 4 edits; edit 4
(`add_after` targeting `"Top the mixture with the snow peas to serve."`) failed to match anything
in the recipe's instructions and was dropped. `enhancement_summary.total_changes` correctly says
3, but `modification.reasoning` ("adjusted the seasoning to enhance the flavor") and the source
review text still read as if the full suggested change was applied — a reader has no way to know
one of the four proposed edits silently never happened.

**Why it matters:** this is the same *shape* of bug the "hidden cost" writeup warns about
(defensive code that hides failure instead of surfacing it) — just implemented as a
similarity-threshold silent-continue instead of a `try/except: return []`. The fix is the same
principle: let it surface. A dropped edit should show up in the output (e.g. an `unmatched_edits`
list on `ModificationApplied`) rather than vanishing.

### 4. `has_modification` flagging is a noisy heuristic — high recall, low precision
The scraper's regex set (`I (added|used|substituted...)`, `(next time|will make again)`,
`(more|less|extra) (\w+)`, etc.) doesn't distinguish an applied change from a stated preference or
future intention.

**Evidence (live run, spicy apple cake):** the review actually selected and applied was "I would
prefer some more apple chunks in it, but that is just my preference" and "next time I will add one
more cup of apples" — a hypothetical, not a tested change — yet it was flagged
`has_modification: true`, selected, extracted as an `addition`, and applied to the recipe exactly
as if someone had already made and validated it.

**Why it matters:** this directly breaks the "community-*tested*" claim in the product framing —
the enhanced recipe can end up reflecting an untested hypothetical someone floated, presented with
the same confidence as a change someone actually made and reported on.

**Fix direction:** likely needs both a scraper-side tighten (distinguish past-tense "I did X" from
future/conditional "I will/would X") and an extraction-side check — the LLM call already gets the
full review text, so the prompt could explicitly ask it to flag/reject not-yet-applied statements
rather than assuming everything flagged by the regex is real.

---

## P2 — Scaling / robustness beyond the 5 examples

### 5. 2 of 6 sample recipes have zero scraped reviews and can never produce output
`recipe_284494_spiced-purple-plum-jam.json` and `recipe_45613_mango-teriyaki-marinade.json` both
have an empty `reviews` list. `pipeline.py` correctly no-ops on these (logs a warning, returns
`None`) rather than crashing, but it means 1/3 of the given sample set is dead on arrival — not
because of a pipeline bug, but because `scraper_v2.py`'s review selectors didn't find anything for
those pages.

**Why it matters for "does it scale":** any accuracy/coverage numbers computed from this sample
set need to account for the fact that a third of it structurally cannot succeed — and if this
selector-fragility generalizes to a wider recipe catalog, real coverage could be meaningfully
lower than the 4/6 "success rate" suggests.

**Fix direction:** worth checking whether AllRecipes' review markup actually differs for these two
pages (fixable selector gap) vs. these specific pages genuinely having no reviews (not a bug,
just a sparse-data case the product needs to handle gracefully, e.g. "no community tweaks yet"
rather than silent failure).

### 6. Few-shot prompt exists but isn't used
`prompts.py` defines `build_few_shot_prompt()` with 4 worked examples covering quantity
adjustments, additions+removals, and technique changes. `tweak_extractor.py` only calls
`build_simple_prompt()` (no examples) — a code comment says this is "to avoid format string
issues," suggesting the few-shot version had a bug that was worked around by dropping it rather
than fixing it.

**Why it matters:** items 1 and 2 above are extraction-quality problems, and few-shot examples are
a well-established, low-effort lever on exactly that kind of problem. This is a plausible quick
partial-improvement to try before or alongside a bigger extraction redesign — worth checking
`build_few_shot_prompt()`'s actual bug (likely an f-string trying to format a dict literal
containing `{}`) since it may be a one-line fix.

---

## P3 — Infra / hygiene (low risk, low effort, worth cleaning up)

### 7. `output_dir` is a relative path that silently writes to the wrong directory
`LLMAnalysisPipeline.__init__` defaults `output_dir="data/enhanced"`. The README instructs
`cd src` before running, so this resolves to `src/data/enhanced/`, not the top-level
`data/enhanced/` everything else (docs, `.gitignore`, the checked-in samples) assumes. Confirmed
live — running `test_pipeline.py all` from `src/` produced files in `src/data/enhanced/` (now
gitignored) instead of `data/enhanced/`.

**Fix direction:** resolve `output_dir` relative to the repo root (e.g. via `Path(__file__)`
ancestry) rather than cwd, or have `test_pipeline.py` pass an explicit absolute-ish path.

### 8. README says GPT-4o-mini; code hardcodes `gpt-3.5-turbo`
`TweakExtractor.__init__` defaults `model="gpt-3.5-turbo"`. `README.md`'s "How It Works" section
says extraction uses "GPT-4o-mini." One of these is wrong; low-effort to align, worth doing
alongside item 6 since model choice affects extraction quality too.

### 9. Checked-in sample output doesn't match what current code produces
`data/enhanced/enhanced_10813_best-chocolate-chip-cookies.json` has 2 modifications and
`confidence_score` fields that don't exist in `models.py` and that `pipeline.py` cannot currently
produce (single-modification only, no confidence scoring anywhere in the codebase). Likely a
stale/hand-authored aspirational example from before the current code was written, or written
against a since-reverted schema. Either regenerate it from the current pipeline once items 1-2 are
fixed, or clearly label it as a target/mockup rather than a real sample.

### 10. Dead/unused code worth being aware of before rebuilding equivalents
- `RecipeModifier.validate_modification_safety()` — pre-flight match validation, unused
- `RecipeModifier.apply_modifications_batch()` — sequential multi-modification apply, unused (see
  item 1 — this is likely directly reusable)
- `EnhancedRecipeGenerator.generate_comparison_data()` — UI-ready diff structure, unused (no UI
  exists yet, but relevant groundwork for later)
- `prompts.build_few_shot_prompt()` — unused (see item 6)

None of these are bugs by themselves, but not knowing they exist risks re-implementing them.

### 11. `docs/react_optimizor.md` is unrelated boilerplate
It's a React-rerendering-analysis prompt in a Python/backend-only repo — looks like leftover
scaffolding from a template, not project-relevant. Not touched here since deleting files wasn't
asked for; flagging so it doesn't get mistaken for real project context.

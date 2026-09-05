# Thoughts & Notes (Plain-Language Version)

This is a running notebook of ideas, questions, and problems found while working through this
project, written in the order things actually happened. It's meant to be readable by someone who
isn't a programmer — technical details (file names, line numbers) are kept in small "for
developers" notes so nothing is lost, but they're not required reading to follow the story.

---

## How this went, in order

Before touching any code, I spent time writing `CLAUDE.md` and the `docs/` files
(`architecture.md`, `business-context.md`) so that Claude would be aligned with the actual product
goal — a recipe that genuinely reflects trustworthy community changes — not just whatever narrow
task I happened to type in a given message.

From there, I read through the codebase and started testing it live, which raised two questions
right away, before I'd even hit what I'd call a real bug:

### Should the system prefer reviews with higher star ratings?

Right now, when the system picks a review to learn a recipe tweak from, it picks completely at
random out of all the reviews that mention a change — a 5-star review and a 3-star review have
the exact same chance of being picked.

Question: should a tweak from a review people rated highly be trusted more than one from a review
people rated lower? Or does the star rating not really tell you whether the *specific tweak*
worked, just whether the person liked the recipe overall?

*(For developers: `TweakExtractor.extract_single_modification()`, `tweak_extractor.py:136`. The
rating is already parsed and available — nothing stops us from using it, we just don't yet.)*

### If the chosen review doesn't work out, should the system try a different one?

Today, if the system picks a review and then fails to turn it into a usable change (for example,
the AI's response comes back malformed), the whole thing just gives up — even if there were three
other perfectly good reviews sitting right there that were never even attempted.

Question: should it fall back and try one of the other reviews instead of giving up entirely?

*(For developers: no fallback loop exists — confirmed by reading `tweak_extractor.py:136-145` and
`pipeline.py:152-154`. This may become less important if we fix "only one review is ever used"
more broadly, since then every review gets a chance anyway.)*

---

## 1. Direction Hint

Before testing, I'd read through and thought about this hint:

> Are we certain that the system parses out ALL the intended modifications? E.g. If the review
> says "I added an egg and halved the sugar" -> these are two discrete modifications!

> Does the system scale beyond the 5 examples we gave? Are there poor assumptions embedded in the current implementation?
--> Fix 2: code assumes each instructions represents one atomic fact. Solution: we created a syntehsizer node / layer.

Sure enough, once I actually ran the pipeline, this is exactly the kind of issue that showed up.

**Fix 1 — A review describing several changes was only ever recorded as ONE change**

The problem: if a review said "I added an egg and halved the sugar," that's two separate changes.
But the system could only ever store one "type" of change per review, so it would either mislabel
one of the two, or just drop it.

What we changed: updated the data structure so the system can record a *list* of separate changes
instead of being boxed into one, and told the AI explicitly (in its instructions) to list out
every distinct change on its own instead of merging them together.

**Status: Done.** Tested directly with the exact "added an egg and halved the sugar" sentence — it
now correctly comes back as two separate, properly labeled changes.

*(For developers: new `ModificationExtractionResult` wrapper in `models.py`; prompt and JSON
schema updated in `prompts.py`; `pipeline.py` now loops through the list via
`RecipeModifier.apply_modifications_batch`.)*

---

## 2. Fix 2 — Splitting up recipe steps that were secretly doing two jobs

While testing Fix 1, a new issue turned up: if a single instruction line contains two actions, the
AI can end up removing both of them together — the second action becomes collateral damage of
removing the first, purely because they happen to live in the same line.

Concrete example: a recipe step read *"Dissolve baking soda in hot water. Add to batter along with
salt."* That's really two separate instructions squeezed into one array item. A review said "I
left out the water" — the system correctly understood this meant *remove* something, but because
both instructions were glued together, removing the water part silently deleted the salt part too.
Nobody told it to touch the salt, but it disappeared anyway. Nothing about this looked like an
error — every log line said "success."

What we changed: added a clean-up pass that runs *before* the AI even looks at making changes. It
goes through the recipe's steps once and splits any step doing two unrelated things into two
separate, complete steps that each make sense on their own.

**Status: Done.** Re-tested the exact case above — the salt instruction no longer disappears when
the water gets removed.

*(For developers: `RecipeModifier.apply_edit`'s `remove` branch, `recipe_modifier.py:123-139`,
deletes a whole list item, not a piece of text within it — that's the root cause. New
`InstructionSynthesizer` class + `NormalizedInstructions` model, wired into `pipeline.py` right
after loading the recipe, before extraction. Runs once per recipe, only changes the in-memory
copy, never touches the source `data/recipe_*.json` file.)*

---

## 3. Fix 3 — Checking the other list before giving up

Testing Fix 2 surfaced yet another issue. In the results, I noticed an edit had been labeled as
belonging to the ingredients list, but the actual text it was trying to find only existed in the
instructions list. Since the system only ever looked where it was told to look, it found nothing
and dropped the change — a community-suggested tweak (adding cream of tartar) didn't make it into
the final recipe. Unlike Fix 2's issue, this one wasn't silent — the system correctly logged that
it couldn't find the text, it just didn't know to look anywhere else.

What we changed: if the system can't find something where it was told to look, it now checks the
*other* list before giving up. A very strong match there (not just a plausible one) means it uses
that instead of throwing the change away; still no match anywhere means it drops it, same as
before.

**Status: Done.** Verified directly with the exact broken edit from testing — the system now
correctly recovers it instead of dropping it.

*(For developers: `RecipeModifier.apply_edit` now receives both `ingredients` and `instructions`.
A match of `≥ 0.9` similarity in the other list triggers the fallback — deliberately stricter than
the normal `0.6` threshold, since this overrides what the AI explicitly declared rather than just
confirming a plausible line. Also added a short reminder in the AI's instructions to double-check
it's naming the right list.)*

---

## 4. Things I Pushed Back On

Related to Fix 3: while building it, the fix also started recording every dropped or corrected
edit inside the saved recipe file itself, in case an edit couldn't be matched anywhere. I hadn't
actually hit that case yet — it was added on the assumption it *might* happen, not because it had.

I pushed back on this. Our existing guardrails (the matching threshold, the fallback check itself)
already handle the situation correctly by dropping the edit and logging it; adding a whole
extra field and code path to record something we hadn't yet observed is scope creep — noise
outside of what we actually set out to fix. A fix should stay focused on the problem it's solving,
not preemptively build handling for cases we're only guessing might occur. It was removed, keeping
only the part that actually fixes the wrong-list problem.

---

## Should this be a fixed checklist of fixes, or a workflow of specialized "nodes" checking each other's work?

Core idea: **deterministic validation pass first, LLM second.**

Right now, every new failure mode gets patched with another specific rule bolted onto one fixed
pipeline (split compound steps, check the other list, and so on) — each fix reasonable alone, but
each also risking a new edge case we hadn't planned for, which is exactly the pattern this session
kept hitting.

Alternative worth considering: a small workflow of specialized steps that check each other's work
instead of one growing list of special cases — e.g. an extraction step, then a separate
cross-checking step whose only job is asking "does this change hang together across both
ingredients and instructions, whatever type of change it is?" (Tools like LangGraph exist for
wiring up this kind of multi-step workflow.) The hard, deterministic rules we already have — exact
matching, the similarity threshold — would stay exactly as they are; this would just add a general
review layer on top instead of one rule per bug found. Not something to build immediately — a
genuine tradeoff between more moving parts and fewer bugs discovered one at a time — but worth
naming.

## Additional Improvements:
> Add tests that handles both happy paths and edge cases

---

Hi team,

Happy to know your thoughts:
- Where things are great
- Thoughts on my take to future improvements
- Where things can be improved (is my solution reasonable)
- Do you agree / disagree with my decisions, e.g., Logic first, LLM second (for complex cases mentioned above) 

Either way, thanks for this assignment, it was fun! :)

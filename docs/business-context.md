# Business Context

What problem this solves, for whom, and the constraints shaping how we work on it. See
`docs/architecture.md` for how the system works and `docs/tasks.md` for what's currently wrong
with it.

## The problem

AllRecipes (and sites like it) accumulate huge amounts of review text under every recipe, and
buried in that text is genuinely valuable, community-tested signal: people who made the recipe,
changed something specific about it, and reported the result. AllRecipes surfaces some of this
manually as "Featured Tweaks" — but it's editorial/manual, not systematic, and a reader still has
to read a wall of reviews to find the useful ones.

The product goal: automatically mine reviews for concrete, tested modifications, apply the
highest-value ones to the base recipe, and show the reader an **enhanced recipe** with a
line-level diff — exactly what changed, and a citation back to the review that suggested it, so
the improvement is trustworthy (traceable to a real person who actually made it) rather than an
opaque AI rewrite.

## Who this is for

End users cooking from the enhanced recipe, who want:
- A recipe that already incorporates what the community learned works better, without reading
  every review themselves
- Confidence that a given change is real and tested (rating attached, reviewer attached, source
  text visible) rather than the model inventing an "improvement"
- To see *why* a change was made (the reasoning) and be able to inspect or reject individual
  changes, not just accept a black-box rewrite

That last point is why citation tracking and line-level diffs are core to the product, not a
nice-to-have — the value proposition is "community-validated," and that claim only holds if the
attribution is accurate down to the individual edit.

## Where this project stands

This is a simulated onboarding scenario: a jr. engineer already built a first pass end-to-end —
scraper, 3-step LLM pipeline, sample data for 6 recipes, and a manual test script — before
handing it off. The codebase is largely AI-generated and has not been seriously reviewed.

The explicit ask (from the assignment) is **not** to build a UI or deploy anything yet. It's to
answer: *does this pipeline actually work, beyond a couple of superficial examples?* Two questions
were flagged as the ones to focus on:

1. Does the system parse out **all** the intended modifications from a review (a review
   describing "I added an egg and halved the sugar" is two discrete modifications, not one)?
2. Does the system scale beyond the 5 given examples — what assumptions in the current
   implementation break down as review text, recipe types, and volume vary?

Both turned out to be "no" in ways confirmed by re-running the pipeline live — see
`docs/architecture.md` ("Known limitations", "Scaling beyond the 5 examples") and
`docs/tasks.md` for the specific, evidenced findings and what to prioritize fixing first. This is
deliberately a "budget your attention" exercise, not a request to fix everything — a few real,
verified correctness issues are worth more here than broad, shallow polish.

## Constraints on how we work

- **Diagnose before building.** Confirm what's actually broken (and why) before writing fixes,
  and confirm fixes against real re-runs, not just code reading — the jr. engineer's code *looks*
  plausible at a skim (it's well-organized, docstringed, type-hinted) and that's exactly why it
  needs to be traced through rather than trusted on sight.
- **Attribution must stay accurate.** Any fix that changes extraction or matching must not weaken
  the citation/diff guarantee — a change applied to the recipe must always trace back to the real
  review text and reasoning that produced it, and a change that was proposed but not actually
  applied must not be silently reported as if it happened.
- **Small, real sample set.** 6 recipes, several with only 1-2 reviews and 2 with none at all.
  Conclusions and fixes need to hold up across all 6, not just the chocolate-chip-cookie happy
  path the test script defaults to.

## Deliverables this work feeds into

Per the assignment: a private GitHub repo (this one), a written doc covering assumptions/problem
analysis/technical decisions/challenges/future improvements, a 5-7 minute video walkthrough, and
this coding-agent trajectory committed to the repo. Evaluation weighs correctness/tech-debt
handling, code quality, product thinking, and communication roughly evenly — which is part of why
these `docs/` files and `CLAUDE.md` exist: the reasoning and evidence behind decisions needs to be
legible to someone else, not just encoded in a diff.

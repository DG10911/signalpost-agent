# JBOX bonus — $500 execution plan

The Signalpost page lists a **separate $500 JBOX bonus** ($250 / $150 / $100) "for
qualifying agents built with JBOX", and the winning builder gets to partner with
**Håvard Liltved Dalen** (CPO at Fronted, co-founder of JBOX) to launch
Signalpost in Norway. This is a **parallel play to the main $2,000 prize** — do
not trade the main entry for it.

## The two gates (both required)

1. **Qualifying** — the *agent* must score **≥ 65/100** on an official run. (We
   are not there yet; recall is the gap. JBOX alone earns nothing without this.)
2. **"Built with JBOX"** — the *scored agent* must plausibly be built with JBOX.
   The public page gives no technical criterion, so this is the risk.

## The honest problem

JBOX (jboxai.com) is a **browser no-code builder** that generates a hosted
Next.js + Supabase **product UI**. It is **not** something we can drive from this
CLI, and it does **not** build the Python crawler that is actually scored. So:

- A JBOX-built **interface over our engine** is a *reasonable read* of the bonus
  (a product "built with JBOX"), but it is **not certain** the graders accept it —
  an interface-only wrapper may not count if they mean the *agent itself*.
- Re-implementing the **agent** on JBOX (a Next.js/Supabase crawler) is a large
  rewrite with real regression risk to the one thing that matters (the ≥65
  qualification).

**Therefore: gate the effort on a written answer first.**

## Step 1 — ask Soham (do this now; costs nothing)

**To:** submit@builderr.ai · **Cc:** writetosoham87@gmail.com
**Subject:** Signalpost — JBOX bonus eligibility

> Hi Soham,
>
> On the $500 JBOX bonus for Signalpost: what does "built with JBOX" concretely
> require for an entry? Specifically, does the **scored agent** have to run on
> JBOX, or does a **JBOX-built interface** over an otherwise-independent agent
> qualify? And is the bonus only available to entries that first reach the 65
> qualification bar?
>
> I ask because the scored component is a Python collection/evidence engine, while
> JBOX generates the Next.js/Supabase product layer. If a JBOX-built UI over my
> engine qualifies, I'll build it; if it means re-implementing the scored agent on
> JBOX, I'll pass and keep my effort on qualification. Thanks,
> Devansh Goenka

## Step 2 — build the JBOX UI (only after a favourable answer)

The brief is **already written and paste-ready** in `JBOX-BRIEF.md`: users/roles,
5 screens, Supabase schema, product rules, visual direction, and the exact
"Copilot prompt" to paste into jboxai.com. It builds the company-intelligence UI
over our engine's `out/submission-contract.jsonl` export (claims + evidence +
six availability states + refresh history). This is **the cheaper, lower-risk
path** and is stack-aligned with Soham's UX ask.

## Step 3 — sequencing (so we never risk the main entry)

1. **Now:** send the eligibility question (Step 1).
2. **In parallel:** finish the main qualification play — the request-efficient
   resubmission (`ad8c2f7`) so rev-4's news/hiring/socials breadth finally scores.
   JBOX is worthless unless we clear 65.
3. **After a favourable answer AND a scored ≥65:** paste `JBOX-BRIEF.md` into
   JBOX, wire the seed from the engine's JSON export, and submit the hosted URL as
   the bonus entry.
4. **If the answer is "the agent itself must be JBOX":** pass on the bonus; the
   rewrite risk to the main $2,000 is not worth $500.

## Why this ordering

- The main prize ($2,000) and qualification depend on the Python engine, which
  JBOX cannot build. Never jeopardise it.
- The bonus is gated behind the same 65 bar, so it cannot be won before
  qualification anyway.
- The eligibility question is free and removes the guesswork before any spend.

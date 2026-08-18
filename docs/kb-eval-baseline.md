# KB retrieval — recorded baseline

Produced by `scripts/kb_report.py` (plan [kb-revision-2026-08.md](plans/kb-revision-2026-08.md), S0/KB-01).
Read-only over `retrieval_log`, which has recorded per-fact cosine `sim` for
`source='kb'` since migration 022.

---

## 2026-08-17 — first measurement, production

Window 2026-08-06 → 2026-08-17 (11 days, the full age of KB traffic in the log).

```
KB lookups recorded : 41
...returned nothing : 17 (41.5%)   — already blind, no floor can worsen these
...returned facts   : 24
facts returned      : 48           — exactly 2 per lookup

Similarity percentiles
  p    best-per-turn   all facts
   10          0.503       0.481
   25          0.531       0.504
   50          0.544       0.531
   75          0.566       0.546
   90          0.578       0.567
   95          0.580       0.578

Floor sweep
  floor   facts kept    facts cut    turns newly blind
  0.40           48       0 (  0.0%)       0 (  0.0%)
  0.45           48       0 (  0.0%)       0 (  0.0%)
  0.50           39      9 ( 18.8%)        0 (  0.0%)
  0.55           12     36 ( 75.0%)       14 ( 58.3%)
  0.60            0     48 (100.0%)       24 (100.0%)
  ...            0     48 (100.0%)       24 (100.0%)
```

Corpus at time of measurement: **2 facts total**, in 1 chat, 0 with `topic`,
0 with a NULL embedding. Four chats issued KB lookups — so three of them have
the KB enabled and no facts at all, which is the 41.5%.

### What this says

**The knowledge base has never contributed a relevant fact in production.**
Both facts come back on every lookup that returns anything (48 = 2 × 24), and
the highest similarity ever recorded is 0.588. For this embedding model,
0.45–0.59 between a short query and an unrelated short text is baseline
noise, not a match. Every one of those 48 injections was presented to the
model under the header "Curated Knowledge Base facts for this chat
(authoritative, current)". Defect D-5 in the plan, now with numbers.

### What this does NOT say, and why it matters

**The floor cannot be calibrated from this data, and shipping a number from
it would be false precision.** With a 2-fact corpus the sweep has no signal:
every floor in [0.60, 1.00] behaves identically (cut everything) and every
floor at or below 0.45 behaves identically (cut nothing). The measurement
cannot distinguish "the right threshold" from "just above this corpus's
ceiling" — and a corpus of two is a ceiling, not a distribution.

**Consequence for the plan: capture must precede the floor.** S0 was ordered
first so S4 would not ship a guess; the honest reading of its own output is
that S4 cannot be calibrated *yet*, from anything. The order becomes
S1 → S2 (capture) → re-run this report → S4 (floor). Until a real corpus
exists, the floor is guesswork whichever direction it is argued from.

Re-run before setting `min_similarity`:

```
python -m scripts.kb_report <dsn> --since-days 90 --markdown
```

---

## What S2 changed under this measurement (2026-08-17, not yet re-measured)

The line above was recorded against the Phase-1 capture path. S2 changed that
path, so **the next run of this report is not comparable to the one above** and
must be read as a new baseline rather than as a delta. What moved:

- **Capture is append-only** (ADR-0012). Before, a second `/remember` about one
  subject superseded the first, so the corpus could not grow past one fact per
  subject — which is part of why production held two facts. A growing corpus is
  the whole point: the floor is calibrated from it.
- **Retrieval reads an exact index** since S1 (`ivfflat.probes = lists`), so
  similarity figures are no longer partly a function of which partition a fact
  landed in.
- **The prompt header changed** and no longer claims "authoritative, current",
  so the sentence quoted above describes the *old* injection wording. An expiring
  fact now also renders its date to the model.
- **`expires_at` is written for the first time**, which means the live-fact set
  can now shrink on its own. A fact that stopped being injected is no longer
  necessarily evidence of a retrieval change.

**Do not re-run this report immediately after the deploy.** It reads
`retrieval_log`, which needs turns *after* a corpus exists; a run against a
2-fact corpus reproduces exactly the "no signal" state described above. Judge
readiness from the corpus, not the calendar: `SELECT count(*) FROM chat_facts
WHERE status = 'active' AND valid_to IS NULL` in the tens, across more than one
subject, before the sweep can distinguish anything.

---

## 2026-08-18 — first measurement with a real corpus

A production group chat was seeded with 33 hand-written facts through the real
`/remember` path (so every one carries an embedding; a direct `INSERT` would
leave `embedding = NULL` and be invisible to `search_by_similarity`). Corpus
after seeding: **36 live facts** in that chat, 8 topics, lengths 46–233 chars
(none near the 600-char render cap), 4 carrying `expires_at`.

Five addressed turns were then issued deliberately to span the range. Verbatim
from `retrieval_log`, `source='kb'`, all with `n_results = n_injected = 5`:

```
query                                    top sim   bottom sim
what is <a recurring chat event>?          0.706        0.563   ← intended hit
why does everyone call each other X?       0.793        0.658   ← intended hit
(a statement, not a question)              0.646        0.608   ← no topic match
how do you cook borscht?                   0.640        0.586   ← intended miss
what is bitcoin worth right now?           0.630        0.586   ← intended miss
```

**Noise ceiling 0.646. Signal floor 0.706. Gap 0.060.** No overlap — the
separation is clean, but narrow, and n = 5.

> **Measured before R0 (TD-092), and the conditions have since changed.** Every
> one of those five turns was addressed, so every query embedding carried the
> leading trigger word — which the paragraph below names as the reason a miss
> returns bot-about-bot facts. Retrieval now strips that address before
> embedding, so this window is a record of a regime that no longer runs.
>
> The direction is favourable and worth stating plainly, so that nobody "fixes"
> the floor on the strength of the narrow gap above: on the same production
> data, removing the address moved a hit from 0.706 to **0.719** and a miss from
> 0.640 down to **0.524**. Both edges move away from 0.70, so the gap widens
> from 0.060 to roughly 0.195 and the shipped floor sits further inside it than
> when it was chosen. **0.70 needs no change; it needs re-measuring**, on a
> fresh window of addressed turns, before anyone tunes it.

### What this changes versus the 2-fact line

**0.588 from the previous section must not be reused as a threshold.** It was
the *ceiling of a 2-fact corpus*, and the noise floor rises with corpus size:
with 36 facts an unrelated question now reaches 0.640. Comparing the two
numbers directly compares different quantities.

**On a miss the retriever returns the same handful of self-referential facts
every time.** The three low-scoring turns returned near-identical id sets,
dominated by facts *about the bot itself*. Every addressed message begins with
the trigger word, so with no real topical match the query embedding is closest
to the facts that talk about the bot. Low-similarity injection therefore does
not supply varied colour — it supplies the same five rows regardless of the
question.

### The floor: 0.70, copied rather than invented

`rag.min_similarity` is already **0.70** (`config/default.yml`), wired through
`settings.rag` into `MemoryRepository`. It is the same embedding model, the same
768-dim cosine space, and it has been in production for months. In this same
five-turn window RAG returned **0 results on every turn** — which is that floor
working, not a fault.

That number lands inside the measured gap without being tuned to it. KB is the
only retrieval subsystem in the project with no floor at all, so the fix is to
give it the one that already exists, not to derive a second one from five points.

> **Owner decision, 2026-08-18: by default answer *without* the knowledge base;
> attach facts only when there is a topical match.** No match → the KB block is
> not added to the prompt at all.

Implementation notes that follow from this measurement:

- **Filter at injection, not at selection.** Keep fetching the top 5 and keep
  writing all 5 to `retrieval_log` with their similarities, marking sub-floor
  rows `injected: false`. Filtering earlier would stop the log from recording the
  noise band — i.e. destroy exactly the data needed to re-tune the floor later.
- **Full rollback is `min_similarity = 0.0`**, restoring today's behaviour without
  a revert.
- **The hit at 0.706 clears the floor by 0.006.** A slightly worse phrasing of the
  same question would be cut. Re-measure once the chat has lived with the corpus.

### One misattribution, recorded because it was convincing

The off-topic answers were laced with chat in-jokes, which read as the knowledge
base polluting them. It was not: the fact ids actually retrieved on those turns
contained none of that material, the chat's custom `system_prompt` (613 chars)
does not mention it, and RAG returned nothing. The only remaining channel is the
recent-message history in the user prompt — the chat's own lore, which the bot
sees regardless of the KB. **A plausible story about which subsystem caused an
output is not evidence; check what that subsystem actually returned.**

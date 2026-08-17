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

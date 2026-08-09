# Eval case fixtures

`cases.json` is a **tracked, synthetic** template for the RAG eval harness
(slice S3 of `docs/plans/rag-revision-2026-08.md`). All chats, message ids and
questions are made up — no real chat content.

It exists so the case schema is visible and reviewable in a public repo. The
**real** golden set (S3b / Q10) lives at `internal/eval/cases.json`, which is
gitignored (`internal/` is gitignored wholesale) because it carries real chat
quotes and ids.

Both files are validated by the same model, `scripts/eval_schema.EvalCase` —
see that module's docstring for the field-by-field contract (in particular:
`asked_at` is required and timezone-aware, and `expected_message_id_ranges`
must be empty for `stratum="answer-absent"` and non-empty otherwise). One
validator for both files on purpose, so the template cannot drift from the
real schema.

Validate by hand:

```bash
python3 scripts/eval_schema.py tests/fixtures/eval/cases.json
python3 scripts/eval_schema.py internal/eval/cases.json   # once it exists
```

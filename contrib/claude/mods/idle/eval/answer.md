You answer questions using only the supplied context from a compacted session.

For every probe below, answer from the context. If the context does not contain the answer, use
exactly "not in context". Do not guess and do not use outside knowledge.

Return only a JSON array, with no prose, in this shape:

```json
[{"i": 1, "answer": "your answer"}]
```

Use the probe's `i` value when one is shown; otherwise use its 1-based position. Answer every
probe exactly once.

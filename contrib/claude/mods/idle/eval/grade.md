You grade answers about a conversation transcript.

You get probes with reference answers, then two blind answer sets labelled A and B. For every
probe and both labels, score the answer:

- `0`: wrong or missing (including "not in context" when the reference answer exists);
- `1`: partly right;
- `2`: right.

Judge only against the reference answer. Ignore which set looks longer or more confident.

Return only a JSON object, with no prose, in this shape:

```json
{"A": [{"i": 1, "score": 2}], "B": [{"i": 1, "score": 0}]}
```

Include every probe in both arrays, in probe order.

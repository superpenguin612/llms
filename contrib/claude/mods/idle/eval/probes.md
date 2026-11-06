You make probes for an evaluation of conversation compaction.

The transcript follows. Write about 15 questions that a fresh session would need answered to
continue the work. Every answer must be checkable from the transcript. Prefer facts from the
earlier part of the conversation, because that is the part a summary has to carry.

Use these kinds:

- `decision`: what was decided and why;
- `user_words`: a request, preference or correction in the user's own words;
- `running`: background work, servers, jobs or sessions that are running;
- `next_step`: the next concrete action;
- `files`: paths, commands or other concrete identifiers;
- `other`: anything else important.

Return only a JSON array, with no prose, in this shape:

```json
[{"q": "question", "answer": "reference answer from the transcript", "kind": "decision"}]
```

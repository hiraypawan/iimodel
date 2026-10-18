# Human-review evaluation harness

This directory contains an evaluation-task schema and a review-packet generator.
`eval_questions.json` is intentionally empty until questions, ground truth, and
source URLs receive human review. No benchmark questions, quality score, or model
result are fabricated here.

Each response file contains one JSON object per task with `task_id` and
`response`. The evaluator can create a single-system packet or a side-by-side
raw-model versus II packet from user-supplied responses:

```bash
python main.py benchmark \
  --dataset tests/benchmarks/eval_questions.json \
  --raw-responses raw.jsonl \
  --ii-responses ii.jsonl \
  --raw-model "raw-model-id" \
  --ii-model "ii-model-id" \
  --settings "matched prompt and decoding settings" \
  --output tests/benchmarks/results/comparison.json
```

The CLI does not run model inference. It reports only mechanical signals such as
response presence, URL presence, exact gold-URL matches, and abstention phrases.
It does **not** judge factual correctness, whether a citation supports a claim,
or whether abstention was appropriate. Those fields remain pending human review;
URL string matches are not source verification. The packet records a dataset
hash and requires matched model/prompt/settings for a meaningful comparison.
Keep result packets out of Git unless intentionally publishing an evaluation.

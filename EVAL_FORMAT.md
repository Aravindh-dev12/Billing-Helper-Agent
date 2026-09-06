# Eval set format (Task 1)

Put your own questions in `eval/questions.jsonl`, one JSON object per line:

{"id": "Q01", "question": "...", "expected_status": "answered", "expected_answer": "...", "expected_sources": ["path/in/corpus.md"], "notes": "why this question is here"}

Provide `eval/run.py` (or equivalent) that posts each question to your running service and
writes `eval/results.md` with, per question: got status, got answer, citations, your
judgement (pass / partial / fail) and a one-line reason.

Summarise in `EVAL.md`: pass rate by status type, the questions that failed and why, and
what you changed as a result (link to ITERATIONS.md entries).

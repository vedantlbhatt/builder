"""Session analysis: a digest of one session, read by the user's own Claude Code.

    python -m analysis digest  <transcript.jsonl> [--start ISO --end ISO]   # print the digest
    python -m analysis run     <transcript.jsonl> [...] --out analysis.json  # digest + claude -p
    python -m analysis stats   <transcript.jsonl>                             # deterministic numbers only

Reference implementation. The Swift agent builds the same digest from its store
(BuilderAnalysis/Digest.swift) and the two must agree on the fixtures under
spec/fixtures/analysis/. The prompt and the output schema live here and nowhere else.
"""

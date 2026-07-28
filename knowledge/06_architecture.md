# Architecture

Command Request
→ Constitutional Gate
→ Builder Rights Gate
→ Consent and License Gate
→ Authority and Mandate Gate
→ Safe Harbor Evaluation
→ Risk and Consequence Analysis
→ Execution
→ Witness Record
→ Value Distribution
→ Outcome and Repair Review

Core engineering invariants:
- Command Bus is the sole mutation authority.
- State changes are event sourced.
- External effects use a transactional outbox.
- Witness records are immutable and hash-verifiable.
- Execution is deterministic where feasible.
- Privileged operations require explicit authority.
- Policy evaluation precedes execution.
- High-impact actions require human approval.
- Evolution is proposal-driven and evidence-based.

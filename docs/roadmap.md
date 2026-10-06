# Release gates and open questions

## Implemented alpha

Acceptance: one command runs an offline sample, preserves raw evidence, extracts labels, and renders a report. Re-running resume adds no duplicate committed turns. Retry and request ceilings survive restarts. Missing/unknown trials are disclosed and incompatible comparisons fail closed. Offline tests exercise these behaviors.

## Before treating v0.1 as measurement-ready

1. **Provider integration:** run a small paid smoke test against both providers with explicitly chosen models; inspect search configuration, returned model/usage, refusals, incomplete responses and replayed histories. Record fixture provenance and drift.
2. **Extraction validity:** assemble at least 150 real response examples with permission; double-annotate entity resolution and recommendation level, adjudicate disagreements, publish precision/recall and abstention rates by slice. Choose release thresholds before looking at the evaluation scores.
3. **Benchmark realism:** publish a versioned real-entity dataset with verified aliases, geography, sampling rationale, task constraints and held-out prompts. Compare synthetic journeys with real-user elicitation. Document selection bias.
4. **Experiment rigor:** add randomized or interleaved comparisons, richer compatibility checks, explicit intervention metadata and sensitivity analyses for missingness and weights.
5. **Usability:** add richer candidate review and entity citation attribution only after their evidence contracts can be tested. Add Windows locking and provider rate-limit handling if needed by early users.

## Product wedge and defensibility hypotheses

Start with auditable entity-discovery studies for one vertical, such as local hospitality. A useful early deliverable is a report that shows which profiles, locations and conversations lose a business, alongside the actual answers that explain the result.

Potential defensibility comes from a credible benchmark, human-labeled evaluation data, longitudinal evidence, domain-specific journey quality, and community trust in measurement definitions. None of these moats exists merely because this code is open source. Provider wrappers and a dashboard are readily reproducible.

Open questions: Who will pay for diagnosis versus recurring measurement? How well do API runs track consumer-facing recommendations? Which profile distribution corresponds to actual customers? Can intervention effects be separated from model drift? How much extraction uncertainty is acceptable? What is the cost of enough repeats? Which benchmark data can be published responsibly?

## Scope boundaries

No hosted SaaS, auth, billing, polished web dashboard, scheduling, SEO audit, content generation or automatic optimization is included. A public benchmark and validated semantic extraction are release gates, not features claimed by this alpha.

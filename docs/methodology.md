# Measurement contract

## Estimand and unit

The primary rate is the probability that a configured entity is recommended at least once in a **completed, resolved journey**, under the configured distribution of tasks, profiles and contexts. It is not an unconditional estimate of general AI visibility.

Each stratum is task × profile × context. Profile weights are normalized; tasks and contexts receive equal weight. Variants within a stratum receive equal weight. Each journey's repetitions are averaged before strata are combined. This stops additional repeats of a single journey from masquerading as additional independent users.

A failure, incomplete provider result, ambiguous entity identity or missing observation excludes that entire execution from the corresponding rate. A completed refusal is a valid negative response. Missing counts are displayed. A stratum with no evaluable journey suppresses the estimate rather than silently redistributing its weight. Within a partially missing stratum, the rate describes available cases and may be biased by nonrandom missingness.

## Uncertainty

The seeded bootstrap resamples journeys within each stratum, retaining each journey's repeat average. Percentile intervals use 2.5% and 97.5% quantiles. They are unavailable when there are fewer than the configured minimum journeys, fewer than two evaluable journeys in any stratum, or a degenerate bootstrap distribution. A suppressed interval is not evidence of certainty.

These intervals reflect empirical journey variation. They do not quantify uncertainty in the persona distribution, semantic extraction, synthetic-user fidelity, API drift, or conditional variation of repeated live calls beyond the saved repeat averages. Small designed template sets are not representative population samples.

## Recommendation levels

`none` → `mention` → `candidate` → `recommendation` → `strong_recommendation`.

The rules baseline uses exact full names or unambiguous aliases and nearby English cue phrases. Explicit negative cues can produce a mention with negative evidence, not a recommendation. Conflicting positive/negative evidence yields an unknown recommendation label. Short aliases and shared aliases abstain. No classifier confidence is fabricated.

Source annotations are preserved from providers. Retrieved search sources are distinct from cited answer sources. Entity citation attribution and rank remain null. The extractor does not infer rank from list position or assign semantic meaning to arbitrary numeric scores.

Review imports are a JSON list:

```json
[{
  "execution_id": "x_example",
  "turn_index": 1,
  "entity_id": "coastal",
  "level": "recommendation",
  "reviewer": "reviewer-id",
  "evidence": [{"start": 12, "end": 32, "text": "exact response slice"}]
}]
```

Offsets are zero-based Python Unicode string indices, end-exclusive. Replace the example with actual evidence; imports validate every slice. Use `none` with an empty evidence list for an adjudicated absence. Original batches remain intact. The alpha records reviewer identity but does not implement double annotation, agreement metrics, or adjudication queues.

## Journey generation

Frozen bundles contain the full task, profile, context, opening prompt, variant and policy version. All seven profile dimensions affect explicit prompt wording. The four opening templates are finite; sample counts must cover every stratum and cannot exceed available variants. Mixed mode alternates single- and multi-turn variants. It does not guarantee a mode balance when selecting only part of the pool.

The follow-up policy can answer a narrow budget/location clarification; otherwise it asks about fit, strongest choice, and value, to a maximum of four turns. This is a bounded scripted simulator, not a model of all human conversation behavior. It never stops because the target has been recommended. Target/cohort name leakage is rejected unless a task explicitly allows names.

## Ancillary metrics

- Geographic bands use straight-line distance between configured coordinates. They do not imply provider geolocation or travel distance.
- Multi-turn summaries use fully observed conversations: first surfacing turn, discovery after initial absence, final retention among initial recommendations, persistence across eligible adjacent turns, and survival through all refinements.
- Recommendation sets include only configured entities. Unknown entity observations exclude a set. Jaccard stability is computed over repeat pairs for the same journey; both-empty pairs are counted separately.
- Share is the target's entity-execution recommendation count divided by total cohort entity-execution recommendation counts. Multiple recommended entities in an execution each count once.
- Multi-turn, share and Jaccard summaries are unweighted descriptions. No independence across turns or repeat pairs is assumed; no interval is supplied for them.

## Comparisons

Comparisons require identical frozen bundle, entity cohort, repetitions, analysis settings and extraction version. Engine changes require explicit engine selection. Only matched, fully evaluable journey/repetition pairs contribute; excluded pairs are reported. The same stratified cluster bootstrap estimates the difference in recommendation rates. A value of 0.10 means +10 percentage points. A before/after association cannot isolate an intervention from time or provider changes.

The alpha does not provide randomized intervention assignment, causal identification, multiple-comparison correction, user-population calibration, or real-world conversion measurement.

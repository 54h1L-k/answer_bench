# Architecture

```text
YAML configuration → validation → frozen journey bundle
                                      ↓
                              plan / request ceiling
                                      ↓
                         isolated session runner
                                      ↓
                Fake / OpenAI Responses / Perplexity Agent
                                      ↓
                      SQLite raw turns + attempts
                                      ↓
                      versioned extraction batches
                                      ↓
               metrics / paired comparisons / HTML + JSON
```

`Entity`, `UserProfile`, `Task`, `Context`, `Journey`, and `Engine` are validated JSON definitions saved with each run. `Observation` is a versioned label with exact evidence spans. `Run` captures the procedure, outcomes and evidence. The alpha's `Experiment` is a saved paired comparison; multi-arm experiment orchestration remains a later milestone.

## Modules

| Module | Responsibility |
|---|---|
| core | Config validation, canonical JSON, hashes, normalized response |
| journeys | Stratified template sampling and bounded follow-ups |
| engines | Credentials, HTTP transport and response normalization |
| runner | Request reservation, retry limits, persisted history, recovery |
| storage | SQLite schema, transactions, run lock and evidence hashes |
| extraction | Rules baseline, candidate queue, append-only review batches |
| metrics | Defined estimands, bootstrap, slices and matched comparisons |
| reporting | Escaped HTML plus machine-readable exports |
| cli | Explicit command boundaries and exit statuses |

## Storage

SQLite enables foreign keys and WAL. The schema stores `runs`, `definitions`, `executions`, `turns`, `attempts`, `extraction_batches`, `observations`, `candidates`, and `comparisons`. Immutable definition snapshots share a typed `definitions` table rather than separate tables per abstraction. Execution uniqueness is `(run, journey, engine, repetition)`; turn uniqueness is `(execution, sequence)`; extraction uniqueness adds batch, turn and entity.

The runner reserves an attempt transactionally before making a provider request, then commits a normalized assistant turn and attempt completion together. A committed user turn awaiting a response is reused on resume. A process crash between remote completion and local commit leaves an interrupted attempt: a repeated request may still be billed by the provider. This is explicitly recorded; exactly-once remote billing cannot be guaranteed.

An OS advisory lock allows only one executing runner per database; worker threads share a transactional connection behind a lock. Extract only when execution has stopped. Batch evidence hashes prevent using older labels after evidence/status changes. Planned manifest hashes exclude outcome fields, allowing results to be appended without redefining the experiment. Hashes detect accidental modifications; they are not signed audit records.

## Adapter contract

Adapters provide `capabilities()`, `start_session()`, `request(history)`, `send_message(history, execution_key)`, and `close_session(history)`. The runner owns the persisted transcript; adapters are instantiated per execution. Normalized responses preserve text, raw JSON, returned model, request ID, citations/retrieved sources, usage, status, latency and timestamp. Refusals are valid text; empty completed responses become unusable. Incomplete outputs stop a journey and remain inspectable.

Authorization headers exist only in transport. Request logs exclude keys. Redirects are disabled to avoid forwarding authentication. Retries cover connection/timeouts, HTTP 408/429 and server errors, bounded by both attempts and request budgets. Backoff is exponential and capped; this alpha does not yet honor Retry-After or coordinate account-wide rate limits.

## Provider references

The implementation targets these official interfaces, consulted during this build:

- [OpenAI text generation / Responses API](https://developers.openai.com/api/docs/guides/text)
- [Perplexity Agent API quickstart](https://docs.perplexity.ai/docs/agent-api/quickstart)
- [Perplexity migration overview](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview)

Provider contracts and available models can change. Fixture contract tests are not a substitute for a small credentialed integration smoke test. Search tool behavior is provider/model dependent; the manifest records the requested configuration and raw outputs record returned metadata.

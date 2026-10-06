# AnswerBench

**Who gets recommended, for whom, and when?**

AnswerBench is an open-source Python CLI for measuring how AI systems surface and recommend entities across different users, tasks, contexts, and conversations. It saves the actual evidence, records its assumptions, and reports uncertainty instead of collapsing everything into an opaque score.

**Status: v0.1.0a1 — working alpha, not a validated measurement standard.** The included offline benchmark is fictional. Live adapters have contract tests but have not been exercised against paid provider accounts in this build.

## Try it in two minutes

Python 3.11+ on macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
answerbench demo ./my-demo
open ./my-demo/report/index.html  # macOS; open the file in any browser elsewhere
```

The demo needs no API key and makes no network calls. It creates 24 frozen journeys, two synthetic engines, three repeats, a SQLite evidence store, and a self-contained HTML report. All restaurants and responses in the demo are fictional. The fake engine is a software fixture, not a realistic user or market simulator.

## The workflow

```sh
answerbench init ./study
cd study
# Edit answerbench.yaml to describe the entity, cohort, users and context.
answerbench validate
answerbench generate
answerbench run --dry-run
answerbench run
# Use the run ID printed above:
answerbench extract RUN_ID
answerbench report RUN_ID --out report
answerbench runs
answerbench inspect EXECUTION_ID
answerbench reproduce RUN_ID > reproduction.json
```

Global options precede commands: `answerbench --config study/answerbench.yaml run --dry-run` or `answerbench --database study/.answerbench/results.sqlite report RUN_ID`.

- `generate` freezes seeded, stratified journeys and hashes the bundle. It does not call a provider.
- `run --dry-run` reports session counts, maximum response counts and the request budget; it requires no credentials. Dollar estimates for live models are unavailable.
- `run` is the explicit step that makes live calls when live engines are configured. Every attempt, including retries, consumes the run's request budget.
- `resume RUN_ID` continues pending or interrupted executions without duplicating committed turns. `--retry-failed` retries eligible failures within the original limits. Attempts and request ceilings are lifetime limits for that run; raising a limit requires a new run.
- `extract` appends a new observation batch from saved responses. `review RUN_ID --file reviews.json` appends human corrections without overwriting the original labels.
- `report` exports HTML, metrics, manifest and evidence JSON. Changed evidence requires a fresh extraction.
- `compare A B` compares compatible runs using paired journey clusters. For an explicit engine contrast use `--engine-a ID --engine-b ID` (the run IDs may be the same). The comparison does not establish causality.

Exit codes: `0` success, `2` invalid input, `3` incomplete run, `4` incompatible comparison, `130` interrupted.

## Run against providers

Replace the demo engines in your configuration with entries such as:

```yaml
engines:
  - id: openai_search
    adapter: openai
    model_env: ANSWERBENCH_OPENAI_MODEL
    api_key_env: OPENAI_API_KEY
    search: true
    max_output_tokens: 1600
  - id: perplexity_search
    adapter: perplexity
    model_env: ANSWERBENCH_PERPLEXITY_MODEL
    api_key_env: PERPLEXITY_API_KEY
    search: true
    max_output_tokens: 1600
```

Set the named variables locally to credentials and model IDs supported by your account. Choose current provider models explicitly; the project does not silently substitute a model. Perplexity also supports a provider `preset` instead of a model; presets own search and temperature settings. Unsupported model/tool combinations will fail and remain visible as missing executions.

OpenAI uses the Responses API; Perplexity uses its Agent API. Full transcripts are replayed on each turn. Location is conveyed in the prompt, not through a geolocated browser. Consumer chat applications may behave differently. Live runs transmit the configured prompts to the chosen provider and may incur charges. API keys are read from environment variables and never written into manifests or request logs.

## What this alpha includes

- Strict YAML validation; entity definitions, aliases, fixed competitor cohort and behavioral profiles.
- Seeded task × profile × context sampling; single-turn and bounded multi-turn journeys, with a clarification branch.
- Concurrency, transient-error retries, request budgets, process locking and saved-turn recovery.
- Fake, OpenAI and Perplexity adapters; raw response payloads, source annotations, usage and returned model IDs.
- Conservative rule-based extraction with exact spans, ambiguous-name abstention and append-only review imports.
- Weighted journey-level visibility and recommendation metrics; stratified cluster intervals; geographic, user and journey slices.
- Descriptive multi-turn discovery/retention, fixed-cohort share and repeated-set stability.
- Paired comparisons; SQLite storage; immutable planned configuration snapshots and hashed evidence.
- A static report with an evidence browser and downloadable JSON exports.

## What it does not claim yet

The extractor is **unvalidated**, English-only and cue-based. It can miss implicit recommendations or misunderstand a complex sentence. Human review is required before using results for business decisions. Rank and entity-level citation attribution are deliberately unavailable. Discovered bold names are an unverified queue, not confirmed competitors.

Templates approximate behavior; weights are a declared study design, not measured market prevalence. There is no learned simulator, real-user calibration, public human-labeled benchmark, browser automation, hosted service, monitoring or automatic optimization. The checked-in fictional seed dataset establishes reproducibility of the software, not external validity. Windows runner locking is not implemented.

See [methodology](docs/methodology.md), [architecture](docs/architecture.md), [milestones](docs/roadmap.md), and [benchmark notes](benchmarks/README.md).

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

Tests run offline. They cover deterministic generation, extraction abstentions, missing-data handling, provider response contracts, retry limits, interrupted recovery, session isolation, paired comparisons, HTML escaping, and the CLI workflow.

The project is MIT licensed. API outputs remain subject to the respective providers' terms; avoid publishing sensitive raw evidence.

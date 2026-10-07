# Alpha build validation

Validated locally on 6 October 2026 with Python 3.12 on macOS.

- Editable package installation succeeded; installed `answerbench --version` reports `0.1.0a1`.
- Offline automated suite: **26 passed**.
- End-to-end CLI demo: **24 journeys, 144 completed executions, 360 saved assistant responses** across two fictional engines.
- Paired comparison: 72 matched executions, no excluded pairs; JSON exported.
- Browser checks: report renders, geographic filtering hides unrelated dimensions, and a visible conversation disclosure expands to show its transcript and labels.
- Exports: metrics, run manifest, raw provider-shaped fixture payloads, attempts and exact extraction spans.

No live provider calls were made. Provider adapters were tested using contract-shaped fixtures. Extractor semantic accuracy and user-simulator realism remain unvalidated; the demo is not market evidence.

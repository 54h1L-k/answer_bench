# Seed benchmark

`coastal-synthetic.json` is a frozen bundle generated from `examples/demo.yaml`. It contains 24 English journeys: two tasks × two profiles × three contexts × two variants. Half are single-turn and half permit four turns. Profile weights are equal. Coordinates, entity names and contexts are fictional fixture inputs.

The fake engine deliberately samples a few named response patterns deterministically. Its output is useful for regression tests, evidence browsing and checking the pipeline. It is **not** a validated benchmark of AI recommendation systems. There are no human ground-truth labels or real-user observations in this dataset.

Regenerate only deliberately when template or policy versions change. Bundle hashes identify the exact inputs used; seed alone is insufficient. A real public benchmark needs verified entities, consent/licensing where applicable, human annotations, held-out journeys, documented sampling, and versioned provider conditions.

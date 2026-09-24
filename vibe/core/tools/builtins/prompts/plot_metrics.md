Render a braille line chart of a JSONL data stream directly in the
conversation. Use it whenever the user wants to see numeric data over time
without leaving the terminal: ML training metrics (loss/accuracy per epoch),
benchmark timings, monitoring series, or any numeric log.

Input: a JSONL file with one flat JSON object per line, e.g.
`{"epoch": 3, "loss": 0.42, "accuracy": 0.88}`. Numeric fields are
auto-detected; pass `x` and `y` to select specific fields.

The chart is displayed to the user automatically — do not repeat it in your
reply. Comment on what it shows instead (trend, convergence, anomalies).

For live monitoring of a training run: call the tool again after new lines
are appended — each call renders the current state of the file.

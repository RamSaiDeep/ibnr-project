# IBNR Chain-Ladder Reserving Pipeline

An actuarial reserving pipeline that estimates **IBNR (Incurred But Not Reported)**
claim liabilities using the classic **chain-ladder method**, plus an interactive
Dash dashboard for exploring the loss triangles and results.

## What it does

Given a long-format extract of claims data (accident year, development period,
cumulative reported claims, cumulative reported counts), the pipeline builds
loss triangles for claims, counts, and severity, then projects ultimate claims
**two ways** so they can be compared side by side:

1. **Chain-ladder on the aggregate claims triangle** — age-to-age (link ratio)
   development factors computed directly on cumulative reported claims,
   converted to cumulative development factors (CDFs), applied to the latest
   diagonal.
2. **Frequency-severity technique** — chain-ladder run *separately* on the
   counts (frequency) triangle and the severity (claims / counts) triangle to
   get an independent ultimate frequency and ultimate severity, then:
   `Ultimate Claims = Ultimate Counts x Ultimate Severity`.

Development factors for all three triangles can be selected using a simple
average, a volume-weighted average (sum of column totals — the more standard
actuarial default), or a trailing N-period average.

Note: frequency-severity still *uses* chain-ladder as a sub-step — it's not an
alternative method, it's chain-ladder applied to two component triangles and
recombined by multiplication, rather than chain-ladder applied once to the
combined dollar triangle. The two approaches will generally give close but not
identical answers; comparing them is a useful diagnostic (large divergence can
flag a shift in average claim size or claim frequency that a single aggregate
triangle would mask).

## Why this version is different from a typical notebook script

The original exploratory version repeated the same ~20 lines of pivot/pad/loop
logic three separate times (once each for claims, counts, severity), used a
hardcoded Google Drive path, and left leftover scratch code in place. This
version:

- Replaces manual list-padding with `pandas.pivot_table`, removing an entire
  class of "columns silently misaligned" bugs.
- Generalizes triangle-building, age-to-age factors, and development factor
  selection into single reusable functions (`ibnr/triangles.py`) instead of
  copy-pasted blocks.
- **Found and fixed a real bug** in the ultimate-projection step: cumulative
  development factors were being mapped to accident years in reversed order,
  which would have applied the largest CDF to the *most mature* year instead
  of the least mature one. Caught by testing against synthetic data with a
  known development pattern.
- **Found and fixed a methodology mix-up**: the original code built a severity
  triangle but never actually used it — ultimate claims were projected with
  chain-ladder straight off the aggregate claims triangle, and a separate
  "ultimate counts" calculation accidentally multiplied the counts CDF by the
  latest *claims* diagonal instead of the latest counts diagonal. This version
  implements both chain-ladder-on-claims and true frequency-severity
  (ultimate counts x ultimate severity) properly, and shows both side by side
  so they can be sanity-checked against each other.
- Adds a CLI (`--file`, `--method`) instead of a hardcoded path.
- Adds an interactive Dash dashboard.

## Project structure

```
ibnr_project/
├── ibnr/
│   ├── __init__.py
│   ├── triangles.py      # triangle construction, link ratios, dev factors, ultimates
│   └── pipeline.py       # orchestrates the full run; also runnable as a CLI
├── dashboard.py           # Dash app
├── sample_claims.xlsx     # synthetic sample data for demoing the pipeline
├── requirements.txt
└── README.md
```

## Running it

### Local development

```bash
pip install -r requirements.txt

# Run the pipeline from the command line
python -m ibnr.pipeline --file sample_claims.xlsx --method volume_weighted

# Launch the interactive dashboard
python dashboard.py
# then open http://127.0.0.1:8050
```

### Deploy to Render

This application is configured for deployment to Render using the provided `render.yaml` file.

**Prerequisites:**
- A Render account (free tier available)
- Git repository with this code

**Deployment steps:**

1. Push your code to a Git repository (GitHub, GitLab, or Bitbucket)
2. Log in to [Render](https://render.com)
3. Click "New +" and select "Web Service"
4. Connect your Git repository
5. Render will automatically detect the `render.yaml` file and use its configuration
6. Click "Create Web Service"

The application will:
- Automatically install dependencies from `requirements.txt`
- Start the Dash app using Gunicorn
- Use the PORT environment variable provided by Render
- Be accessible at your Render service URL

**Alternative manual deployment:**
If you prefer manual configuration, create a web service with:
- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn dashboard:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`

## Input data format

An Excel file with one row per (accident year, development period) combination:

| AY   | Reported_year | Reported_Claims | Reported_Counts |
|------|----------------|------------------|------------------|
| 2018 | 1              | 393305.00        | 100              |
| 2018 | 2              | 631856.54        | 160              |
| ...  | ...            | ...              | ...              |

`Reported_year` is the sequential development period index (1, 2, 3, ...),
which the pipeline converts into development ages in months (12, 24, 36, ...).

## Dashboard

Three tabs:

- **Triangles** — heatmap + data table for claims, counts, or severity, switchable
  via a dropdown.
- **Development Factors** — line chart comparing selected link ratios across
  claims, counts, and severity by development age.
- **Ultimates & IBNR** — grouped bar chart comparing ultimate claims from
  chain-ladder vs. frequency-severity by accident year, plus the full
  comparison table and frequency-severity detail (ultimate counts, ultimate
  severity, and the resulting ultimate claims).

## Possible next steps

- Add a Bornhuetter-Ferguson or Cape Cod method alongside chain-ladder for
  comparison.
- Add confidence intervals around the ultimate estimates (e.g. Mack's method).
- Let the dashboard accept a file upload instead of a CLI-supplied path.
- Add unit tests for `triangles.py` using a small hand-computed triangle with
  known expected CDFs (this is how the CDF-mapping bug above was found —
  worth formalizing into `pytest`).

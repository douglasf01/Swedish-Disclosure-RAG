# Swedish Disclosure RAG

An analyst assistant for companies listed on Nasdaq Stockholm. A supervisor sends each
question to the specialists it needs, and a judge combines their findings into one answer
with a source for every claim. Questions can be in Swedish or English.

Live demo: https://disclosures-api-bgfm7xmzdq-lz.a.run.app (limited to 50 questions per day)

| Specialist | Source | How it answers |
|---|---|---|
| `press` | Press releases from Nasdaq's company news service | Hybrid search over passages |
| `reports` | The report PDFs attached to those releases | Hybrid search over passages, cited by page |
| `insider` | Insider trades from Finansinspektionen's register | Writes SQL against a table |

> "Vilken soliditet redovisade MEKO efter tredje kvartalet 2026, och har vd köpt aktier i
> bolaget under hösten?"
>
> MEKO redovisade en soliditet på 36,2 procent per den 30 september 2026 [R7]. Ja, MEKO:s
> VD Pehr Oscarsson har köpt aktier under hösten 2026: 20 000 aktier den 2 september till
> kursen 71,80 SEK och 25 000 aktier den 2 oktober till kursen 65,72 SEK [I2].
>
> [R7] MEKO AB, delårsrapport januari - september 2026, page 7 · [I2] query of the insider-trade register

## How it works

Collecting data, twice per weekday:

```
Nasdaq news service ──> press releases ──┐
                    └─> report PDFs ─────┼──> SQLite ──┬──> two search indexes ──> bucket
Finansinspektionen ───> insider trades ──┘             └──> insider-trade table ─> BigQuery
```

Answering a question:

```
                              ┌──> press ────> search the press index ────┐
question ──> supervisor ──────┼──> reports ──> search the reports index ──┼──> judge ──> answer
                              └──> insider ──> SQL on the trade table ────┘
```

The chosen specialists run in parallel, each in a loop of at most five rounds of tool
calls. A question for one specialist skips the judge. The graph is built with LangGraph and
the model is Gemini. There is no database server and no vector database: each index is two
files that the service loads into memory.

Three rules are enforced in code, not in the prompts:

- **Point in time.** Every question has an "as of" date. The server applies it inside every
  search and around every SQL query, so no agent can see anything published later.
- **Grounded or refuse.** Everything a tool shows a model is a numbered source such as
  `[P2]`. An answer that cites no such source is replaced by a refusal.
- **Bounded SQL.** The insider specialist's query must be a single `SELECT` on the view
  `trades`. It can read no other table, gets at most 50 rows and runs read-only.

## Code map

| Part | Where |
|---|---|
| Ingestion, one module per source | `disclosures/ingest/` |
| SQLite store | `disclosures/store.py` |
| Passages and embeddings (multilingual E5 on CPU) | `disclosures/index.py` |
| BM25 and dense search with date filters | `disclosures/retrieve.py` |
| Supervisor, specialists, judge and graph | `disclosures/agents/` |
| Bucket, warehouse and model adapters | `disclosures/cloud/` |
| API: `POST /ask`, `GET /search`, `GET /health`, web client | `disclosures/app.py` |
| Daily job | `disclosures/job.py` |
| Questions and metrics | `evals/` |
| Terraform for Google Cloud | `infra/` |

Only `disclosures/cloud/` imports a cloud SDK. Moving to another cloud means adding an
implementation there and rewriting `infra/`.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run python -m disclosures.ingest --since 2026-08-01   # throttled, takes over an hour
uv run python -m disclosures.index                        # the reports take a while
uv run pytest -q                                          # no network or model needed

# Search needs no credentials:
uv run uvicorn disclosures.app:create_app --factory --port 8080
curl 'localhost:8080/search?q=ny+vd&as_of=2026-11-05'

# Answers need a model, either a Gemini API key or Vertex AI:
GEMINI_API_KEY=... uv run uvicorn disclosures.app:create_app --factory --port 8080
GOOGLE_CLOUD_PROJECT=... uv run uvicorn disclosures.app:create_app --factory --port 8080
```

`SPECIALISTS=press` runs one specialist alone. `WAREHOUSE=bigquery` queries BigQuery
instead of the local SQLite file. All settings are in `disclosures/config.py`.

## Evaluation

`evals/questions.jsonl` holds 49 labelled questions. Some are traps: the answer was
published after the question's date, so using it counts as a leak.

```bash
uv run python -m evals.retrieval --corpus press    # free, runs in seconds
uv run python -m evals.answers                     # calls the model, about 7 cents
uv run python -m evals.answers --specialists press
```

Measured on 12 November 2026 on the dev split with `gemini-3.5-flash-lite`, over 3,045
press releases, 682 report PDFs and 3,725 insider trades.

Retrieval, top 8 passages:

| Corpus | Questions | Keyword | Embeddings | Hybrid | Leaks |
|---|---|---|---|---|---|
| Press | 17 | 0.82 | 1.00 | 0.82 | 0 |
| Reports | 4 | 1.00 | 1.00 | 1.00 | 0 |

Answers, 32 questions of which 7 cannot be answered:

| Measure | Press specialist alone | Full team |
|---|---|---|
| Answerable questions answered | 18 of 25 | 25 of 25 |
| Answer contains the expected fact | 1 of 9 | 9 of 9 |
| Answer cites a labelled release | 18 of 21 | 21 of 21 |
| Unanswerable questions refused | 7 of 7 | 6 of 7 |
| Leaks | 0 | 0 |
| Mean latency | 2.7 s | 3.4 s |
| Cost for all 32 questions | $0.058 | $0.064 |

The press specialist alone is a baseline: 11 questions need report or insider data it
does not have. The question set is small and was drafted with AI assistance, so treat the
numbers as a smoke test. The `test` split is unscored.

## Deploy to Google Cloud

Needs the `gcloud` CLI, Terraform and a project with billing enabled.

```bash
gcloud auth login
gcloud auth application-default login
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project PROJECT

cd infra
cp terraform.tfvars.example terraform.tfvars    # set project_id
terraform init && terraform apply
cd ..

gcloud storage cp data/releases.db gs://PROJECT-disclosures-data/releases.db
gcloud storage cp -r data/index gs://PROJECT-disclosures-data/
scripts/deploy.sh PROJECT
gcloud run jobs execute disclosures-ingest --project PROJECT --region europe-north1   # fills BigQuery
```

Pushes to `main` deploy through GitHub Actions once `github_repo` is set in
`terraform.tfvars` and the three repository variables named in
`.github/workflows/deploy.yml` are filled in. The workflow first runs the retrieval
evaluation and stops on a low hit rate or a leak.

The service scales to zero and runs at most one instance. It answers six questions per
minute per address and 50 per day in total (`DAILY_QUESTION_CAP`). Each question costs
about 0.2 cents in tokens, and everything else stays inside the free tier at demo traffic.
Every question is logged as one JSON line that feeds a dashboard of volume, latency and cost.

## Status and limits

Deployed on Cloud Run and answering from the bucket indexes and BigQuery. Deploys through
GitHub Actions have not run yet.

- The specialists cannot see each other's findings. A question where one part depends on
  another works only because the judge relates the two findings afterwards.
- Hybrid search is worse than embeddings alone on press releases, because the keyword
  ranking has no Swedish stemming or stop words.
- Report PDFs are read as plain text, so tables lose their layout.
- The `event` filter exists but nothing tags releases with events yet.
- Building the reports index from scratch takes about 100 minutes on a laptop. The daily
  job only embeds what is new.

## Data sources

Press releases and report PDFs come from Nasdaq's company news service
(`api.news.eu.nasdaq.com`), which is not a documented public API. Insider trades come from
the CSV export of Finansinspektionen's register (`marknadssok.fi.se`). Both are fetched
throttled. This repository holds code only, and every citation links back to the original.
Check both sites' terms of use before publishing a hosted copy.

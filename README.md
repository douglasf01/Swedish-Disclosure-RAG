# Swedish Disclosure RAG

A multi-agent analyst assistant for companies listed on Nasdaq Stockholm. Three specialist
agents each answer from their own source, a supervisor decides which of them a question
needs, and a judge combines what they find into one answer with a link to every source.

| Specialist | Source | How it answers |
|---|---|---|
| `press` | Press releases from Nasdaq's company news service | Hybrid search over passages |
| `reports` | The interim and annual report PDFs attached to those releases | Hybrid search over passages, cited by page |
| `insider` | Insider trades from Finansinspektionen's public register | Writes SQL against a table |

It runs on Google Cloud Run and costs nothing while idle. Questions can be asked in Swedish
or English, and the answer follows the question's language.

> "Vilken soliditet redovisade MEKO efter tredje kvartalet 2026, och har vd köpt aktier i
> bolaget under hösten?"
>
> MEKO redovisade en soliditet på 36,2 procent per den 30 september 2026 i sin delårsrapport
> för januari–september 2026 [R7]. Ja, MEKO:s VD Pehr Oscarsson har köpt aktier i bolaget
> under hösten 2026; han genomförde två köp, ett den 2 september 2026 av 20 000 aktier till
> kursen 71,80 SEK och ett den 2 oktober 2026 av 25 000 aktier till kursen 65,72 SEK [I2].
>
> [R7] MEKO AB, delårsrapport januari - september 2026, page 7 · [I2] query of the insider-trade register

(An actual answer from the evaluation run. The supervisor sent one question to `reports` and
one to `insider`, and the judge combined the two findings.)

## What is different from a standard RAG demo

**Point-in-time answers.** Every question has an "as of" date. The server applies it inside
every tool call: before ranking in the search, and as a view around every SQL query. No
agent can widen it. Asked "who is acting CEO of Novedo?" as of 30 October, the assistant
must refuse, because the appointment was published on 5 November. The evaluation set
contains such questions on purpose ("traps") and fails if anything later is used.

**Grounded or refuse.** Everything a tool shows a model is registered as a numbered source,
such as `[P2]` for a press release passage or `[I1]` for a query result. A specialist's text
that cites no registered source is replaced by a refusal in code, whatever the model wrote,
and the judge's combined answer goes through the same check.

**Agents with one job each.** The supervisor only routes, the specialists only answer from
their own source, and the judge only combines. Each can be tested alone with a scripted
model, and the evaluation can run one specialist or the whole team on the same questions.

**Evaluation as a deploy gate.** The deploy workflow downloads the live index, runs the
retrieval evaluation, and stops if the hit rate falls below a threshold or if any passage
from after a question's date is returned.

**Built to move.** Everything that depends on the cloud provider sits behind three small
interfaces in `disclosures/cloud/`. See [Porting to another cloud](#porting-to-another-cloud).

## How it works

Collecting data, twice per weekday (a Cloud Run job started by Cloud Scheduler):

```
Nasdaq news service ──> press releases ──┐
                    └─> report PDFs ─────┼──> SQLite ──┬──> two search indexes ──> bucket
Finansinspektionen ───> insider trades ──┘             └──> insider-trade table ─> BigQuery
```

Answering a question (the Cloud Run service):

```
                              ┌──> press ────> search the press index ────┐
question ──> supervisor ──────┼──> reports ──> search the reports index ──┼──> judge ──> answer
             (who gets what)  └──> insider ──> SQL on the trade table ────┘    (one text,
                                                                                every claim cited)
```

The supervisor's plan is a list of assignments: which specialist, and what to ask it. The
chosen specialists run in parallel. Each one is a loop of at most five rounds of tool calls
and returns a finding: a short text with source ids. The judge sees the findings and the
sources they cite, writes the answer, and points out what no specialist could answer or
where two of them disagree. The graph is built with LangGraph. The model is Gemini on
Vertex AI, reached through LangChain's chat-model interface.

The model is only called where there is a decision to make. A question for one specialist
skips the judge, and an assistant configured with a single specialist skips the supervisor
too, which leaves that specialist as a plain RAG agent.

### Code map

| Part | Where | Notes |
|---|---|---|
| Ingestion | `disclosures/ingest/` | One module per source, throttled and safe to re-run |
| Store | `disclosures/store.py` | One SQLite file, one table per source |
| Index | `disclosures/index.py` | 700-character passages, multilingual E5 embeddings on CPU (ONNX), incremental rebuilds |
| Retrieval | `disclosures/retrieve.py` | BM25 and dense search fused with reciprocal rank fusion, with filters for date, company and category |
| Agents | `disclosures/agents/` | `base.py` (tool loop, citation check), `documents.py`, `insider.py`, `supervisor.py`, `judge.py`, `graph.py` |
| Cloud adapters | `disclosures/cloud/` | Blob storage, SQL warehouse, language model |
| Wiring | `disclosures/wiring.py` | Builds retrievers and the assistant from the settings |
| API | `disclosures/app.py` | `POST /ask`, `GET /search`, `GET /health`, a one-page web client |
| Daily job | `disclosures/job.py` | Download, ingest, extend the indexes, load the warehouse, upload |
| Evaluation | `evals/` | Labelled questions, retrieval metrics, end-to-end answer metrics |
| Infrastructure | `infra/` | Terraform: Cloud Run service and job, Scheduler, bucket, BigQuery dataset, image repository, keyless GitHub deploys, log-based metrics, dashboard, budget alert |

There is no database server and no vector database. The corpora are small enough that each
index is two files which a service instance loads into memory at start.

### The SQL specialist

The insider specialist writes its own SQL, so `disclosures/cloud/warehouse.py` keeps that
SQL in bounds:

- **Read only.** A query must be a single `SELECT`. In the cloud the service's identity has
  read access to the dataset and nothing more. Locally the SQLite file is opened read-only.
- **Point in time.** The model never sees the stored table. Its query runs against
  `trades`, a view the warehouse wraps around the query, holding only rows published up to
  the question's date. A query that names the underlying table is rejected.
- **Bounded.** At most 50 rows come back, and BigQuery refuses a query that would scan more
  than 100 MB.

An error goes back to the model as text, so it can correct its query.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run python -m disclosures.ingest --since 2026-08-01   # all three sources, throttled, over an hour
uv run python -m disclosures.index                        # embeds on CPU, the reports take a while
uv run pytest -q                                          # no network and no model needed

# Search works without any credentials:
uv run uvicorn disclosures.app:create_app --factory --port 8080
curl 'localhost:8080/search?q=ny+vd&as_of=2026-11-05'
curl 'localhost:8080/search?q=kassaflöde&corpus=reports&company=karnov'

# Answers need a model. A Gemini API key from Google AI Studio has a free tier:
GEMINI_API_KEY=... uv run uvicorn disclosures.app:create_app --factory --port 8080
```

`--only press reports insider` limits ingestion to some sources, and `--only press` limits
indexing to one corpus. Locally the insider table is queried in SQLite. Set
`WAREHOUSE=bigquery` to use BigQuery instead. `SPECIALISTS=press` runs one specialist alone.

## Evaluation

`evals/questions.jsonl` holds the questions. Each has the date it is asked on, the
specialists a good answer draws on, the ids of the releases that hold the answer, strings a
correct answer must contain, and a split: `dev` for tuning and `test` for a sealed set that
should be scored once.

```bash
uv run python -m evals.retrieval --corpus press            # free, runs in seconds
uv run python -m evals.retrieval --corpus reports
GEMINI_API_KEY=... uv run python -m evals.answers --specialists press   # one specialist alone
GEMINI_API_KEY=... uv run python -m evals.answers                       # the whole team
```

All numbers below were measured on 12 November 2026 with `gemini-3.5-flash-lite` on Vertex
AI, the insider table in SQLite, and this corpus (1 August to 12 November 2026):

| Source | Size |
|---|---|
| Press releases | 3,045 releases from 461 companies, 20,132 passages |
| Reports | 682 PDFs attached to 544 report releases from 439 companies, 62,020 passages (one PDF could not be read) |
| Insider trades | 3,725 rows for 672 issuers |

### Retrieval

Dev split. Press: 17 questions with a labelled release. Reports: 4.

| Corpus | Mode | Hit@1 | Hit@3 | Hit@8 | MRR | Leaks |
|---|---|---|---|---|---|---|
| Press | Keyword (BM25) | 0.59 | 0.65 | 0.82 | 0.64 | 0 |
| Press | Embeddings | 0.77 | 0.88 | 1.00 | 0.83 | 0 |
| Press | Hybrid | 0.65 | 0.82 | 0.82 | 0.72 | 0 |
| Reports | Keyword (BM25) | 0.50 | 1.00 | 1.00 | 0.71 | 0 |
| Reports | Embeddings | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| Reports | Hybrid | 1.00 | 1.00 | 1.00 | 1.00 | 0 |

On press releases hybrid is still worse than embeddings alone: the keyword ranking has no
Swedish stemming or stop words, and fusing it in pushes correct releases out of the top
eight. Hybrid remains the default until that is fixed. Four report questions are far too
few to rank the modes. That row only shows the reports index works.

### Answers: one specialist against the team

Dev split, 32 questions: 25 that can be answered from the data and 7 that cannot. The same
questions were put to the press specialist alone and to the full team.

| Measure | Press specialist alone | Supervisor + three specialists + judge |
|---|---|---|
| Answerable questions answered | 18 of 25 | 25 of 25 |
| Answer contains the expected fact | 1 of 9 | 9 of 9 |
| Answer cites a labelled release | 18 of 21 | 21 of 21 |
| Citation precision | 0.88 | 0.90 |
| Expected specialists consulted | 16 of 25 | 23 of 25 |
| Unanswerable questions refused | 7 of 7 | 6 of 7 |
| Leaks (anything from after the question date) | 0 | 0 |
| Mean latency | 2.7 s | 3.4 s |
| Mean tool calls | 2.5 | 2.6 |
| Total cost, 32 questions (list price) | $0.058 | $0.064 |

How to read this:

- On the 21 questions about press releases both give the same result: 16 of 16 answered
  and 5 of 5 refused.
- The other 11 questions need report or insider data. The press specialist has neither,
  so the left column mostly shows that it refuses them, as it should. It is a baseline,
  not a fair fight.
- The one refusal the team missed is "why did Fredrik Strömholm sell his Humana shares?".
  The answer says that the sources do not give a reason and then states the trade from the
  register, with a citation. That is arguably the right answer, but it is not a refusal, so
  it is counted against the team.
- In both "expected specialist" misses the supervisor chose `reports` where the label says
  `press`, for a question about a quarterly report. The answers were correct and cited the
  labelled release. The same release is in both corpora, so the label is the stricter one.
- The team costs about a second more per question and almost nothing more in tokens,
  because most questions still go to a single specialist and skip the judge.

Whether each answer's text is right is not scored beyond the expected-fact check. A handful
were checked by hand against the sources and matched. The `test` split (17 questions) has
not been run. It is meant to be scored once, after the question set has been reviewed.

Limits of the current set, stated plainly: it is small (49 questions), only three of them
need two specialists, nearly every question names the company, and all were drafted from
the data with AI assistance and have not been reviewed line by line. Treat the numbers as a
smoke test until the set is reviewed and extended.

## Deploy to Google Cloud

One-time setup, with the `gcloud` CLI and Terraform installed and a project that has billing
enabled (the free trial credit is enough):

```bash
gcloud auth login
gcloud auth application-default login
# Terraform needs these two enabled before it can enable the rest:
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project PROJECT

cd infra
cp terraform.tfvars.example terraform.tfvars    # set project_id
terraform init
terraform apply

cd ..
gcloud storage cp data/releases.db gs://PROJECT-disclosures-data/releases.db
gcloud storage cp -r data/index gs://PROJECT-disclosures-data/
scripts/deploy.sh PROJECT
gcloud run jobs execute disclosures-ingest --project PROJECT --region europe-north1   # fills BigQuery
```

After that, pushes to `main` deploy through GitHub Actions if `github_repo` is set in
`terraform.tfvars` and the three repository variables named in
`.github/workflows/deploy.yml` are filled in from `terraform output`.

### Cost and spending guards

| Resource | Setting | Expected cost at demo traffic |
|---|---|---|
| Cloud Run service | scales to zero, at most one instance, 1 vCPU and 4 GiB | inside the free tier |
| Cloud Run job | twice per weekday, some minutes each | inside the free tier |
| Cloud Scheduler | one schedule | free (three are included) |
| Bucket | about 250 MB | cents per month |
| BigQuery | one table of a few thousand rows, queries capped at 100 MB | inside the free tier |
| Image repository | keeps the three newest images | cents per month above 0.5 GB |
| Gemini | per token | about 0.2 cents per question |

The public endpoint is limited to six questions per minute per address and 50 per day in
total (`DAILY_QUESTION_CAP`). Because the service never runs more than one instance, the
daily total is a real ceiling. Set `billing_account` in `terraform.tfvars` to also get
e-mail alerts at 50, 90 and 100 percent of a monthly budget.

### Monitoring

The service logs one JSON line per question with latency, tokens in and out, estimated cost,
whether it answered or refused, which specialists were consulted and the number of tool
calls. Terraform turns these into three log-based metrics and a dashboard (questions per
hour, latency percentiles, cost).

## Porting to another cloud

The code outside `disclosures/cloud/` imports no cloud SDK. Three settings choose an
implementation, and each has one module with the interface, today's implementations and a
factory:

| Concern | Setting | Interface | Today | To add for Azure, for example |
|---|---|---|---|---|
| Data files | `STORAGE_PROVIDER` | `storage.BlobStore`: `download`, `upload` | Cloud Storage, or none (local files) | A class over Azure Blob Storage |
| Insider-trade table | `WAREHOUSE` | `warehouse.Warehouse`: `describe`, `query`, `load` | BigQuery or SQLite | A class over Synapse or Azure SQL |
| Language model | `LLM_PROVIDER` | LangChain `BaseChatModel` | Gemini on Vertex AI or the Gemini API | A branch returning `AzureChatOpenAI` |

What a port involves, in order of effort:

1. **`infra/`** has to be rewritten for the other provider. This is the bulk of the work:
   a container service that scales to zero, a scheduled container job, a bucket, a
   warehouse, a registry, two identities and the log metrics.
2. **One class or branch per row above**, plus its SDK in `pyproject.toml`.
3. **The deploy script and workflow**: registry login and the deploy commands.
4. **Run the evaluations again.** A different model writes different answers and different
   SQL. The warehouse tells the model which SQL dialect to write (`Warehouse.dialect` and
   `describe()`), so the insider specialist's prompt follows the warehouse automatically,
   but the numbers below are only valid for the model and warehouse they were measured on.

Ingestion, the store, indexing, retrieval, the agents, the API and the tests do not change.
Embeddings run in-process on CPU, so the indexes are the same files in every cloud.

## Data sources and rights

Press releases and report PDFs come from Nasdaq's company news service
(`api.news.eu.nasdaq.com`), the endpoint its own news pages use. It is not a documented
public API and may change. Requests are throttled to a few per second. The texts belong to
the issuing companies.

Insider trades come from the CSV export of Finansinspektionen's insider register
(`marknadssok.fi.se`). The register is public, but the site drops clients that ask too
often, so it is fetched a week at a time with a pause in between.

This repository contains code only, and the assistant links back to the original for every
citation. Check both sites' terms of use before publishing a hosted copy.

## Status

The system runs locally and has been evaluated with live model calls (see Evaluation).
It has not been deployed from this repository yet.

Done and verified locally:

- Ingestion of all three sources, both indexes, the SQLite warehouse, the LangGraph
  assistant, the API and web client, and both evaluations.
- 56 unit tests, none needing network or a model.
- The Docker image builds and serves `/health` and `/search` from the local data.
- `terraform validate` passes for the infrastructure.

Written but not yet run against the real service:

- **BigQuery.** The adapter is unit-tested against a fake client only. It has never loaded
  or queried a real table, and the model has never written BigQuery SQL in an evaluation.
- **Terraform.** The configuration validates but has not been applied.
- **GitHub Actions deploys**: the workflow and the keyless identity setup have never run,
  because the code is not on GitHub yet.
- **The dashboard and log-based metrics** have not been checked with real traffic.

Known weaknesses:

- The specialists run in parallel and cannot see each other's findings. A question where
  one part depends on another ("did management buy after the profit warning?") works only
  because the supervisor asks for the wider facts and the judge relates them. A second
  round, where the supervisor can ask again after seeing the findings, is not built.
- The question set is small and unreviewed (see Evaluation), and the `test` split is unscored.
- A cold start loads the embedding model and both indexes, about 1.7 GiB in memory. It has
  not been timed on Cloud Run.
- Building the reports index from scratch is slow: about 100 minutes on a 12-core laptop
  for 62,000 passages. The daily job only embeds what is new.
- Report PDFs are read as plain text, so tables lose their layout and a figure can end up
  far from its label.
- Event tagging (profit warning, acquisition, order, share issue, management change) is
  still missing. The `event` column and filter exist, but the tagger does not.
- Swedish stemming and stop words for the keyword search, and reranking.

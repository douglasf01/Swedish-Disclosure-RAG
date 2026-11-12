# Warehouse for the insider-trade table. The insider specialist writes its own SQL, so the
# service only gets read access. The daily job replaces the table after each ingestion
# (the job creates the table itself, with the schema defined in disclosures/store.py).
resource "google_bigquery_dataset" "warehouse" {
  dataset_id                 = local.name
  location                   = var.region
  description                = "Insider trades from Finansinspektionen's register"
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_dataset_iam_member" "runtime_read" {
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.runtime.member
}

resource "google_bigquery_dataset_iam_member" "ingest_write" {
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.ingest.member
}

# Running a query or a load job is a project-level permission, separate from data access.
resource "google_project_iam_member" "bigquery_jobs" {
  for_each = {
    runtime = google_service_account.runtime.member
    ingest  = google_service_account.ingest.member
  }
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = each.value
}

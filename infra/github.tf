# Keyless deploys from GitHub Actions: the workflow proves which repository it runs in
# and receives a short-lived token. No service account key is stored in GitHub.
locals {
  github = var.github_repo != "" ? 1 : 0
}

resource "google_service_account" "deployer" {
  count        = local.github
  account_id   = "${local.name}-deployer"
  display_name = "GitHub Actions deployer"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "deployer_run" {
  count   = local.github
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer[0].member
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  count      = local.github
  repository = google_artifact_registry_repository.app.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer[0].member
}

# Needed to deploy a service revision or job that runs as one of the two service accounts.
resource "google_service_account_iam_member" "deployer_act_as" {
  for_each = local.github == 1 ? {
    runtime = google_service_account.runtime.name
    ingest  = google_service_account.ingest.name
  } : {}
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer[0].member
}

# The deploy workflow downloads the index to run the retrieval evaluation before deploying.
resource "google_storage_bucket_iam_member" "deployer_data" {
  count  = local.github
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.deployer[0].member
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = local.github
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = local.github
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only this repository can obtain a token, and only from the main branch.
  attribute_condition = "assertion.repository == \"${var.github_repo}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonate" {
  count              = local.github
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repo}"
}

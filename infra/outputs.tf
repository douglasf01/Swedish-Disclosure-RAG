output "service_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "image" {
  description = "Image path to build and push to"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}/app"
}

output "data_bucket" {
  value = google_storage_bucket.data.name
}

output "warehouse_dataset" {
  value = google_bigquery_dataset.warehouse.dataset_id
}

output "github_workload_identity_provider" {
  description = "Set as the GitHub Actions variable GCP_WORKLOAD_IDENTITY_PROVIDER"
  value       = one(google_iam_workload_identity_pool_provider.github[*].name)
}

output "github_service_account" {
  description = "Set as the GitHub Actions variable GCP_SERVICE_ACCOUNT"
  value       = one(google_service_account.deployer[*].email)
}

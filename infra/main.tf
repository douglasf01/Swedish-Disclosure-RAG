terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

locals {
  name = "disclosures"
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
  # Placeholder images let Terraform create the service and job before the first build.
  # After that the image is set by scripts/deploy.sh or the deploy workflow.
  placeholder_service = "us-docker.pkg.dev/cloudrun/container/hello"
  placeholder_job     = "us-docker.pkg.dev/cloudrun/container/job"
  env = {
    DATA_BUCKET          = google_storage_bucket.data.name
    GOOGLE_CLOUD_PROJECT = var.project_id
    VERTEX_LOCATION      = var.vertex_location
    LLM_MODEL            = var.llm_model
    WAREHOUSE            = "bigquery"
    WAREHOUSE_DATASET    = google_bigquery_dataset.warehouse.dataset_id
    DAILY_QUESTION_CAP   = tostring(var.daily_question_cap)
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  repository_id = local.name
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for the disclosure assistant"

  # Old images are the main storage cost, so keep the three newest.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      older_than = "86400s"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-${local.name}-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

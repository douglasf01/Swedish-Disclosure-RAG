# Two identities, so that the public service can read data but never change it.
#
#   runtime   the web service: calls Vertex AI, reads the bucket, queries the warehouse
#   ingest    the daily job: writes the bucket and replaces the warehouse table
resource "google_service_account" "runtime" {
  account_id   = "${local.name}-runtime"
  display_name = "Disclosure assistant runtime"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "ingest" {
  account_id   = "${local.name}-ingest"
  display_name = "Disclosure assistant ingestion job"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.runtime.member
}

resource "google_storage_bucket_iam_member" "runtime_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.runtime.member
}

resource "google_storage_bucket_iam_member" "ingest_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.ingest.member
}

resource "google_cloud_run_v2_service" "api" {
  name                = "${local.name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.runtime.email
    max_instance_request_concurrency = 8
    timeout                          = "120s"

    scaling {
      min_instance_count = 0 # scale to zero: no traffic, no cost
      max_instance_count = 1 # caps spend and makes the in-memory rate limit a real ceiling
    }

    containers {
      image = local.placeholder_service
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "4Gi" # the embedding model and both indexes in memory take about 1.7 GiB
        }
        cpu_idle          = true # bill CPU only while handling requests
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "ingest" {
  name                = "${local.name}-ingest"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.ingest.email
      timeout         = "3600s"
      max_retries     = 1

      containers {
        image   = local.placeholder_job
        command = ["python", "-m", "disclosures.job"]
        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi" # a backfill of the reports index peaked at 2.7 GiB
          }
        }
        dynamic "env" {
          for_each = local.env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_service.apis]
}

# Runs the ingestion job twice every weekday, after the morning and evening release peaks.
resource "google_service_account" "scheduler" {
  account_id   = "${local.name}-scheduler"
  display_name = "Triggers the ingestion job"
  depends_on   = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  name     = google_cloud_run_v2_job.ingest.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "ingest" {
  name      = "${local.name}-ingest"
  region    = var.scheduler_region
  schedule  = "30 9,19 * * 1-5"
  time_zone = "Europe/Stockholm"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingest.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
  depends_on = [google_project_service.apis]
}

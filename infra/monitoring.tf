# Metrics are read from the JSON line the service logs for every question
# (see disclosures/telemetry.py), so no metrics library or agent is needed.
locals {
  ask_filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${google_cloud_run_v2_service.api.name}\" AND jsonPayload.message=\"ask\""
}

resource "google_logging_metric" "questions" {
  name   = "${local.name}/questions"
  filter = local.ask_filter
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key        = "answerable"
      value_type = "STRING"
    }
  }
  label_extractors = {
    answerable = "EXTRACT(jsonPayload.answerable)"
  }
}

resource "google_logging_metric" "latency" {
  name            = "${local.name}/latency_ms"
  filter          = local.ask_filter
  value_extractor = "EXTRACT(jsonPayload.latency_ms)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 1.5
      scale              = 100
    }
  }
}

resource "google_logging_metric" "cost" {
  name            = "${local.name}/cost_usd"
  filter          = local.ask_filter
  value_extractor = "EXTRACT(jsonPayload.cost_usd)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 2
      scale              = 0.00001
    }
  }
}

resource "google_monitoring_dashboard" "main" {
  dashboard_json = jsonencode({
    displayName = "Disclosure assistant"
    gridLayout = {
      columns = 2
      widgets = [
        {
          title = "Questions per hour, by answered or refused"
          xyChart = {
            dataSets = [{
              plotType = "STACKED_BAR"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.questions.name}\" resource.type=\"cloud_run_revision\""
                  aggregation = {
                    alignmentPeriod    = "3600s"
                    perSeriesAligner   = "ALIGN_SUM"
                    crossSeriesReducer = "REDUCE_SUM"
                    groupByFields      = ["metric.label.answerable"]
                  }
                }
              }
            }]
          }
        },
        {
          title = "Latency per question, 50th and 95th percentile (ms)"
          xyChart = {
            dataSets = [
              for p in ["50", "95"] : {
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.latency.name}\" resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod    = "3600s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_${p}"
                    }
                  }
                }
              }
            ]
          }
        },
        {
          title = "Estimated model cost per hour (USD)"
          xyChart = {
            dataSets = [{
              plotType = "STACKED_BAR"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.cost.name}\" resource.type=\"cloud_run_revision\""
                  aggregation = {
                    alignmentPeriod    = "3600s"
                    perSeriesAligner   = "ALIGN_DELTA"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
      ]
    }
  })
  depends_on = [google_project_service.apis]
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "Disclosure assistant monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    # No currency code: the amount is in the billing account's own currency.
    specified_amount {
      units = tostring(var.monthly_budget)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
  depends_on = [google_project_service.apis]
}

data "google_project" "this" {}

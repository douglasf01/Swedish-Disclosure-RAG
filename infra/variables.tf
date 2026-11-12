variable "project_id" {
  description = "GCP project id"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, the bucket and the image repository"
  type        = string
  default     = "europe-north1"
}

variable "vertex_location" {
  description = "Vertex AI location for model calls"
  type        = string
  default     = "global"
}

variable "llm_model" {
  description = "Gemini model id"
  type        = string
  default     = "gemini-3.5-flash-lite"
}

variable "daily_question_cap" {
  description = "Most questions the public endpoint answers per day"
  type        = number
  default     = 50
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy, as owner/name. Empty disables keyless deploys."
  type        = string
  default     = ""
}

variable "billing_account" {
  description = "Billing account id for the budget alert. Empty skips the budget."
  type        = string
  default     = ""
}

variable "monthly_budget" {
  description = "Monthly budget in the billing account's currency, with e-mail alerts at 50, 90 and 100 percent"
  type        = number
  default     = 100
}

variable "scheduler_region" {
  description = "Cloud Scheduler region (not every Cloud Run region has Scheduler)"
  type        = string
  default     = "europe-west1"
}

variable "project_id" {
  description = "GCP project ID to deploy the Cloud Scheduler job into"
  type        = string
}

variable "region" {
  description = "Region for Cloud Scheduler"
  type        = string
  default     = "asia-northeast1"
}

variable "github_owner" {
  description = "GitHub repository owner (user or org)"
  type        = string
  default     = "tokyo3am"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "jkkwatcher"
}

variable "github_workflow_file" {
  description = "GitHub Actions workflow filename to dispatch"
  type        = string
  default     = "watch.yml"
}

variable "github_ref" {
  description = "Git ref to run the workflow against"
  type        = string
  default     = "main"
}

variable "github_pat_secret_id" {
  description = "Secret Manager secret ID that stores the fine-grained GitHub PAT (Actions:write). The latest version is read at apply time."
  type        = string
  default     = "jkkwatcher-github-pat"
}

variable "schedule" {
  description = "Cron schedule for triggering JKK+UR watchers"
  type        = string
  default     = "*/5 * * * *"
}

variable "schedule_suumo" {
  description = "Cron schedule for triggering the Suumo watcher (lower frequency to avoid load)"
  type        = string
  default     = "0 * * * *"
}

variable "time_zone" {
  description = "Time zone for the cron schedule"
  type        = string
  default     = "Asia/Tokyo"
}

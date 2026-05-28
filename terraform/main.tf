provider "google" {
  project = var.project_id
  region  = var.region
}

# 必要な API を有効化
resource "google_project_service" "cloud_scheduler" {
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secret_manager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# Secret Manager から PAT を読み出す (apply 時に取得 → Scheduler config に展開)
data "google_secret_manager_secret_version" "github_pat" {
  secret = var.github_pat_secret_id

  depends_on = [google_project_service.secret_manager]
}

locals {
  workflow_dispatch_url = format(
    "https://api.github.com/repos/%s/%s/actions/workflows/%s/dispatches",
    var.github_owner,
    var.github_repo,
    var.github_workflow_file,
  )
}

resource "google_cloud_scheduler_job" "jkkwatcher_trigger" {
  name        = "jkkwatcher-trigger"
  description = "Trigger jkkwatcher GitHub Actions workflow for JKK+UR on a tight schedule"
  schedule    = var.schedule
  time_zone   = var.time_zone
  region      = var.region

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = local.workflow_dispatch_url

    headers = {
      "Accept"               = "application/vnd.github+json"
      "X-GitHub-Api-Version" = "2022-11-28"
      "Authorization"        = "Bearer ${data.google_secret_manager_secret_version.github_pat.secret_data}"
      "Content-Type"         = "application/json"
      "User-Agent"           = "cloud-scheduler-jkkwatcher"
    }

    body = base64encode(jsonencode({
      ref    = var.github_ref
      inputs = { targets = "jkk,ur" }
    }))
  }

  depends_on = [google_project_service.cloud_scheduler]
}

resource "google_cloud_scheduler_job" "jkkwatcher_suumo_trigger" {
  name        = "jkkwatcher-suumo-trigger"
  description = "Trigger jkkwatcher GitHub Actions workflow for Suumo (lower frequency)"
  schedule    = var.schedule_suumo
  time_zone   = var.time_zone
  region      = var.region

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = local.workflow_dispatch_url

    headers = {
      "Accept"               = "application/vnd.github+json"
      "X-GitHub-Api-Version" = "2022-11-28"
      "Authorization"        = "Bearer ${data.google_secret_manager_secret_version.github_pat.secret_data}"
      "Content-Type"         = "application/json"
      "User-Agent"           = "cloud-scheduler-jkkwatcher"
    }

    body = base64encode(jsonencode({
      ref    = var.github_ref
      inputs = { targets = "suumo" }
    }))
  }

  depends_on = [google_project_service.cloud_scheduler]
}

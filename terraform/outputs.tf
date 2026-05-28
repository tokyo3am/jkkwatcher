output "scheduler_job_name" {
  description = "Cloud Scheduler job resource name (JKK+UR)"
  value       = google_cloud_scheduler_job.jkkwatcher_trigger.id
}

output "scheduler_suumo_job_name" {
  description = "Cloud Scheduler job resource name (Suumo)"
  value       = google_cloud_scheduler_job.jkkwatcher_suumo_trigger.id
}

output "workflow_dispatch_url" {
  description = "GitHub API URL invoked by the scheduler"
  value       = local.workflow_dispatch_url
}

output "github_pat_secret_version" {
  description = "Resolved Secret Manager version path used at apply time"
  value       = data.google_secret_manager_secret_version.github_pat.name
}

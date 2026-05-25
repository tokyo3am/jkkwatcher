terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # state を GCS で管理したい場合は以下を有効化:
  # backend "gcs" {
  #   bucket = "your-tf-state-bucket"
  #   prefix = "jkkwatcher"
  # }
}

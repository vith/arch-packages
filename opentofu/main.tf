# Stable v5.26.0 separates identity/settings from Worker versions/deployments.
# https://github.com/cloudflare/terraform-provider-cloudflare/releases/tag/v5.26.0
# https://github.com/cloudflare/terraform-provider-cloudflare/blob/v5.26.0/docs/resources/worker.md
# https://github.com/cloudflare/terraform-provider-cloudflare/blob/v5.26.0/docs/resources/workers_custom_domain.md
terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.26.0"
    }
  }
  backend "local" {
    path = "infrastructure.tfstate"
  }
  encryption {
    key_provider "pbkdf2" "local" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "local" {
      keys = key_provider.pbkdf2.local
    }
    state {
      method   = method.aes_gcm.local
      enforced = true
    }
    plan {
      method   = method.aes_gcm.local
      enforced = true
    }
  }
}

variable "state_passphrase" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.state_passphrase) >= 32
    error_message = "Provide a separate persistent recovery secret of at least 32 characters."
  }
}
variable "account_id" {
  type = string
}
variable "zone_id" {
  type = string
}
variable "attach_domain" {
  type    = bool
  default = false
}
variable "verified_version_id" {
  type    = string
  default = ""
}
provider "cloudflare" {}

resource "cloudflare_worker" "repository" {
  account_id = var.account_id
  name       = "n3t-arch-packages"
  logpush    = false
  subdomain = {
    enabled          = false
    previews_enabled = false
  }
  observability = {
    enabled = false
  }
  lifecycle {
    prevent_destroy = true
  }
}

# Bootstrap sets this only AFTER public hashes/signatures, upload and 100% readback.
# No code or deployment resources belong in infrastructure state.
resource "cloudflare_workers_custom_domain" "repository" {
  count      = var.attach_domain ? 1 : 0
  account_id = var.account_id
  hostname   = "arch.packages.n3t.work"
  service    = cloudflare_worker.repository.name
  zone_id    = var.zone_id
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = length(var.verified_version_id) > 0
      error_message = "Deploy and verify the first complete signed Worker version before binding the hostname."
    }
  }
}
output "worker_id" {
  value = cloudflare_worker.repository.id
}

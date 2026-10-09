terraform {
  required_version = ">= 1.10.0"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "6.13.0"
    }
  }
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }
    state {
      method   = method.aes_gcm.state
      enforced = true
    }
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}

variable "state_passphrase" {
  type      = string
  sensitive = true
}

variable "signing_directory" {
  type    = string
  default = "~/.local/state/arch-packages/signing"
}

provider "github" {
  owner = "vith"
}

resource "github_repository" "packages" {
  name                   = "arch-packages"
  description            = "Signed native Arch packages built independently on GitHub Actions"
  visibility             = "public"
  has_issues             = true
  has_projects           = true
  has_wiki               = true
  allow_merge_commit     = true
  allow_squash_merge     = false
  allow_rebase_merge     = false
  allow_auto_merge       = false
  delete_branch_on_merge = false
  archive_on_destroy     = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "github_branch_default" "main" {
  repository = github_repository.packages.name
  branch     = "main"
}

resource "github_actions_repository_permissions" "packages" {
  repository           = github_repository.packages.name
  enabled              = true
  allowed_actions      = "all"
  sha_pinning_required = false
}

resource "github_repository_ruleset" "source_review" {
  name        = "source-review-immutable"
  repository  = github_repository.packages.name
  target      = "tag"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/tags/source-review-*"]
      exclude = []
    }
  }

  rules {
    update   = true
    deletion = true
  }
}

resource "github_branch_protection" "main" {
  repository_id       = github_repository.packages.node_id
  pattern             = "main"
  enforce_admins      = true
  allows_deletions    = false
  allows_force_pushes = false
  required_status_checks {
    strict   = true
    contexts = ["verify", "recipe-policy", "candidate-build"]
  }
  required_pull_request_reviews {
    required_approving_review_count = 0
    dismiss_stale_reviews           = true
  }
}

resource "github_branch_protection" "recipes" {
  repository_id           = github_repository.packages.node_id
  pattern                 = "pkg/*"
  enforce_admins          = true
  allows_deletions        = false
  allows_force_pushes     = false
  required_linear_history = false
  required_status_checks {
    strict   = true
    contexts = ["verify", "recipe-policy", "candidate-build"]
  }
  required_pull_request_reviews {
    required_approving_review_count = 0
    dismiss_stale_reviews           = true
  }
}

resource "terraform_data" "source_updates" {
  triggers_replace = [github_repository.packages.name, "update.yml", "active"]
  provisioner "local-exec" {
    command = "gh api --method PUT repos/vith/arch-packages/actions/workflows/update.yml/enable"
  }
}

resource "github_workflow_repository_permissions" "packages" {
  repository                       = github_repository.packages.name
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = true
}

resource "github_repository_environment" "publish" {
  repository        = github_repository.packages.name
  environment       = "publish"
  can_admins_bypass = true
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "publish_main" {
  repository     = github_repository.packages.name
  environment    = github_repository_environment.publish.environment
  branch_pattern = "main"
}

resource "github_repository_environment" "recipe_review" {
  repository          = github_repository.packages.name
  environment         = "recipe-review"
  can_admins_bypass   = false
  prevent_self_review = false
  reviewers {
    users = [3265539]
  }
}

# Retain the environment ID for historical producer proof, without new approvals.
resource "github_repository_environment" "code_review" {
  repository          = github_repository.packages.name
  environment         = "code-review"
  can_admins_bypass   = false
  prevent_self_review = false
}

resource "github_actions_environment_secret" "signing" {
  for_each = {
    ARCH_SIGNING_KEY        = "private.asc"
    ARCH_SIGNING_PASSPHRASE = "passphrase"
  }
  repository  = github_repository.packages.name
  environment = github_repository_environment.publish.environment
  secret_name = each.key
  value       = sensitive(file("${pathexpand(var.signing_directory)}/${each.value}"))
}

resource "github_actions_environment_variable" "fingerprint" {
  repository    = github_repository.packages.name
  environment   = github_repository_environment.publish.environment
  variable_name = "ARCH_SIGNING_FINGERPRINT"
  value         = trimspace(file("${path.module}/../keys/fingerprint"))
}

resource "github_repository" "source_fork" {
  name                   = "oh-my-pi"
  description            = "⌥ Coding agent with the IDE wired in. Built by Stencil Labs."
  homepage_url           = "https://omp.sh"
  visibility             = "public"
  has_issues             = false
  has_projects           = true
  has_wiki               = true
  allow_merge_commit     = true
  allow_squash_merge     = true
  allow_rebase_merge     = true
  allow_auto_merge       = false
  delete_branch_on_merge = false

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = github_repository.source_fork
  id = "oh-my-pi-vith"
}

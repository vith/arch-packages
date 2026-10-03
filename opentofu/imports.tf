import {
  to = github_repository.packages
  id = "arch-packages"
}
import {
  to = github_branch_default.main
  id = "arch-packages"
}
import {
  to = github_actions_repository_permissions.packages
  id = "arch-packages"
}
import {
  to = github_workflow_repository_permissions.packages
  id = "arch-packages"
}
import {
  to = github_repository_environment.publish
  id = "arch-packages:publish"
}
import {
  to = github_repository_environment.recipe_review
  id = "arch-packages:recipe-review"
}
import {
  to = github_repository_environment_deployment_policy.publish_main
  id = "arch-packages:publish:61780424"
}
import {
  to = github_actions_environment_secret.signing["ARCH_SIGNING_KEY"]
  id = "arch-packages:publish:ARCH_SIGNING_KEY"
}
import {
  to = github_actions_environment_secret.signing["ARCH_SIGNING_PASSPHRASE"]
  id = "arch-packages:publish:ARCH_SIGNING_PASSPHRASE"
}
import {
  to = github_actions_environment_variable.fingerprint
  id = "arch-packages:publish:ARCH_SIGNING_FINGERPRINT"
}

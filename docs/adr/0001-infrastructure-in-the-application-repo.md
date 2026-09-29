# 1. Keep infrastructure in the application repository

- Status: accepted
- Date: 2026-09-29

## Context

elt-suite needs AWS infrastructure: a container registry, a Fargate task, a network and a Postgres warehouse. That Terraform could live in this repository or in a separate infrastructure repository.

## Decision

Keep it here, under `infra/`:

- `infra/modules/`: reusable modules (`network`, `registry`, `warehouse`, `runner`), each with its own `terraform test` suite.
- `infra/envs/dev/`: the root module that composes them, with an S3 backend.

The bootstrap pieces (the state bucket and the GitHub OIDC provider and roles) are created once by hand, following the AWS setup guide. That way CI never manages the permissions it runs with.

## Consequences

- One pull request can change the code, the image and the infrastructure together. One CI pipeline tests, builds, plans and deploys them in order.
- Reviewers see the whole system in one place.
- If other applications start sharing this infrastructure, or a separate team takes ownership of it, `infra/modules/` should move to its own versioned repository.

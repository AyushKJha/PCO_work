# AgentPGO AWS production infrastructure

This module provisions the backend V1 baseline in `us-east-1`:

- A `10.20.0.0/16` VPC with two public and two private subnets and an S3 gateway endpoint. Fargate tasks run in public subnets with public IPs; RDS remains private.
- Public HTTPS ALB with HTTP redirect, an ACM certificate (or an existing certificate ARN), and an alias record in an existing public Route53 zone.
- ECS Fargate cluster with separately deployable API and worker services, public-subnet task networking, container health checks, and encrypted CloudWatch logs.
- One immutable, scan-on-push ECR repository (agentpgo-backend) shared by API and worker task definitions.
- Private, encrypted, Single-AZ PostgreSQL 16 RDS with backups and deletion protection in `prod`.
- SQS jobs queue with a 5-attempt DLQ policy and an encrypted, versioned, private S3 artifacts bucket with lifecycle cleanup.
- KMS-backed Secrets Manager runtime secret, service-specific IAM roles, CloudWatch alarms/SNS notifications, and an account-level AWS Budget.

## Required inputs

Production HTTPS mode requires an existing public Route53 hosted zone. Supply either
`route53_zone_id` (recommended) or `route53_zone_name`, plus the API hostname.

For temporary smoke testing without a domain, set `temporary_http = true`. Terraform
then exposes the ALB-generated AWS DNS name over HTTP and skips Route53/ACM. This mode
is not production-safe and should be replaced with HTTPS before customer traffic.


```hcl
# terraform.tfvars (do not commit secrets)
aws_account_id    = "123456789012"
route53_zone_id   = "Z0123456789EXAMPLE"
domain_name       = "api.example.com"
certificate_arn   = "arn:aws:acm:us-east-1:123456789012:certificate/..." # optional
```

If `certificate_arn` is omitted, Terraform requests a DNS-validated ACM
certificate and writes the validation records into the existing zone. The
certificate and ALB must be in `us-east-1`.

## Container images

By default, task definitions use the agentpgo-backend ECR repository created by this
module and `image_tag`. Push immutable images before the ECS services start:

```bash
terraform output -raw ecr_api_repository_url
```

Use `api_image` and `worker_image` for images in another repository. The
repository includes the worker at `services.worker.main`; the default command
starts it with `python -m services.worker.main`. Override `worker_command` only
when supplying a custom worker image.

## Apply

```bash
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

This module creates billable AWS resources. Review the plan, especially the RDS
instance and monthly budget inputs, before applying. Email SNS
subscriptions and Budget notifications require confirmation from each recipient.

The generated database password and `DATABASE_URL` are stored in Terraform state
through the Secrets Manager version. Use an encrypted remote backend with state
locking and restrict state access. Provider API credentials are intentionally not
placed in source or Terraform variables; add any provider secrets to the runtime
secret out of band and update task definition wiring if the application needs
them.

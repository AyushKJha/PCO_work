locals {
  name       = "${var.project_name}-${var.environment}"
  account_id = coalesce(var.aws_account_id, data.aws_caller_identity.current.account_id)

  default_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  az_count = length(var.availability_zones)

  public_subnet_cidrs = [
    "10.20.0.0/20",
    "10.20.16.0/20",
  ]

  private_subnet_cidrs = [
    "10.20.128.0/20",
    "10.20.144.0/20",
  ]

  route53_zone_id = var.temporary_http ? null : (var.route53_zone_id != null ? var.route53_zone_id : try(data.aws_route53_zone.existing[0].zone_id, null))

  certificate_arn = var.temporary_http ? null : (var.certificate_arn != null ? var.certificate_arn : aws_acm_certificate_validation.api[0].certificate_arn)

  api_image    = var.api_image != null ? var.api_image : "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
  worker_image = var.worker_image != null ? var.worker_image : "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"

  db_url = "postgresql+psycopg://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"

  common_environment = [
    { name = "ENVIRONMENT", value = var.environment },
    { name = "AWS_REGION", value = "us-east-1" },
    { name = "LOG_LEVEL", value = "INFO" },
    { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
    { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.id },
  ]
}

data "aws_route53_zone" "existing" {
  count        = var.route53_zone_id == null && var.route53_zone_name != null ? 1 : 0
  name         = var.route53_zone_name
  private_zone = false
}

output "api_gateway_invoke_url" {
  description = "HTTPS invoke URL for the API Gateway HTTP API bridge."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "api_url" {
  description = "API URL. Temporary HTTP mode returns the ALB DNS endpoint; production mode returns the HTTPS domain."
  value       = var.temporary_http ? "http://${aws_lb.api.dns_name}" : "https://${var.domain_name}"
}

output "api_load_balancer_dns_name" {
  description = "ALB DNS name."
  value       = aws_lb.api.dns_name
}

output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private ECS/RDS subnet IDs."
  value       = aws_subnet.private[*].id
}

output "ecr_api_repository_url" {
  description = "API ECR repository URL."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_worker_repository_url" {
  description = "Worker ECR repository URL."
  value       = aws_ecr_repository.backend.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "ecs_api_service_name" {
  description = "ECS API service name."
  value       = aws_ecs_service.api.name
}

output "ecs_worker_service_name" {
  description = "ECS worker service name."
  value       = aws_ecs_service.worker.name
}

output "migration_task_definition_arn" {
  description = "One-off ECS task definition used to run Alembic migrations."
  value       = aws_ecs_task_definition.migration.arn
}

output "rds_endpoint" {
  description = "Private RDS endpoint without credentials."
  value       = aws_db_instance.main.address
}

output "jobs_queue_url" {
  description = "SQS jobs queue URL."
  value       = aws_sqs_queue.jobs.url
}

output "jobs_dlq_url" {
  description = "SQS dead-letter queue URL."
  value       = aws_sqs_queue.dead_letter.url
}

output "artifacts_bucket_name" {
  description = "Private versioned S3 artifacts bucket."
  value       = aws_s3_bucket.artifacts.id
}

output "runtime_secret_arn" {
  description = "Secrets Manager runtime secret ARN."
  value       = aws_secretsmanager_secret.runtime.arn
}

output "application_kms_key_arn" {
  description = "KMS key used for application data encryption."
  value       = aws_kms_key.application.arn
}

output "cloudwatch_alert_topic_arn" {
  description = "SNS topic receiving CloudWatch alarm notifications."
  value       = aws_sns_topic.alerts.arn
}

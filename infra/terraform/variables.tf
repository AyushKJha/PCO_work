variable "project_name" {
  description = "Short name used in AWS resource names."
  type        = string
  default     = "agentpgo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.project_name))
    error_message = "project_name must be 2-25 lowercase letters, numbers, or hyphens and start with a letter."
  }
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must be a short lowercase name."
  }
}

variable "aws_account_id" {
  description = "Expected AWS account ID. If null, the provider caller identity is used."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.aws_account_id == null || can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "availability_zones" {
  description = "Exactly two us-east-1 availability zones."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) == 2 && alltrue([for az in var.availability_zones : startswith(az, "us-east-1")])
    error_message = "availability_zones must contain exactly two us-east-1 zones."
  }
}

variable "route53_zone_id" {
  description = "Existing public Route53 hosted zone ID for the application domain."
  type        = string
  default     = null
  nullable    = true
}

variable "route53_zone_name" {
  description = "Existing public Route53 hosted zone name, used when route53_zone_id is null."
  type        = string
  default     = null
  nullable    = true
}

variable "temporary_http" {
  description = "Development-only mode: expose the ALB over its AWS HTTP DNS name without Route53/ACM."
  type        = bool
  default     = false
}

variable "domain_name" {
  description = "Fully qualified public hostname for the API. Required unless temporary_http is true."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.domain_name == null || can(regex("^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$", var.domain_name))
    error_message = "domain_name must be a valid DNS hostname when supplied."
  }
}

variable "certificate_arn" {
  description = "Existing ACM certificate ARN in us-east-1. Leave null to request and DNS-validate one in the existing zone."
  type        = string
  default     = null
  nullable    = true
}

variable "api_image" {
  description = "Optional complete API image URI. Defaults to the managed ECR repository and image_tag."
  type        = string
  default     = null
  nullable    = true
}

variable "worker_image" {
  description = "Optional complete worker image URI. Defaults to the managed ECR repository and image_tag."
  type        = string
  default     = null
  nullable    = true
}

variable "image_tag" {
  description = "Container tag used when api_image or worker_image is not supplied."
  type        = string
  default     = "v1"
}

variable "worker_command" {
  description = "Command for the worker container; provide the real worker entrypoint for the worker image."
  type        = list(string)
  default     = ["python", "-m", "services.worker.main"]
}

variable "api_desired_count" {
  description = "Number of API tasks."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Number of worker tasks."
  type        = number
  default     = 1
}

variable "api_cpu" {
  description = "API task CPU units."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "API task memory in MiB."
  type        = number
  default     = 1024
}

variable "worker_cpu" {
  description = "Worker task CPU units."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Worker task memory in MiB."
  type        = number
  default     = 2048
}

variable "db_name" {
  description = "Initial PostgreSQL database name."
  type        = string
  default     = "agentpgo"
}

variable "db_username" {
  description = "PostgreSQL master username."
  type        = string
  default     = "agentpgo"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GiB."
  type        = number
  default     = 20
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "monthly_budget_usd" {
  description = "AWS account monthly budget threshold in USD."
  type        = number
  default     = 150
}

variable "budget_email_addresses" {
  description = "Optional email addresses for AWS Budget and alarm notifications."
  type        = list(string)
  default     = []
}

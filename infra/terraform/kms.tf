resource "aws_kms_key" "application" {
  description             = "Encryption key for ${local.name} application data"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableAccountAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.us-east-1.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:us-east-1:${local.account_id}:log-group:/ecs/${local.name}*" } }
      }
    ]
  })

  tags = { Name = "${local.name}-application" }
}

resource "aws_kms_alias" "application" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.application.key_id
}

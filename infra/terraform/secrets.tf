resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${local.name}/runtime"
  description             = "AgentPGO runtime configuration; provider credentials can be added out of band."
  kms_key_id              = aws_kms_key.application.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode({
    DATABASE_URL = local.db_url
  })
}

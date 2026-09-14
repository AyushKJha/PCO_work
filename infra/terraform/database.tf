resource "random_password" "db" {
  length           = 32
  special          = true
  override_special = "!#$%&()*+,-.:;<=>?[]^_{|}~"
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name}-db" }
}

resource "aws_db_parameter_group" "main" {
  name   = local.name
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier                 = local.name
  engine                     = "postgres"
  engine_version             = "16.4"
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  max_allocated_storage      = var.db_allocated_storage * 2
  storage_type               = "gp3"
  storage_encrypted          = true
  kms_key_id                 = aws_kms_key.application.arn
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = random_password.db.result
  port                       = 5432
  db_subnet_group_name       = aws_db_subnet_group.main.name
  parameter_group_name       = aws_db_parameter_group.main.name
  vpc_security_group_ids     = [aws_security_group.rds.id]
  multi_az                   = false
  publicly_accessible        = false
  backup_retention_period    = 7
  backup_window              = "03:00-04:00"
  maintenance_window         = "sun:04:00-sun:05:00"
  copy_tags_to_snapshot      = true
  deletion_protection        = var.environment == "prod"
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${local.name}-final"
  auto_minor_version_upgrade = true

  depends_on = [aws_kms_alias.application]
}

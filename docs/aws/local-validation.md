# AWS V1 local validation

These checks simulate SQS duplicate delivery, S3 deletion, readiness, spend caps, migration-chain validation, and 100k-span ingestion without contacting AWS.

```bash
python scripts/validate_aws_migrations.py
python scripts/aws_load_test.py --spans 100000
pytest -q tests/aws
```

Live AWS behavior still requires an authorized Terraform apply and authenticated provider smoke test.

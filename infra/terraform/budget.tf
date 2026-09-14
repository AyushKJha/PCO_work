resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  account_id   = local.account_id
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = length(var.budget_email_addresses) > 0 ? toset([80, 100]) : toset([])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "FORECASTED"
      subscriber_email_addresses = var.budget_email_addresses
    }
  }
}

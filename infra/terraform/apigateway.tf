resource "aws_apigatewayv2_api" "api" {
  name          = "${local.name}-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "alb" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "HTTP_PROXY"
  integration_uri        = "http://${aws_lb.api.dns_name}"
  integration_method     = "ANY"
  payload_format_version = "1.0"
  # HTTP API proxy integrations otherwise strip the `/api/v1` route prefix.
  # The backend's canonical routes are `/v1/*`, so preserve the suffix while
  # rewriting it to that prefix at the HTTPS boundary.
  request_parameters = {
    "overwrite:path" = "/v1/$request.path.proxy"
  }
}

resource "aws_apigatewayv2_route" "api_v1" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /api/v1"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "api_v1_proxy" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /api/v1/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

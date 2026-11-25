# HTTP API：给游戏一个公网网址
resource "aws_apigatewayv2_api" "http" {
  name          = "wordchain"
  protocol_type = "HTTP"
}

# 把收到的请求原样转给 Lambda
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app.invoke_arn
  payload_format_version = "2.0"
}

# 所有路径（/、/api/...、/static/...）都交给 FastAPI 处理
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # 限流：防止有人刷请求、刷爆 Claude 的费用
  default_route_settings {
    throttling_rate_limit  = 10 # 每秒最多 10 个请求
    throttling_burst_limit = 20 # 瞬间最多 20 个
  }
}

# 允许 API Gateway 调用 Lambda
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

output "game_url" {
  description = "Open this URL to play WordChain"
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "secret_name" {
  value = aws_secretsmanager_secret.anthropic.name
}

# ---------- Lambda 的权限 ----------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "wordchain-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# 权限 1：写日志到 CloudWatch
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 权限 2：只能读取我们那一个 secret
data "aws_iam_policy_document" "read_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.anthropic.arn]
  }
}

resource "aws_iam_role_policy" "read_secret" {
  name   = "read-anthropic-secret"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.read_secret.json
}

# 权限 3：只能读写我们的两张表（和它们的索引），而且只开放用到的操作
data "aws_iam_policy_document" "dynamodb" {
  statement {
    actions = [
      "dynamodb:GetItem",    # 按 ID 读取
      "dynamodb:PutItem",    # 保存
      "dynamodb:DeleteItem", # 删除
      "dynamodb:Query",      # 用索引查找
    ]
    resources = [
      aws_dynamodb_table.sessions.arn,
      "${aws_dynamodb_table.sessions.arn}/index/*",
      aws_dynamodb_table.rooms.arn,
    ]
  }
}

resource "aws_iam_role_policy" "dynamodb" {
  name   = "wordchain-dynamodb"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.dynamodb.json
}

# ---------- 日志 ----------
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/wordchain"
  retention_in_days = 14 # 只保留 14 天，省钱
}

# ---------- Lambda 函数 ----------
resource "aws_lambda_function" "app" {
  function_name = "wordchain"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "main.handler" # main.py 里的 handler = Mangum(app)

  filename         = "${path.module}/../lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda.zip") # 代码变了才重新上传

  timeout     = 30  # 秒
  memory_size = 512 # MB，词典有 37 万个词

  environment {
    variables = {
      ANTHROPIC_SECRET_ARN = aws_secretsmanager_secret.anthropic.arn
      TABLE_NAME           = aws_dynamodb_table.sessions.name
      ROOMS_TABLE_NAME     = aws_dynamodb_table.rooms.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.lambda_logs]
}
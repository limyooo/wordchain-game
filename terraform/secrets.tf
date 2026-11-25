# 存放 Claude API key 的“保险箱”
# 这里只创建保险箱，不放 key。key 用命令单独存进去，这样它不会出现在代码和 Terraform 状态文件里
resource "aws_secretsmanager_secret" "anthropic" {
  name                    = "wordchain/anthropic-api-key"
  recovery_window_in_days = 0 # 删除时立即删除，方便反复部署测试
}

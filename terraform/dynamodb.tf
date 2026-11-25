resource "aws_dynamodb_table" "sessions" {
  name         = "wordchain-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  attribute {
    name = "player_id"
    type = "S"
  }
  attribute {
    name = "created_at"
    type = "S"
  }
  global_secondary_index {
    name = "player-created-index"

    key_schema {
      attribute_name = "player_id"
      key_type       = "HASH" # 按玩家分组
    }
    key_schema {
      attribute_name = "created_at"
      key_type       = "RANGE" # 组内按时间排序
    }

    projection_type    = "INCLUDE"
    non_key_attributes = ["name"]
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
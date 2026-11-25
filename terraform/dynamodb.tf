# ---------- 游戏记录表 ----------
resource "aws_dynamodb_table" "sessions" {
  name         = "wordchain-sessions"
  billing_mode = "PAY_PER_REQUEST" # 按量付费，没人玩几乎不花钱
  hash_key     = "id"              # 主键：游戏 ID

  # 只需要声明用来当键的字段
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
  attribute {
    name = "room_code"
    type = "S"
  }

  # 索引 1：按玩家查找他的所有游戏，按创建时间排序
  global_secondary_index {
    name = "player-created-index"

    key_schema {
      attribute_name = "player_id"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "created_at"
      key_type       = "RANGE"
    }

    projection_type    = "INCLUDE"
    non_key_attributes = ["name"]
  }

  # 索引 2：按房间查找所有人的游戏，给排行榜用
  # 稀疏索引：只有带 room_code 的游戏（房间游戏）才会出现在这里
  global_secondary_index {
    name = "room-created-index"

    key_schema {
      attribute_name = "room_code"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "created_at"
      key_type       = "RANGE"
    }

    projection_type    = "INCLUDE"
    non_key_attributes = ["nickname", "status", "rounds", "started_at", "finished_at", "late"] # 排行榜要显示的字段
  }

  # 自动过期：到了 expires_at 的时间，DynamoDB 自动删除这条记录
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# ---------- 房间表 ----------
resource "aws_dynamodb_table" "rooms" {
  name         = "wordchain-rooms"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "code" # 主键：房间码，比如 K7QX

  attribute {
    name = "code"
    type = "S"
  }

  # 房间 1 天后自动删除
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
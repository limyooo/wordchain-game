terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"

  # 给所有资源自动加上标签，方便在控制台里找到、统计费用
  default_tags {
    tags = {
      Project = "wordchain"
    }
  }
}

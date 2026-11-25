#!/bin/bash
# 打包 Lambda 代码：下载 Linux 版的依赖，加上我们的代码，压缩成 lambda.zip
set -e

rm -rf build lambda.zip
mkdir build

pip install -r requirements.txt \
  --target build \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --upgrade --quiet

cp main.py words.txt build/
cp -r static build/

cd build
zip -r -q ../lambda.zip .
cd ..

echo "Built lambda.zip"
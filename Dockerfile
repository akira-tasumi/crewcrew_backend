# ベースイメージ: Python 3.11 軽量版
FROM python:3.11-slim

# 作業ディレクトリの設定
WORKDIR /app

# システム依存パッケージのインストール
# rembg (背景除去) に必要なライブラリを追加
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Pythonの依存関係をインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードをコピー
COPY . .

# ポート8000を公開
EXPOSE 8000

# uvicornでアプリケーションを起動
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

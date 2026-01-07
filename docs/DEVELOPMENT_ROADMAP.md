# 開発ロードマップ・要件定義書

## 概要

本ドキュメントは、DXセレクトチャットボットシステムの機能拡張に関する開発ロードマップと必要要件を定義する。

---

## フェーズ1: 資料ダウンロードSlack通知の追加

### 1.1 概要
田角さん実装のapp.jsから送信される新規ログイベントを受信し、Slackへ通知する機能を追加する。

### 1.2 田角さん実装済みのログイベント（app.js）

| question | answer | 説明 |
|----------|--------|------|
| `document` | `email_form_opened` | 「資料をもらう」ボタンがクリックされた |
| `document_email_sent` | `企業名 / 氏名 / メールアドレス` | 資料請求メール送信成功 |
| `document_email_failed` | `企業名 / 氏名 / メールアドレス` | 資料請求メール送信失敗 |

### 1.3 データ形式（従来通り）
```json
{
  "sessionId": "1736234567890-abc123",
  "status": "click",
  "question": "document_email_sent",
  "answer": "株式会社サンプル / 山田太郎 / yamada@example.com"
}
```

### 1.4 必要な実装

#### 1.4.1 Slack通知サービスの拡張
**ファイル**: `services/slack_service.py`

```python
# 追加関数
def send_document_download_notification(
    session_id: str,
    company_name: str,
    user_name: str,
    email: str,
    event_type: str  # "sent" or "failed"
) -> bool:
    """資料ダウンロード通知をSlackに送信"""
```

#### 1.4.2 ログ受信エンドポイントの拡張
**ファイル**: 新規 `routers/chatbot_log.py` または既存ルーターに追加

```python
# エンドポイント
POST /api/chatbot/log

# リクエストボディ
{
    "sessionId": str,
    "status": str,
    "question": str,
    "answer": str
}
```

#### 1.4.3 通知内容フォーマット

**成功時（document_email_sent）**:
```
📥 資料ダウンロードがありました

企業名: 株式会社サンプル
氏名: 山田太郎
メールアドレス: yamada@example.com

セッションID: 1736234567890-abc123
```

**失敗時（document_email_failed）**:
```
⚠️ 資料ダウンロードでエラーが発生しました

企業名: 株式会社サンプル
氏名: 山田太郎
メールアドレス: yamada@example.com

セッションID: 1736234567890-abc123
```

### 1.5 受け入れ条件
- [ ] `document_email_sent`イベント受信時にSlack通知が送信される
- [ ] `document_email_failed`イベント受信時にエラー通知が送信される
- [ ] 通知に企業名・氏名・メールアドレスが含まれる
- [ ] CEMSデータベースにログが記録される

---

## フェーズ2: DXcatalog RAGシステムの開発

### 2.1 概要
CEMSのサービス情報データベースを元に、RAG（Retrieval-Augmented Generation）システムを構築する。

### 2.2 システム構成

```
┌─────────────────────────────────────────────────────────────┐
│                    DXcatalog RAGシステム                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐ │
│  │   Vector DB   │────▶│   RAG Engine  │────▶│  LLM (Bedrock)│ │
│  │  (FAISS等)    │     │  (LangChain)  │     │              │ │
│  └──────────────┘     └──────────────┘     └──────────────┘ │
│         ▲                                         │         │
│         │                                         ▼         │
│  ┌──────────────┐                         ┌──────────────┐ │
│  │ サービス情報DB │                         │  出力層      │ │
│  │  (CEMS)      │                         │              │ │
│  └──────────────┘                         └──────────────┘ │
│                                                   │         │
└───────────────────────────────────────────────────│─────────┘
                                                    │
                    ┌───────────────────────────────┼───────────────────────────────┐
                    │                               │                               │
                    ▼                               ▼                               ▼
           ┌──────────────┐                ┌──────────────┐                ┌──────────────┐
           │ 提案者向け    │                │ ユーザー向け  │                │ メール送信    │
           │ 最適解表示    │                │ 3選表示      │                │ (CEセールス)  │
           │ (DXカタログ)  │                │ (チャット)   │                │              │
           └──────────────┘                └──────────────┘                └──────────────┘
```

### 2.3 機能要件

#### 2.3.1 管理者（提案者）向け機能

**目的**: ITコーディネーター、ママさん等の提案者が、カタログから最適解を導き出せるようにする

**機能**:
1. チャットボットの会話履歴をもとに、ユーザーの課題・ニーズを分析
2. CEMSサービス情報DBから最適なサービスをランキング表示
3. 各サービスの詳細情報・比較ポイント・提案時のトークスクリプトを提供

**出力情報**:
| 項目 | 説明 | 優先度 |
|------|------|--------|
| サービス名 | 推奨サービスの名称 | 必須 |
| マッチ度スコア | ユーザーニーズとの適合度（0-100） | 必須 |
| 推奨理由 | なぜこのサービスが最適かの説明 | 必須 |
| 主要機能 | サービスの主要機能一覧 | 必須 |
| 料金プラン | 料金体系・価格帯 | 必須 |
| CE経由メリット | 代理店経由の割引・特典情報 | 高 |
| 導入事例 | 類似業種・規模の導入実績 | 中 |
| 競合比較 | 他サービスとの差別化ポイント | 中 |
| 提案トークスクリプト | ヒアリング時に使える質問例 | 低 |

**議論ポイント（要検討）**:
- どの情報が提案者にとって最も価値があるか？
- 料金情報はどこまで開示するか？
- 競合他社との比較情報をどう表現するか？

#### 2.3.2 ユーザー向け機能

**目的**: チャットボット経由でのリード獲得を最大化しつつ、ユーザーに価値を提供する

**機能**:
1. チャットボットの会話内容から課題を抽出
2. 課題に対して「オススメサービス3選」を提示（**チョイ出し**）
3. 詳細はCEへの相談を促す導線を作る

**情報開示レベル（2層構造）**:
```
【ユーザー向け（一次情報）】
- サービスカテゴリ
- サービス名
- 簡単な説明（1-2文）
- 「詳しくはCEにご相談ください」の導線

【提案者向け（最適解）】
- 上記 + 詳細情報すべて
```

**設計思想**:
- ユーザーには「こういうカテゴリのサービスが合いそう」レベルに留める
- 具体的なサービス名・料金比較は「メアド入力後」or「CE相談後」に出す
- リード獲得を先にする設計

### 2.4 API設計

#### 2.4.1 RAG検索API（内部用）

```python
POST /api/rag/search

# リクエスト
{
    "query": str,           # 検索クエリ（ユーザーの課題等）
    "conversation_history": list[dict],  # チャット履歴
    "mode": "user" | "admin",  # 出力モード
    "limit": int = 3        # 取得件数
}

# レスポンス（userモード）
{
    "recommendations": [
        {
            "service_name": "サービスA",
            "category": "CRM",
            "summary": "顧客管理を効率化するクラウドサービス",
            "match_score": 85
        }
    ],
    "cta_message": "詳しい比較や料金については、無料相談でご案内いたします"
}

# レスポンス（adminモード）
{
    "recommendations": [
        {
            "service_name": "サービスA",
            "category": "CRM",
            "summary": "顧客管理を効率化するクラウドサービス",
            "match_score": 85,
            "match_reason": "顧客管理の課題に最適。中小企業向けの価格帯で導入しやすい",
            "features": ["顧客管理", "商談管理", "レポート"],
            "pricing": "月額5,000円〜",
            "ce_benefits": "CE経由で初期費用50%OFF",
            "case_studies": ["製造業A社", "小売B社"],
            "talk_script": "現在の顧客管理はExcelですか？それとも..."
        }
    ]
}
```

#### 2.4.2 メール用サービス提案API（PHP連携）

```python
POST /api/rag/email-recommendations

# リクエスト
{
    "session_id": str,
    "conversation_summary": str,
    "user_email": str,
    "user_name": str,
    "company_name": str
}

# レスポンス
{
    "recommendations": [
        {
            "service_name": str,
            "category": str,
            "one_liner": str  # メール用の1行説明
        }
    ],
    "email_body_suggestion": str  # メール本文の提案
}
```

### 2.5 技術スタック

| コンポーネント | 技術選定 | 理由 |
|--------------|---------|------|
| Vector DB | FAISS or ChromaDB | 軽量・高速、既存環境との親和性 |
| Embedding | AWS Bedrock (Titan) | 既存のBedrock連携を活用 |
| LLM | AWS Bedrock (Claude) | 既存のBedrock連携を活用 |
| Framework | LangChain | 既に依存関係にあり、実装が容易 |
| API | FastAPI | 既存のバックエンドに統合 |

### 2.6 データ要件

#### 2.6.1 CEMSサービス情報DBの必要項目

```python
class ServiceInfo:
    id: int
    service_name: str           # サービス名
    category: str               # カテゴリ（CRM, ERP, etc.）
    description: str            # 詳細説明
    features: list[str]         # 機能一覧
    pricing_info: str           # 料金情報
    target_company_size: str    # 対象企業規模
    target_industry: list[str]  # 対象業種
    ce_benefits: str            # CE経由の特典
    official_url: str           # 公式サイトURL
    case_studies: list[dict]    # 導入事例
    embedding: list[float]      # ベクトル埋め込み（検索用）
```

#### 2.6.2 チャット履歴の構造

```python
class ChatHistory:
    session_id: str
    messages: list[dict]  # {"role": "user"|"assistant", "content": str}
    extracted_needs: list[str]  # 抽出されたニーズ
    company_info: dict  # 企業情報（業種、規模等）
```

---

## フェーズ3: PHP連携・メール送信

### 3.1 概要
RAGで抽出したオススメサービス3選をPHP側に送信し、CEセールスのメール送信機能と連携する。

### 3.2 連携フロー

```
[チャットボット(PHP)]
    │
    │ 1. 会話完了・資料DL
    ▼
[crewcrew_backend (Python/FastAPI)]
    │
    │ 2. RAG検索実行
    │ 3. オススメ3選抽出
    ▼
[PHP側に結果返却]
    │
    │ 4. メール本文生成
    │ 5. CEセールス連携
    ▼
[ユーザーへメール送信]
```

### 3.3 API仕様

```python
# Python側（crewcrew_backend）
POST /api/chatbot/recommendations

# リクエスト（PHP→Python）
{
    "session_id": str,
    "conversation": list[dict],
    "user_info": {
        "company_name": str,
        "name": str,
        "email": str
    }
}

# レスポンス（Python→PHP）
{
    "success": bool,
    "recommendations": [
        {
            "rank": 1,
            "service_name": str,
            "category": str,
            "summary": str,
            "ce_benefit": str | null
        }
    ],
    "email_snippet": str  # メールに挿入するHTMLスニペット
}
```

### 3.4 app.js編集内容（田角さん担当）

資料ダウンロード完了後、バックエンドAPIを呼び出してオススメ情報を取得し、メール送信に含める。

```javascript
// 資料DL完了時の処理に追加
async function handleDocumentDownloadComplete(sessionId, userInfo) {
    // 1. バックエンドにオススメ取得リクエスト
    const recommendations = await fetch('/api/chatbot/recommendations', {
        method: 'POST',
        body: JSON.stringify({
            session_id: sessionId,
            conversation: conversationHistory,
            user_info: userInfo
        })
    });

    // 2. メール送信（既存のCEセールス連携を利用）
    // recommendations.email_snippet をメール本文に含める
}
```

---

## 開発スケジュール

### 担当分担

| フェーズ | タスク | 担当 |
|---------|--------|------|
| 1 | 資料DL Slack通知実装 | 松本 |
| 2-1 | RAGシステム基盤構築 | 松本 |
| 2-2 | サービス情報DBスキーマ設計 | 松本 |
| 2-3 | 提案者向けUI/表示 | 松本 |
| 3-1 | PHP連携API実装 | 松本 |
| 3-2 | app.jsへのAPI連携追加 | 田角 |
| 3-3 | CEセールスメール連携 | 田角 |

### 優先順位

1. **最優先**: フェーズ1（資料DL Slack通知）
   - 既存の仕組みに追加するだけなので、最も早く価値を出せる

2. **次優先**: フェーズ2（RAGシステム）
   - 提案者向け機能を先に実装
   - ユーザー向けはリード獲得の導線を検討後に実装

3. **最後**: フェーズ3（PHP連携・メール送信）
   - RAGが動いてから実装

---

## 議論が必要な事項

### 1. 提案者向け情報の優先度
> ※ここでどのような情報がより提案者にとって有利な情報なのか議論する必要があります

- 料金情報の開示範囲（概算？詳細？）
- 競合比較の表現方法
- CE経由メリットの強調度合い

### 2. ユーザー離れ防止策
Slackでの議論を踏まえ、以下の対策を検討：

1. **情報の出し方をコントロール**
   - RAGは「こういうカテゴリが合いそう」レベルに留める
   - 具体的サービス名・料金比較は「メアド入力後」or「CE相談後」

2. **リード獲得を先にする設計**
   - 順番：チャットで課題ヒアリング → メアド取得 → おすすめ情報提供

3. **CE経由のメリット明示**
   - 「CE経由なら初期費用割引」等

### 3. 将来的なPython移行
> RAGがpythonで組むなら最終チャットボットもバックエンドpythonに置き換えても良いかも

- 現時点はPHPチャットボットを維持
- RAG連携が安定後、段階的にPython移行を検討

---

## 補足：既存システム構成

```
【現在の構成】
チャットボット(PHP + app.js)
    ↓ ログ送信
crewcrew_backend (Python/FastAPI)
    ├── CEMSログ記録
    ├── DXカタログ表示
    └── Slack通知

【Slack通知の種類（現在）】
- ✅ 完了
- 📥 資料ダウンロードへ進みました
- 📅 TIMEREXに進みました

【追加予定】
- 📥 資料ダウンロードされました（メール送信成功）
- ⚠️ 資料ダウンロードエラー（メール送信失敗）
```

---

## 変更履歴

| 日付 | 変更内容 | 担当 |
|------|---------|------|
| 2026-01-07 | 初版作成 | Claude |

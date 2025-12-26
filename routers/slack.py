"""
Slack Events API ルーター

@CrewCrew メンションに反応してクルーに依頼を送る
"""

import hashlib
import hmac
import os
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

router = APIRouter(prefix="/api/slack", tags=["slack"])

# 環境変数から設定を読み込み
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")


def verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """
    Slackリクエストの署名を検証する

    Args:
        request_body: リクエストのボディ（バイト列）
        timestamp: X-Slack-Request-Timestamp ヘッダー
        signature: X-Slack-Signature ヘッダー

    Returns:
        bool: 署名が有効な場合はTrue
    """
    if not SLACK_SIGNING_SECRET:
        print("[Slack] Warning: SLACK_SIGNING_SECRET is not set")
        return False

    # タイムスタンプが古すぎる場合は拒否（リプレイ攻撃対策）
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False

    # 署名を計算
    sig_basestring = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(my_signature, signature)


def process_mention(channel: str, user: str, text: str, ts: str):
    """
    メンションを処理してSlackに返信する（バックグラウンド処理）

    Args:
        channel: チャンネルID
        user: ユーザーID
        text: メンションのテキスト
        ts: メッセージのタイムスタンプ
    """
    print(f"[Slack] process_mention started - channel: {channel}, token exists: {bool(SLACK_BOT_TOKEN)}")

    if not SLACK_BOT_TOKEN:
        print("[Slack] Error: SLACK_BOT_TOKEN is not set")
        return

    try:
        client = WebClient(token=SLACK_BOT_TOKEN)

        # メンションから@CrewCrewを除去してタスク内容を取得
        task_text = text.replace("<@", "").split(">", 1)[-1].strip()

        print(f"[Slack] Sending message to channel {channel}...")

        # 受付完了メッセージを送信
        response = client.chat_postMessage(
            channel=channel,
            text=f"クルーが依頼を受け付けました！\n\n依頼内容: {task_text if task_text else '(内容なし)'}",
            thread_ts=ts  # スレッドで返信
        )

        print(f"[Slack] Message sent successfully: {response['ok']}")

        # TODO: ここで実際のクルー処理を呼び出す
        # from services.bedrock_service import execute_task_with_crew
        # result = execute_task_with_crew(task_text, ...)
        # client.chat_postMessage(channel=channel, text=result, thread_ts=ts)

    except SlackApiError as e:
        print(f"[Slack] API Error: {e.response['error']}")
        print(f"[Slack] Full error response: {e.response}")
    except Exception as e:
        print(f"[Slack] Unexpected error: {type(e).__name__}: {e}")


@router.post("/events")
async def handle_slack_events(request: Request, background_tasks: BackgroundTasks):
    """
    Slack Events APIのエンドポイント

    - URL検証 (challenge) に対応
    - app_mention イベントを処理
    - 署名検証でセキュリティを確保
    """
    # リクエストボディを取得
    body = await request.body()
    body_json: dict[str, Any] = await request.json()

    # Slack署名を検証
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        print("[Slack] Invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # URL検証 (Slack設定時のchallenge)
    if "challenge" in body_json:
        return {"challenge": body_json["challenge"]}

    # イベント処理
    event = body_json.get("event", {})
    event_type = event.get("type", "")

    if event_type == "app_mention":
        channel = event.get("channel", "")
        user = event.get("user", "")
        text = event.get("text", "")
        ts = event.get("ts", "")

        print(f"[Slack] Received mention from {user} in {channel}: {text}")

        # 直接実行（デバッグ用）
        try:
            process_mention(channel, user, text, ts)
        except Exception as e:
            print(f"[Slack] Error in process_mention: {e}")

        # 即時レスポンス
        return {"status": "ok"}

    # その他のイベントは無視
    return {"status": "ok"}


@router.get("/health")
async def slack_health():
    """Slack連携のヘルスチェック"""
    return {
        "status": "ok",
        "bot_token_configured": bool(SLACK_BOT_TOKEN),
        "signing_secret_configured": bool(SLACK_SIGNING_SECRET)
    }

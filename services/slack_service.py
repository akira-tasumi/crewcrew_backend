"""
Slack通知サービス

Webhook URLを使用してSlackチャンネルにメッセージを送信する
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)


def send_notification(message: str, title: str = None) -> bool:
    """
    SlackにWebhook経由でメッセージを送信する

    Args:
        message: 送信するメッセージ本文
        title: オプションのタイトル（太字で表示）

    Returns:
        bool: 送信成功時True、失敗時False
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")

    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL is not set. Skipping Slack notification.")
        return False

    try:
        # Slackメッセージのフォーマット
        blocks = []

        if title:
            blocks.append({
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": title,
                    "emoji": True
                }
            })

        # メッセージ本文を複数ブロックに分割（Slackのsectionは3000文字制限）
        # 全文を送信するため、3000文字ごとに分割
        remaining_message = message
        while remaining_message:
            chunk = remaining_message[:3000]
            remaining_message = remaining_message[3000:]

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": chunk
                }
            })

        # フッター
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "🤖 _Sent from クルクル Director Mode_"
                }
            ]
        })

        payload = {
            "blocks": blocks,
            "text": title or "クルクルからの通知"  # フォールバックテキスト
        }

        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30  # 長いメッセージ用にタイムアウト延長
        )

        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.error(f"Slack notification failed: {response.status_code} - {response.text}")
            return False

    except requests.exceptions.Timeout:
        logger.error("Slack notification timed out")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Slack notification error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending Slack notification: {e}")
        return False


def send_project_completion(project_title: str, task_summaries: list[dict]) -> bool:
    """
    プロジェクト完了通知を送信する（全文送信）

    Args:
        project_title: プロジェクト名
        task_summaries: タスク結果のリスト [{role, crew_name, result}, ...]

    Returns:
        bool: 送信成功時True
    """
    # タスク結果の全文を作成
    summary_lines = []
    for i, task in enumerate(task_summaries, 1):
        result_full = task.get("result", "（結果なし）")

        summary_lines.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*{i}. {task.get('role', 'タスク')}* （担当: {task.get('crew_name', '担当者')}）\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{result_full}"
        )

    message = "\n\n".join(summary_lines)

    return send_notification(
        message=message,
        title=f"✅ プロジェクト完了: {project_title}"
    )

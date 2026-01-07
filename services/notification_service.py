"""
通知・ログサービス

アプリ内通知の作成とアクティビティログの記録を行う。
将来的にSlack/Chatwork等の外部連携を追加しやすいよう設計。
"""

import json
import logging
from datetime import datetime
from typing import Optional, Any
from enum import Enum

from sqlalchemy.orm import Session

from models import Notification, ActivityLog, now_jst

logger = logging.getLogger(__name__)


# =============================================================================
# 通知タイプ・ログレベルの定義
# =============================================================================

class NotificationType(str, Enum):
    """通知タイプ"""
    SUCCESS = "success"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


class LogLevel(str, Enum):
    """ログレベル"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogAction(str, Enum):
    """ログアクション（拡張可能）"""
    PROJECT_STARTED = "project_started"
    PROJECT_COMPLETED = "project_completed"
    PROJECT_FAILED = "project_failed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    RESEARCH_STARTED = "research_started"
    RESEARCH_COMPLETED = "research_completed"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    SYSTEM_EVENT = "system_event"


# =============================================================================
# 通知サービス
# =============================================================================

def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    notification_type: NotificationType = NotificationType.INFO,
    link: Optional[str] = None,
    channels: Optional[list[str]] = None,
) -> Notification:
    """
    ユーザーへの通知を作成する

    Args:
        db: データベースセッション
        user_id: 通知対象のユーザーID
        title: 通知タイトル
        message: 通知メッセージ
        notification_type: 通知タイプ (success/error/info/warning)
        link: クリック時の遷移先URL
        channels: 外部通知チャンネル（将来用）["slack", "chatwork"]等

    Returns:
        作成されたNotificationオブジェクト
    """
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type.value if isinstance(notification_type, NotificationType) else notification_type,
        link=link,
        is_read=False,
        created_at=now_jst(),
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)

    logger.info(f"Notification created: user_id={user_id}, title={title}, type={notification_type}")

    # 将来的な外部連携（現時点ではログのみ）
    if channels:
        _send_to_external_channels(channels, title, message, notification_type)

    return notification


def _send_to_external_channels(
    channels: list[str],
    title: str,
    message: str,
    notification_type: NotificationType,
) -> None:
    """
    外部チャンネルへの通知（将来的な拡張用プレースホルダー）

    Args:
        channels: 送信先チャンネルのリスト
        title: 通知タイトル
        message: 通知メッセージ
        notification_type: 通知タイプ
    """
    for channel in channels:
        if channel == "slack":
            # TODO: slack_service.send_notification()を呼び出す
            logger.debug(f"Slack notification placeholder: {title}")
        elif channel == "chatwork":
            # TODO: chatwork_service実装後に連携
            logger.debug(f"Chatwork notification placeholder: {title}")
        else:
            logger.warning(f"Unknown notification channel: {channel}")


def get_notifications(
    db: Session,
    user_id: int,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    """
    ユーザーの通知一覧を取得する

    Args:
        db: データベースセッション
        user_id: ユーザーID
        unread_only: 未読のみ取得するか
        limit: 取得件数上限
        offset: 取得開始位置

    Returns:
        通知のリスト
    """
    query = db.query(Notification).filter(Notification.user_id == user_id)

    if unread_only:
        query = query.filter(Notification.is_read == False)

    return query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()


def get_unread_count(db: Session, user_id: int) -> int:
    """
    未読通知の件数を取得する

    Args:
        db: データベースセッション
        user_id: ユーザーID

    Returns:
        未読通知の件数
    """
    return db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).count()


def mark_as_read(db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
    """
    通知を既読にする

    Args:
        db: データベースセッション
        notification_id: 通知ID
        user_id: ユーザーID（権限確認用）

    Returns:
        更新された通知、または存在しない場合None
    """
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).first()

    if notification:
        notification.is_read = True
        db.commit()
        db.refresh(notification)
        logger.debug(f"Notification marked as read: id={notification_id}")

    return notification


def mark_all_as_read(db: Session, user_id: int) -> int:
    """
    全ての通知を既読にする

    Args:
        db: データベースセッション
        user_id: ユーザーID

    Returns:
        更新された件数
    """
    count = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).update({"is_read": True})

    db.commit()
    logger.info(f"Marked {count} notifications as read for user {user_id}")

    return count


# =============================================================================
# ログサービス
# =============================================================================

def write_log(
    db: Session,
    user_id: int,
    action: LogAction | str,
    message: str,
    level: LogLevel = LogLevel.INFO,
    project_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
) -> ActivityLog:
    """
    アクティビティログを記録する

    Args:
        db: データベースセッション
        user_id: ユーザーID
        action: アクション種別
        message: ログメッセージ
        level: ログレベル
        project_id: 関連プロジェクトID（任意）
        details: 追加情報（JSON形式で保存）

    Returns:
        作成されたActivityLogオブジェクト
    """
    activity_log = ActivityLog(
        user_id=user_id,
        project_id=project_id,
        level=level.value if isinstance(level, LogLevel) else level,
        action=action.value if isinstance(action, LogAction) else action,
        message=message,
        details=json.dumps(details, ensure_ascii=False) if details else None,
        created_at=now_jst(),
    )

    db.add(activity_log)
    db.commit()
    db.refresh(activity_log)

    # ログレベルに応じたPythonロギング
    log_message = f"[{action}] user_id={user_id}, project_id={project_id}: {message}"
    if level == LogLevel.ERROR:
        logger.error(log_message)
    elif level == LogLevel.WARNING:
        logger.warning(log_message)
    elif level == LogLevel.DEBUG:
        logger.debug(log_message)
    else:
        logger.info(log_message)

    return activity_log


def get_logs(
    db: Session,
    user_id: int,
    project_id: Optional[int] = None,
    level: Optional[LogLevel] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ActivityLog]:
    """
    アクティビティログを取得する

    Args:
        db: データベースセッション
        user_id: ユーザーID
        project_id: プロジェクトIDでフィルタ（任意）
        level: ログレベルでフィルタ（任意）
        action: アクション種別でフィルタ（任意）
        limit: 取得件数上限
        offset: 取得開始位置

    Returns:
        アクティビティログのリスト
    """
    query = db.query(ActivityLog).filter(ActivityLog.user_id == user_id)

    if project_id is not None:
        query = query.filter(ActivityLog.project_id == project_id)

    if level is not None:
        level_value = level.value if isinstance(level, LogLevel) else level
        query = query.filter(ActivityLog.level == level_value)

    if action is not None:
        query = query.filter(ActivityLog.action == action)

    return query.order_by(ActivityLog.created_at.desc()).offset(offset).limit(limit).all()


# =============================================================================
# ヘルパー関数（プロジェクト実行時に使用）
# =============================================================================

def notify_project_started(
    db: Session,
    user_id: int,
    project_id: int,
    project_title: str,
) -> tuple[Notification, ActivityLog]:
    """
    プロジェクト開始時の通知とログを作成
    """
    notification = create_notification(
        db=db,
        user_id=user_id,
        title="プロジェクト開始",
        message=f"「{project_title}」の実行を開始しました",
        notification_type=NotificationType.INFO,
        link="/log",
    )

    log = write_log(
        db=db,
        user_id=user_id,
        action=LogAction.PROJECT_STARTED,
        message=f"プロジェクト「{project_title}」を開始",
        level=LogLevel.INFO,
        project_id=project_id,
    )

    return notification, log


def notify_project_completed(
    db: Session,
    user_id: int,
    project_id: int,
    project_title: str,
    result_summary: Optional[str] = None,
    task_results: Optional[list] = None,
) -> tuple[Notification, ActivityLog]:
    """
    プロジェクト完了時の通知とログを作成

    task_results: 各タスクの詳細結果リスト
        [{"role": "...", "crew_name": "...", "crew_image": "...", "result": "...", "score": 100}, ...]
    """
    notification = create_notification(
        db=db,
        user_id=user_id,
        title="プロジェクト完了",
        message=f"「{project_title}」が完了しました" + (f"\n{result_summary}" if result_summary else ""),
        notification_type=NotificationType.SUCCESS,
        link="/log",
    )

    # 詳細情報を構築
    details = {}
    if result_summary:
        details["result_summary"] = result_summary
    if task_results:
        details["task_results"] = task_results

    log = write_log(
        db=db,
        user_id=user_id,
        action=LogAction.PROJECT_COMPLETED,
        message=f"プロジェクト「{project_title}」が完了",
        level=LogLevel.INFO,
        project_id=project_id,
        details=details if details else None,
    )

    return notification, log


def notify_project_failed(
    db: Session,
    user_id: int,
    project_id: int,
    project_title: str,
    error_message: str,
) -> tuple[Notification, ActivityLog]:
    """
    プロジェクト失敗時の通知とログを作成
    """
    notification = create_notification(
        db=db,
        user_id=user_id,
        title="プロジェクトエラー",
        message=f"「{project_title}」でエラーが発生しました: {error_message}",
        notification_type=NotificationType.ERROR,
        link="/log",
    )

    log = write_log(
        db=db,
        user_id=user_id,
        action=LogAction.PROJECT_FAILED,
        message=f"プロジェクト「{project_title}」でエラー: {error_message}",
        level=LogLevel.ERROR,
        project_id=project_id,
        details={"error": error_message},
    )

    return notification, log

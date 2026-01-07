"""
通知・ログ管理ルーター

/api/notifications - 通知の取得・既読管理
/api/logs - アクティビティログの取得
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User as UserModel, Notification, ActivityLog
from services import notification_service
from services.notification_service import LogLevel

router = APIRouter(prefix="/api", tags=["notifications"])


# --- Pydantic Schemas ---

class NotificationResponse(BaseModel):
    """通知レスポンス"""
    id: int
    title: str
    message: str
    notification_type: str
    link: Optional[str]
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationsListResponse(BaseModel):
    """通知一覧レスポンス"""
    notifications: list[NotificationResponse]
    unread_count: int
    total: int


class ActivityLogResponse(BaseModel):
    """アクティビティログレスポンス"""
    id: int
    project_id: Optional[int]
    level: str
    action: str
    message: str
    details: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ActivityLogsListResponse(BaseModel):
    """ログ一覧レスポンス"""
    logs: list[ActivityLogResponse]
    total: int


class MarkReadResponse(BaseModel):
    """既読更新レスポンス"""
    success: bool
    message: str


class DeleteLogResponse(BaseModel):
    """ログ削除レスポンス"""
    success: bool
    message: str


class UnreadCountResponse(BaseModel):
    """未読件数レスポンス"""
    unread_count: int


# --- Helper Functions ---

def get_current_user(db: Session = Depends(get_db)) -> UserModel:
    """
    現在のユーザーを取得（シングルユーザーモードなのでID=1固定）
    """
    user = db.query(UserModel).filter(UserModel.id == 1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# --- 通知 API Endpoints ---

@router.get("/notifications", response_model=NotificationsListResponse)
async def get_notifications(
    unread_only: bool = Query(False, description="未読のみ取得"),
    limit: int = Query(50, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> NotificationsListResponse:
    """
    通知一覧を取得
    """
    notifications = notification_service.get_notifications(
        db=db,
        user_id=current_user.id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )

    unread_count = notification_service.get_unread_count(db, current_user.id)

    # 総件数を取得
    total_query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        total_query = total_query.filter(Notification.is_read == False)
    total = total_query.count()

    return NotificationsListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                title=n.title,
                message=n.message,
                notification_type=n.notification_type,
                link=n.link,
                is_read=n.is_read,
                created_at=n.created_at,
            )
            for n in notifications
        ],
        unread_count=unread_count,
        total=total,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> UnreadCountResponse:
    """
    未読通知の件数を取得
    """
    count = notification_service.get_unread_count(db, current_user.id)
    return UnreadCountResponse(unread_count=count)


@router.put("/notifications/{notification_id}/read", response_model=MarkReadResponse)
async def mark_notification_as_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> MarkReadResponse:
    """
    指定した通知を既読にする
    """
    notification = notification_service.mark_as_read(
        db=db,
        notification_id=notification_id,
        user_id=current_user.id,
    )

    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    return MarkReadResponse(success=True, message="通知を既読にしました")


@router.put("/notifications/read-all", response_model=MarkReadResponse)
async def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> MarkReadResponse:
    """
    全ての通知を既読にする
    """
    count = notification_service.mark_all_as_read(
        db=db,
        user_id=current_user.id,
    )

    return MarkReadResponse(
        success=True,
        message=f"{count}件の通知を既読にしました",
    )


# --- ログ API Endpoints ---

@router.get("/logs", response_model=ActivityLogsListResponse)
async def get_activity_logs(
    project_id: Optional[int] = Query(None, description="プロジェクトIDでフィルタ"),
    level: Optional[str] = Query(None, description="ログレベルでフィルタ (INFO/ERROR/WARNING/DEBUG)"),
    action: Optional[str] = Query(None, description="アクション種別でフィルタ"),
    limit: int = Query(100, ge=1, le=500, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> ActivityLogsListResponse:
    """
    アクティビティログ一覧を取得
    """
    # ログレベルの変換
    log_level = None
    if level:
        try:
            log_level = LogLevel(level.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid log level: {level}. Must be one of: INFO, ERROR, WARNING, DEBUG"
            )

    logs = notification_service.get_logs(
        db=db,
        user_id=current_user.id,
        project_id=project_id,
        level=log_level,
        action=action,
        limit=limit,
        offset=offset,
    )

    # 総件数を取得
    total_query = db.query(ActivityLog).filter(ActivityLog.user_id == current_user.id)
    if project_id is not None:
        total_query = total_query.filter(ActivityLog.project_id == project_id)
    if log_level is not None:
        total_query = total_query.filter(ActivityLog.level == log_level.value)
    if action is not None:
        total_query = total_query.filter(ActivityLog.action == action)
    total = total_query.count()

    return ActivityLogsListResponse(
        logs=[
            ActivityLogResponse(
                id=log.id,
                project_id=log.project_id,
                level=log.level,
                action=log.action,
                message=log.message,
                details=log.details,
                created_at=log.created_at,
            )
            for log in logs
        ],
        total=total,
    )


@router.delete("/logs/{log_id}", response_model=DeleteLogResponse)
async def delete_activity_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> DeleteLogResponse:
    """
    指定したアクティビティログを削除する
    """
    log = db.query(ActivityLog).filter(
        ActivityLog.id == log_id,
        ActivityLog.user_id == current_user.id
    ).first()

    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")

    db.delete(log)
    db.commit()

    return DeleteLogResponse(success=True, message="ログを削除しました")

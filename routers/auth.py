"""
認証ルーター
- ログイン（ID/パスワード）
- デモアカウントの場合はログイン時にリセット
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.user_service import authenticate_user, reset_demo_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """ログインリクエスト"""
    username: str
    password: str


class LoginResponse(BaseModel):
    """ログインレスポンス"""
    success: bool
    message: str
    user_id: int | None = None
    username: str | None = None
    user_name: str | None = None
    company_name: str | None = None
    is_demo: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """
    ログイン処理
    - ID/パスワードで認証
    - デモアカウントの場合はデータをリセット
    """
    user = authenticate_user(db, request.username, request.password)

    if not user:
        return LoginResponse(
            success=False,
            message="IDまたはパスワードが間違っています",
        )

    # デモアカウントの場合はリセット
    if user.is_demo:
        reset_demo_user(db, user)

    return LoginResponse(
        success=True,
        message="ログインしました" + ("（デモアカウント: データがリセットされました）" if user.is_demo else ""),
        user_id=user.id,
        username=user.username,
        user_name=user.user_name,
        company_name=user.company_name,
        is_demo=user.is_demo,
    )

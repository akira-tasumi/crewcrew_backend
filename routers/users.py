"""
ユーザープロフィール管理ルーター

/api/users/me - 現在のユーザー情報取得・更新
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User as UserModel

router = APIRouter(prefix="/api/users", tags=["users"])


# --- Pydantic Schemas ---

class UserResponse(BaseModel):
    """ユーザー情報レスポンス"""
    id: int
    company_name: str
    user_name: str | None
    job_title: str | None
    avatar_data: str | None
    coin: int
    ruby: int
    rank: str
    office_level: int

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """ユーザー情報更新リクエスト"""
    company_name: str | None = None
    user_name: str | None = None
    job_title: str | None = None
    avatar_data: str | None = None  # Base64エンコードされた画像データ


# --- Helper Functions ---

def get_current_user(db: Session = Depends(get_db)) -> UserModel:
    """
    現在のユーザーを取得（シングルユーザーモードなのでID=1固定）
    """
    user = db.query(UserModel).filter(UserModel.id == 1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# --- API Endpoints ---

@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    current_user: UserModel = Depends(get_current_user),
) -> UserResponse:
    """
    現在ログインしているユーザーの情報を取得
    """
    return UserResponse(
        id=current_user.id,
        company_name=current_user.company_name,
        user_name=current_user.user_name,
        job_title=current_user.job_title,
        avatar_data=current_user.avatar_data,
        coin=current_user.coin,
        ruby=current_user.ruby,
        rank=current_user.rank,
        office_level=current_user.office_level,
    )


@router.put("/me", response_model=UserResponse)
async def update_my_profile(
    update_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> UserResponse:
    """
    現在のユーザー情報を更新
    """
    # 更新対象のフィールドのみ更新
    if update_data.company_name is not None:
        current_user.company_name = update_data.company_name
    if update_data.user_name is not None:
        current_user.user_name = update_data.user_name
    if update_data.job_title is not None:
        current_user.job_title = update_data.job_title
    if update_data.avatar_data is not None:
        # 空文字の場合はNoneに設定（アバター削除）
        current_user.avatar_data = update_data.avatar_data if update_data.avatar_data else None

    db.commit()
    db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        company_name=current_user.company_name,
        user_name=current_user.user_name,
        job_title=current_user.job_title,
        avatar_data=current_user.avatar_data,
        coin=current_user.coin,
        ruby=current_user.ruby,
        rank=current_user.rank,
        office_level=current_user.office_level,
    )

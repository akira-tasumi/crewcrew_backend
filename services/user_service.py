"""
ユーザーサービス
- 認証
- デモアカウントリセット
"""

import hashlib
from sqlalchemy.orm import Session

from models import User, UserGadget, UnlockedPersonality, DailyLog


def hash_password(password: str) -> str:
    """パスワードをSHA-256でハッシュ化"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """パスワードを検証"""
    return hash_password(password) == hashed


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """
    ユーザー認証
    - usernameとパスワードでユーザーを認証
    - 成功したらUserオブジェクトを返す
    """
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.hashed_password:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def reset_demo_user(db: Session, user: User) -> None:
    """
    デモユーザーのデータをリセット
    - coin: 3000に戻す
    - ruby: 50に戻す
    - 購入したガジェットを削除
    - アンロックした性格を削除
    - プロフィールをリセット
    """
    if not user.is_demo:
        return

    # 通貨リセット
    user.coin = 3000
    user.ruby = 50

    # プロフィールリセット
    user.company_name = "デモ株式会社"
    user.user_name = "デモユーザー"
    user.job_title = "マネージャー"
    user.avatar_data = None
    user.rank = "ブロンズ"
    user.office_level = 1
    user.background_theme = "modern"

    # 購入済みガジェット削除
    db.query(UserGadget).filter(UserGadget.user_id == user.id).delete()

    # アンロック済み性格削除
    db.query(UnlockedPersonality).filter(UnlockedPersonality.user_id == user.id).delete()

    # デイリーログ削除
    db.query(DailyLog).filter(DailyLog.user_id == user.id).delete()

    db.commit()

from datetime import datetime, date, timezone, timedelta

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# 日本時間（JST = UTC+9）
JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    """現在の日本時間を返す"""
    return datetime.now(JST)


class User(Base):
    """ユーザー（プレイヤー）情報"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_name: Mapped[str] = mapped_column(String(100), default="新規開拓株式会社")
    coin: Mapped[int] = mapped_column(Integer, default=1000)
    ruby: Mapped[int] = mapped_column(Integer, default=10)
    rank: Mapped[str] = mapped_column(String(50), default="ブロンズ")
    office_level: Mapped[int] = mapped_column(Integer, default=1)  # オフィスレベル
    background_theme: Mapped[str] = mapped_column(String(50), default="modern")  # 背景テーマ
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )

    # リレーション
    unlocked_personalities: Mapped[list["UnlockedPersonality"]] = relationship(
        "UnlockedPersonality", back_populates="user"
    )


class UnlockedPersonality(Base):
    """アンロック済みの特殊性格を管理"""
    __tablename__ = "unlocked_personalities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    personality_key: Mapped[str] = mapped_column(String(50), nullable=False)  # ナルシスト, 王様, ツンデレ等
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )

    # リレーション
    user: Mapped["User"] = relationship("User", back_populates="unlocked_personalities")


class Crew(Base):
    __tablename__ = "crews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1)
    exp: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    personality: Mapped[str] = mapped_column(Text, nullable=True)
    is_partner: Mapped[bool] = mapped_column(Boolean, default=False)  # 相棒フラグ
    rarity: Mapped[int] = mapped_column(Integer, default=1)  # レアリティ（★1〜★5）

    # リレーション
    task_logs: Mapped[list["TaskLog"]] = relationship("TaskLog", back_populates="crew")
    gadgets: Mapped[list["CrewGadget"]] = relationship("CrewGadget", back_populates="crew")
    skills: Mapped[list["CrewSkill"]] = relationship("CrewSkill", back_populates="crew")


class TaskLog(Base):
    """タスク実行履歴を保存するテーブル"""
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    crew_id: Mapped[int] = mapped_column(Integer, ForeignKey("crews.id"), nullable=False)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)  # 依頼内容
    ai_response: Mapped[str] = mapped_column(Text, nullable=False)  # AIの回答
    exp_gained: Mapped[int] = mapped_column(Integer, default=0)  # 獲得EXP
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )

    # リレーション
    crew: Mapped["Crew"] = relationship("Crew", back_populates="task_logs")


class DailyLog(Base):
    """日報（デイリーレポート）を管理"""
    __tablename__ = "daily_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # YYYY-MM-DD
    task_count: Mapped[int] = mapped_column(Integer, default=0)  # 消化タスク数
    earned_coins: Mapped[int] = mapped_column(Integer, default=0)  # 獲得コイン
    partner_comment: Mapped[str | None] = mapped_column(Text, nullable=True)  # 相棒からの労いメッセージ
    login_stamp: Mapped[bool] = mapped_column(Boolean, default=False)  # ログインスタンプ取得済み
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )


class Gadget(Base):
    """ガジェット（装備アイテム）マスタデータ"""
    __tablename__ = "gadgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str] = mapped_column(String(255), nullable=False)  # emoji or URL
    effect_type: Mapped[str] = mapped_column(String(50), nullable=False)  # speed/creativity/mood
    base_effect_value: Mapped[int] = mapped_column(Integer, default=10)  # 初期効果値
    base_cost: Mapped[int] = mapped_column(Integer, default=500)  # 購入価格

    # リレーション
    crew_gadgets: Mapped[list["CrewGadget"]] = relationship("CrewGadget", back_populates="gadget")


class CrewGadget(Base):
    """クルーのガジェット装備（中間テーブル）"""
    __tablename__ = "crew_gadgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    crew_id: Mapped[int] = mapped_column(Integer, ForeignKey("crews.id"), nullable=False)
    gadget_id: Mapped[int] = mapped_column(Integer, ForeignKey("gadgets.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1)  # ガジェットレベル
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0, 1, 2
    equipped_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )

    # リレーション
    crew: Mapped["Crew"] = relationship("Crew", back_populates="gadgets")
    gadget: Mapped["Gadget"] = relationship("Gadget", back_populates="crew_gadgets")


class Skill(Base):
    """スキルマスタデータ"""
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    skill_type: Mapped[str] = mapped_column(String(50), nullable=False)  # Intelligence/Creative/Communication/Execution
    description: Mapped[str] = mapped_column(Text, nullable=False)
    bonus_effect: Mapped[str] = mapped_column(String(50), nullable=False)  # speed/creativity/mood

    # リレーション
    crew_skills: Mapped[list["CrewSkill"]] = relationship("CrewSkill", back_populates="skill")


class CrewSkill(Base):
    """クルーのスキル（中間テーブル）"""
    __tablename__ = "crew_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    crew_id: Mapped[int] = mapped_column(Integer, ForeignKey("crews.id"), nullable=False)
    skill_id: Mapped[int] = mapped_column(Integer, ForeignKey("skills.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1)  # スキルレベル (1-10)
    slot_type: Mapped[str] = mapped_column(String(20), nullable=False)  # primary/sub/random

    # リレーション
    crew: Mapped["Crew"] = relationship("Crew", back_populates="skills")
    skill: Mapped["Skill"] = relationship("Skill", back_populates="crew_skills")


class Project(Base):
    """プロジェクト（Director Mode）"""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    user_goal: Mapped[str] = mapped_column(Text, nullable=False)  # 元のユーザー入力
    status: Mapped[str] = mapped_column(String(50), default="planning")  # planning/in_progress/completed/cancelled
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # リレーション
    tasks: Mapped[list["ProjectTask"]] = relationship("ProjectTask", back_populates="project")
    inputs: Mapped[list["ProjectInput"]] = relationship("ProjectInput", back_populates="project")


class ProjectTask(Base):
    """プロジェクト内のタスク"""
    __tablename__ = "project_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    crew_id: Mapped[int] = mapped_column(Integer, ForeignKey("crews.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # Analyst, Writer, etc.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)  # 実行順序
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending/in_progress/completed
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # タスク結果
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # リレーション
    project: Mapped["Project"] = relationship("Project", back_populates="tasks")


class ProjectInput(Base):
    """プロジェクトの入力データ"""
    __tablename__ = "project_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)  # client_file, company_url等
    label: Mapped[str] = mapped_column(String(255), nullable=False)  # 表示用ラベル
    input_type: Mapped[str] = mapped_column(String(50), nullable=False)  # file/url/text
    value: Mapped[str | None] = mapped_column(Text, nullable=True)  # 値（URL/テキスト）
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # ファイルパス
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )

    # リレーション
    project: Mapped["Project"] = relationship("Project", back_populates="inputs")

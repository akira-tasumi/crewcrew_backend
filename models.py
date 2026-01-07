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
    # 認証用フィールド
    username: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True, index=True)  # ログインID
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)  # ハッシュ化パスワード
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # デモアカウントフラグ
    # プロフィール
    company_name: Mapped[str] = mapped_column(String(100), default="新規開拓株式会社")
    user_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 担当者名
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 役職
    avatar_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # アバター画像(Base64)
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
    image_base64: Mapped[str | None] = mapped_column(Text, nullable=True)  # Base64画像データ（本番環境用）
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


class UserGadget(Base):
    """ユーザーが所持しているガジェット（購入済み）"""
    __tablename__ = "user_gadgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    gadget_id: Mapped[int] = mapped_column(Integer, ForeignKey("gadgets.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # ガジェットのレベル（強化で上昇）
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )


class PersonalityItem(Base):
    """ショップで販売する特殊性格アイテム"""
    __tablename__ = "personality_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    personality_key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # ナルシスト等
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 表示名
    description: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)  # AI用の口調指示
    ruby_price: Mapped[int] = mapped_column(Integer, nullable=False)  # ルビー価格


class SavedProject(Base):
    """保存されたプロジェクトテンプレート（ディレクターモード用）"""
    __tablename__ = "saved_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)  # 保存された指示内容
    crew_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("crews.id"), nullable=True)  # nullなら自動選択
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    run_count: Mapped[int] = mapped_column(Integer, default=0)  # 実行回数
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, onupdate=now_jst, nullable=False
    )


class Notification(Base):
    """ユーザーへの通知"""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), default="info")  # success, error, info, warning
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 遷移先URL
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )


class ActivityLog(Base):
    """システムアクティビティログ（バックグラウンドタスクの履歴等）"""
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="INFO")  # INFO, ERROR, WARNING, DEBUG
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # project_started, project_completed, etc.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON形式の追加情報
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )


class ExecutionStatus:
    """
    BackgroundExecutionの状態定数

    状態遷移:
    - pending → running → completed/failed
    - pending/running → cancelling → cancelled
    """
    PENDING = "pending"          # 実行待ち
    RUNNING = "running"          # 実行中
    CANCELLING = "cancelling"    # キャンセル処理中（ユーザー要求済み、プロセス停止待ち）
    CANCELLED = "cancelled"      # キャンセル完了
    COMPLETED = "completed"      # 正常終了
    FAILED = "failed"            # エラー終了

    # キャンセル可能な状態
    CANCELLABLE = [PENDING, RUNNING]

    # 終了状態（これ以上変化しない）
    TERMINAL = [COMPLETED, FAILED, CANCELLED]


class BackgroundExecution(Base):
    """バックグラウンド実行の状態管理"""
    __tablename__ = "background_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(50), nullable=False)  # task, project

    # タスク依頼の場合
    crew_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("crews.id"), nullable=True)
    task_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # プロジェクトの場合
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    project_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: tasks, inputs等

    # 共通
    status: Mapped[str] = mapped_column(String(50), default=ExecutionStatus.PENDING)  # ExecutionStatus参照
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # 現在のステップ（タスク番号）
    total_steps: Mapped[int] = mapped_column(Integer, default=1)  # 総ステップ数
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 進捗メッセージ
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最終結果（JSON）
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApprovalRequest(Base):
    """
    Human-in-the-loop: 承認待ちリクエストを管理

    AIが生成した成果物を外部送信・保存する前に、
    人間がレビュー・修正・承認できるようにするためのテーブル。
    """
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    execution_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("background_executions.id"), nullable=True)

    # LangGraph再開用
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    checkpoint_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # シリアライズされたチェックポイント

    # 承認対象の情報
    output_type: Mapped[str] = mapped_column(String(50), nullable=False)  # slides / sheets / slack / email
    pending_output: Mapped[str] = mapped_column(Text, nullable=False)  # 承認待ちの成果物（テキスト or JSON）
    preview_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # プレビュー用データ（スライド構造等）

    # メタデータ
    crew_name: Mapped[str] = mapped_column(String(100), nullable=False)
    crew_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    task_summary: Mapped[str] = mapped_column(String(500), nullable=False)  # タスクの要約

    # ステータス管理
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending / approved / rejected / modified
    human_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)  # 修正指示やコメント
    modified_output: Mapped[str | None] = mapped_column(Text, nullable=True)  # 修正後の成果物

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_jst, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 承認期限（オプション）

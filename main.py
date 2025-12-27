import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine, get_db
from models import Crew as CrewModel, TaskLog, User as UserModel, UnlockedPersonality, DailyLog, Gadget, CrewGadget, Skill, CrewSkill, Project, ProjectTask, ProjectInput, UserGadget
from seed import seed_crews, seed_gadgets, seed_skills, seed_users, ROLES, PERSONALITIES
from services.bedrock_service import execute_task_with_crew, execute_task_with_crew_and_images, generate_greeting, route_task_with_partner, generate_whimsical_talk, generate_labor_words
from graphs import run_director_workflow
from services.image_generation_service import generate_crew_image_with_fallback, evolve_crew_image
from services.youtube import get_transcript_from_url
from services.web_reader import fetch_web_content
from services.pdf_reader import extract_text_from_pdf
from services.google_slides_service import create_presentation
from services.google_sheets_service import create_spreadsheet, parse_table_from_text, extract_sheet_title
from routers import slides as slides_router
from routers import slack as slack_router
from routers import users as users_router
from routers import shop as shop_router
from routers import auth as auth_router
from routers import saved_projects as saved_projects_router
from routers import research as research_router
import re

load_dotenv()


# --- スライド生成ヘルパー関数 ---

def _parse_slides_from_ai_output(ai_output: str) -> list[str]:
    """
    AIの出力からスライドのページ内容を抽出する

    以下のパターンを認識:
    1. "スライド1:", "スライド 1:", "Slide 1:" などの形式
    2. "## スライド1" などのMarkdown見出し形式
    3. "【スライド1】" などの括弧形式
    4. "---" で区切られたセクション

    Args:
        ai_output: AIが生成したテキスト

    Returns:
        各スライドの内容のリスト
    """
    if not ai_output:
        return []

    pages = []

    # パターン1: スライドX: または Slide X: 形式
    slide_pattern = re.compile(
        r'(?:スライド|Slide|ページ|Page)\s*(\d+)\s*[:：]\s*(.*?)(?=(?:スライド|Slide|ページ|Page)\s*\d+\s*[:：]|$)',
        re.DOTALL | re.IGNORECASE
    )
    matches = slide_pattern.findall(ai_output)
    if matches:
        for _, content in matches:
            cleaned = content.strip()
            if cleaned:
                pages.append(cleaned)
        if pages:
            return pages

    # パターン2: Markdown見出し形式 (## スライド1)
    markdown_pattern = re.compile(
        r'##\s*(?:スライド|Slide|ページ|Page)?\s*(\d+)?\s*[：:]?\s*(.*?)(?=##\s*(?:スライド|Slide|ページ|Page)?|$)',
        re.DOTALL | re.IGNORECASE
    )
    matches = markdown_pattern.findall(ai_output)
    if matches and len(matches) > 1:
        for _, content in matches:
            cleaned = content.strip()
            if cleaned:
                pages.append(cleaned)
        if pages:
            return pages

    # パターン3: 【スライド1】形式
    bracket_pattern = re.compile(
        r'[【\[](?:スライド|Slide|ページ|Page)\s*(\d+)[】\]]\s*(.*?)(?=[【\[](?:スライド|Slide|ページ|Page)|$)',
        re.DOTALL | re.IGNORECASE
    )
    matches = bracket_pattern.findall(ai_output)
    if matches:
        for _, content in matches:
            cleaned = content.strip()
            if cleaned:
                pages.append(cleaned)
        if pages:
            return pages

    # パターン4: --- で区切られたセクション
    if '---' in ai_output:
        sections = ai_output.split('---')
        for section in sections:
            cleaned = section.strip()
            if cleaned and len(cleaned) > 10:  # 短すぎるセクションは除外
                pages.append(cleaned)
        if len(pages) > 1:
            return pages

    # パターン5: 番号付きリスト (1. 2. 3.)
    numbered_pattern = re.compile(r'^\s*(\d+)[.）)]\s*(.+?)(?=^\s*\d+[.）)]|\Z)', re.MULTILINE | re.DOTALL)
    matches = numbered_pattern.findall(ai_output)
    if matches and len(matches) >= 3:
        for _, content in matches:
            cleaned = content.strip()
            if cleaned:
                pages.append(cleaned)
        if pages:
            return pages

    # どのパターンにもマッチしない場合: 全体を1枚のスライドとして扱う
    # ただし改行で段落分けして複数スライドにする
    paragraphs = ai_output.split('\n\n')
    meaningful_paragraphs = [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 20]

    if len(meaningful_paragraphs) >= 2:
        return meaningful_paragraphs[:10]  # 最大10スライド
    elif ai_output.strip():
        return [ai_output.strip()]

    return []


def _extract_slide_title(task: str, ai_output: str) -> str:
    """
    タスク内容またはAI出力からスライドのタイトルを抽出する

    Args:
        task: ユーザーのタスク入力
        ai_output: AIが生成した出力

    Returns:
        スライドのタイトル
    """
    # タスクからタイトルを抽出するパターン
    title_patterns = [
        r'「(.+?)」',  # 「タイトル」形式
        r'『(.+?)』',  # 『タイトル』形式
        r'"(.+?)"',    # "タイトル"形式
        r'について.*(?:スライド|プレゼン)',  # 〇〇についてスライド
        r'(.+?)の(?:スライド|プレゼン|資料)',  # 〇〇のスライド
    ]

    for pattern in title_patterns:
        match = re.search(pattern, task)
        if match:
            title = match.group(1) if match.groups() else match.group(0)
            if title and len(title) < 50:
                return title.strip()

    # AI出力の最初の行をタイトルとして使用
    first_line = ai_output.split('\n')[0].strip() if ai_output else ""
    # Markdown記法を除去
    first_line = re.sub(r'^#+\s*', '', first_line)
    first_line = re.sub(r'^\*+\s*', '', first_line)

    if first_line and len(first_line) < 100:
        return first_line[:50]

    # フォールバック: タスクの最初の部分を使用
    task_title = task[:30].strip()
    if task_title:
        return f"{task_title}..."

    return "プレゼンテーション"


# CORS設定: 環境変数から許可リストを取得（デフォルトは全許可）
def get_cors_origins() -> list[str]:
    """
    BACKEND_CORS_ORIGINS 環境変数からCORS許可リストを取得する
    カンマ区切りで複数指定可能（例: "http://localhost:3000,https://example.com"）
    設定がない場合は ["*"] を返す（全許可）
    """
    cors_origins = os.getenv("BACKEND_CORS_ORIGINS")
    if cors_origins:
        return [origin.strip() for origin in cors_origins.split(",")]
    return ["*"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)

    # マイグレーション: 新しいカラムを追加（存在しない場合）
    logger.info("Running migrations...")
    from sqlalchemy import text
    migrations = [
        ("crews", "image_base64", "ALTER TABLE crews ADD COLUMN image_base64 TEXT"),
        ("users", "username", "ALTER TABLE users ADD COLUMN username VARCHAR(50)"),
        ("users", "hashed_password", "ALTER TABLE users ADD COLUMN hashed_password VARCHAR(255)"),
        ("users", "is_demo", "ALTER TABLE users ADD COLUMN is_demo BOOLEAN DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, column, sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.info(f"Added {column} column to {table} table")
            except Exception as e:
                # カラムが既に存在する場合はエラーを無視（duplicate columnまたはalready existsを含む）
                pass

    logger.info("Seeding initial data...")
    db = SessionLocal()
    try:
        seed_users(db)  # 認証用ユーザー（test/demo）
        seed_skills(db)
        seed_crews(db)
        seed_gadgets(db)
    finally:
        db.close()

    logger.info("Kurukuru Backend server started successfully!")
    yield


app = FastAPI(title="Kurukuru Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーターを登録
app.include_router(slides_router.router)
app.include_router(slack_router.router)
app.include_router(users_router.router)
app.include_router(shop_router.router)
app.include_router(auth_router.router)
app.include_router(saved_projects_router.router)
app.include_router(research_router.router)


# --- Response Models ---


class UserResponse(BaseModel):
    id: int
    company_name: str
    user_name: str | None = None
    job_title: str | None = None
    avatar_data: str | None = None
    coin: int
    ruby: int
    rank: str
    office_level: int = 1
    background_theme: str = "modern"

    model_config = {"from_attributes": True}


class WhimsicalTalkRequest(BaseModel):
    time_of_day: str  # morning, afternoon, evening, night


class WhimsicalTalkResponse(BaseModel):
    success: bool
    talk: str | None = None
    partner_name: str | None = None
    partner_image: str | None = None
    error: str | None = None


class CrewResponse(BaseModel):
    id: int
    name: str
    role: str
    level: int
    exp: int
    image: str
    personality: str | None = None
    greeting: str | None = None  # 入社挨拶（作成時のみ）
    is_partner: bool = False
    rarity: int = 1  # レアリティ（★1〜★5）

    model_config = {"from_attributes": True}


class PartnerResponse(BaseModel):
    id: int
    name: str
    role: str
    level: int
    image: str
    personality: str | None = None
    greeting: str  # 相棒の挨拶メッセージ

    model_config = {"from_attributes": True}


class ExecuteTaskRequest(BaseModel):
    crew_id: int
    task: str
    google_access_token: str | None = None  # Google認証トークン（スライド生成時に使用）


class ExecuteTaskResponse(BaseModel):
    success: bool
    result: str | None  # AIが生成したテキストをそのまま返す
    crew_name: str
    crew_id: int
    error: str | None = None
    # EXP/レベル関連
    old_level: int | None = None  # レベルアップ前のレベル
    new_level: int | None = None  # レベルアップ後のレベル
    new_exp: int | None = None    # 新しいEXP値
    exp_gained: int | None = None # 獲得したEXP
    leveled_up: bool = False      # レベルアップしたかどうか
    # コイン報酬
    coin_gained: int | None = None  # 獲得コイン
    new_coin: int | None = None     # 新しいコイン残高
    # ルビー報酬（レベルアップ時）
    ruby_gained: int | None = None  # 獲得ルビー
    new_ruby: int | None = None     # 新しいルビー残高
    # スライド生成結果
    slide_url: str | None = None  # 生成されたスライドのURL
    slide_id: str | None = None   # 生成されたスライドのID
    # スプレッドシート生成結果
    sheet_url: str | None = None  # 生成されたシートのURL
    sheet_id: str | None = None   # 生成されたシートのID


class RouteTaskRequest(BaseModel):
    task: str


class RouteTaskResponse(BaseModel):
    success: bool
    selected_crew_id: int
    selected_crew_name: str
    partner_comment: str
    partner_name: str
    error: str | None = None


class SkillInfo(BaseModel):
    name: str
    level: int
    skill_type: str
    description: str
    bonus_effect: str
    slot_type: str  # primary/sub/random


class StatsInfo(BaseModel):
    speed: int
    creativity: int
    mood: int


class ScoutedCrewResponse(BaseModel):
    id: int
    name: str
    role: str
    role_label: str  # 日本語ラベル
    level: int
    exp: int
    image: str
    personality: str
    personality_label: str  # 日本語ラベル
    rarity: int
    stats: StatsInfo
    skills: list[SkillInfo]

    model_config = {"from_attributes": True}


class ScoutResponse(BaseModel):
    success: bool
    crew: ScoutedCrewResponse | None = None
    greeting: str | None = None
    error: str | None = None
    new_coin: int | None = None
    rarity: int | None = None  # レアリティ（★1〜★5）
    partner_reaction: str | None = None  # 相棒の反応（★4以上で特別コメント）


class PersonalityInfo(BaseModel):
    key: str
    name: str
    description: str
    cost: int  # ルビーコスト（0=無料）
    is_unlocked: bool


class PersonalitiesResponse(BaseModel):
    free_personalities: list[PersonalityInfo]
    premium_personalities: list[PersonalityInfo]


class UnlockPersonalityRequest(BaseModel):
    personality_key: str


class UnlockPersonalityResponse(BaseModel):
    success: bool
    error: str | None = None
    new_ruby: int | None = None


class StampInfo(BaseModel):
    date: str  # YYYY-MM-DD
    has_stamp: bool


class DailyReportResponse(BaseModel):
    success: bool
    date: str  # YYYY-MM-DD
    task_count: int
    earned_coins: int
    login_bonus_given: bool  # 今回ログインボーナスを付与したか
    login_bonus_amount: int  # ログインボーナス額
    stamps: list[StampInfo]  # 過去7日分のスタンプ情報
    consecutive_days: int  # 連続ログイン日数
    labor_words: str  # 相棒の労いの言葉
    partner_name: str | None = None
    partner_image: str | None = None
    new_coin: int | None = None  # 新しいコイン残高
    error: str | None = None


class EvolveCrewResponse(BaseModel):
    success: bool
    crew: CrewResponse | None = None
    old_image: str | None = None  # 進化前の画像パス
    new_image: str | None = None  # 進化後の画像パス
    old_role: str | None = None   # 進化前の役職
    new_role: str | None = None   # 進化後の役職
    error: str | None = None
    new_ruby: int | None = None   # 新しいルビー残高


class CreateCrewRequest(BaseModel):
    name: str
    role: str
    personality_key: str  # 性格のキー（熱血、おだやか等）
    image: str | None = None  # オプション（デフォルト画像を使用）


class UpdateCrewRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    personality: str | None = None
    image: str | None = None


# --- Endpoints ---


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Hello from Kurukuru Backend!"}


@app.get("/api/crews")
async def get_crews(db: Session = Depends(get_db)) -> list[CrewResponse]:
    crews = db.query(CrewModel).order_by(CrewModel.id.desc()).all()
    return [
        CrewResponse(
            id=crew.id,
            name=crew.name,
            role=crew.role,
            level=crew.level,
            exp=crew.exp,
            # Base64がある場合はそれを優先、なければimage_urlを使用
            image=crew.image_base64 if crew.image_base64 else crew.image_url,
            personality=crew.personality,
            is_partner=crew.is_partner,
            rarity=crew.rarity,
        )
        for crew in crews
    ]


@app.get("/api/crews/{crew_id}/skills")
async def get_crew_skills(
    crew_id: int,
    db: Session = Depends(get_db),
) -> list[SkillInfo]:
    """
    指定したクルーのスキル一覧を取得
    """
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    crew_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew_id).all()

    return [
        SkillInfo(
            name=cs.skill.name,
            level=cs.level,
            skill_type=cs.skill.skill_type,
            description=cs.skill.description,
            bonus_effect=cs.skill.bonus_effect,
            slot_type=cs.slot_type,
        )
        for cs in crew_skills
    ]


@app.get("/api/crews/{crew_id}/stats")
async def get_crew_stats(
    crew_id: int,
    db: Session = Depends(get_db),
) -> StatsInfo:
    """
    指定したクルーのステータスを取得（役割とレベルから計算）
    """
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    stats = calculate_base_stats(crew.role, crew.level)
    return StatsInfo(**stats)


@app.post("/api/crews/{crew_id}/assign-skills")
async def assign_skills_to_existing_crew(
    crew_id: int,
    db: Session = Depends(get_db),
) -> list[SkillInfo]:
    """
    スキルを持たない既存クルーにスキルを付与する
    """
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 既にスキルを持っているか確認
    existing_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew_id).count()
    if existing_skills > 0:
        # 既存スキルを返す
        crew_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew_id).all()
        return [
            SkillInfo(
                name=cs.skill.name,
                level=cs.level,
                skill_type=cs.skill.skill_type,
                description=cs.skill.description,
                bonus_effect=cs.skill.bonus_effect,
                slot_type=cs.slot_type,
            )
            for cs in crew_skills
        ]

    # スキルを付与
    assigned_skills = assign_skills_to_crew(db, crew_id, crew.role)
    db.commit()

    logger.info(f"Assigned skills to existing crew: {crew.name} (ID: {crew_id})")
    return assigned_skills


@app.post("/api/crews/assign-skills-all")
async def assign_skills_to_all_crews(
    db: Session = Depends(get_db),
) -> dict:
    """
    スキルを持たない全クルーにスキルを付与する（初期化用）
    """
    crews = db.query(CrewModel).all()
    assigned_count = 0

    for crew in crews:
        existing_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew.id).count()
        if existing_skills == 0:
            assign_skills_to_crew(db, crew.id, crew.role)
            assigned_count += 1
            logger.info(f"Assigned skills to: {crew.name} (ID: {crew.id})")

    db.commit()
    return {
        "success": True,
        "total_crews": len(crews),
        "assigned_count": assigned_count,
        "message": f"{assigned_count} crews received skills",
    }


@app.post("/api/crews")
async def create_crew(
    request: CreateCrewRequest,
    db: Session = Depends(get_db),
) -> CrewResponse:
    """
    新しいクルーを作成する（500コイン消費）

    - name: クルーの名前
    - role: クルーの役割
    - personality_key: 性格のキー（熱血、おだやか等）
    - image: 画像URL（オプション、指定がなければAI生成）

    画像が指定されていない場合、Bedrock Nova Canvas で
    ベース画像からバリエーションを生成し、背景透過して保存する。
    """
    CREATE_COST = 500

    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # コイン残高を確認
    if user.coin < CREATE_COST:
        raise HTTPException(
            status_code=400,
            detail=f"コインが足りません（必要: {CREATE_COST}、現在: {user.coin}）"
        )

    # 性格の検証
    personality_key = request.personality_key
    personality = None

    # 無料性格をチェック
    if personality_key in FREE_PERSONALITIES:
        personality = FREE_PERSONALITIES[personality_key]
    # プレミアム性格をチェック
    elif personality_key in PREMIUM_PERSONALITIES:
        # アンロック済みかチェック
        unlocked = db.query(UnlockedPersonality).filter(
            UnlockedPersonality.user_id == user.id,
            UnlockedPersonality.personality_key == personality_key
        ).first()
        if not unlocked:
            raise HTTPException(
                status_code=400,
                detail=f"性格「{personality_key}」はアンロックされていません"
            )
        personality = PREMIUM_PERSONALITIES[personality_key]["description"]
    else:
        raise HTTPException(status_code=400, detail=f"不明な性格: {personality_key}")

    # コインを消費
    user.coin -= CREATE_COST

    # 画像の決定：指定があればそれを使用、なければAI生成
    image_base64 = None
    if request.image:
        image_url = request.image
        logger.info(f"Using specified image: {image_url}")
    else:
        # Nova Canvas で画像を生成（失敗時はデフォルト画像）
        logger.info(f"Generating image for crew: {request.name} (Role: {request.role}, Personality: {personality_key})")
        image_url, image_base64 = await generate_crew_image_with_fallback(
            crew_name=request.name,
            role=request.role,
            personality=personality_key,
            rarity=1,
        )
        logger.info(f"Generated image: {image_url}, base64: {'Yes' if image_base64 else 'No'}")

    new_crew = CrewModel(
        name=request.name,
        role=request.role,
        personality=personality,
        image_url=image_url,
        image_base64=image_base64,
        level=1,
        exp=0,
        rarity=1,  # 自由作成は★1固定
    )
    db.add(new_crew)
    db.commit()
    db.refresh(new_crew)

    logger.info(f"Created new crew: {new_crew.name} (ID: {new_crew.id})")

    # 入社挨拶を生成
    greeting = await generate_greeting(
        crew_name=request.name,
        crew_role=request.role,
        personality=personality,
    )

    return CrewResponse(
        id=new_crew.id,
        name=new_crew.name,
        role=new_crew.role,
        level=new_crew.level,
        exp=new_crew.exp,
        # Base64がある場合はそれを優先
        image=new_crew.image_base64 if new_crew.image_base64 else new_crew.image_url,
        personality=new_crew.personality,
        greeting=greeting,
        rarity=new_crew.rarity,
    )


@app.put("/api/crews/{crew_id}")
async def update_crew(
    crew_id: int,
    request: UpdateCrewRequest,
    db: Session = Depends(get_db),
) -> CrewResponse:
    """
    クルーを編集する

    - crew_id: 編集するクルーのID
    - name, role, personality, image: 更新するフィールド（指定されたもののみ更新）
    """
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 指定されたフィールドのみ更新
    if request.name is not None:
        crew.name = request.name
    if request.role is not None:
        crew.role = request.role
    if request.personality is not None:
        crew.personality = request.personality
    if request.image is not None:
        crew.image_url = request.image

    db.commit()
    db.refresh(crew)

    logger.info(f"Updated crew: {crew.name} (ID: {crew.id})")

    return CrewResponse(
        id=crew.id,
        name=crew.name,
        role=crew.role,
        level=crew.level,
        exp=crew.exp,
        image=crew.image_url,
        personality=crew.personality,
    )


@app.delete("/api/crews/{crew_id}")
async def delete_crew(
    crew_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """
    クルーを削除する（関連データも一緒に削除）

    - crew_id: 削除するクルーのID
    """
    from models import TaskLog, CrewGadget, CrewSkill, ProjectTask

    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    crew_name = crew.name

    # 関連データを先に削除
    db.query(TaskLog).filter(TaskLog.crew_id == crew_id).delete()
    db.query(CrewGadget).filter(CrewGadget.crew_id == crew_id).delete()
    db.query(CrewSkill).filter(CrewSkill.crew_id == crew_id).delete()
    db.query(ProjectTask).filter(ProjectTask.crew_id == crew_id).delete()

    # クルーを削除
    db.delete(crew)
    db.commit()

    logger.info(f"Deleted crew: {crew_name} (ID: {crew_id}) with all related data")

    return {"success": True, "message": f"Crew '{crew_name}' deleted successfully"}


@app.get("/api/user")
async def get_user(db: Session = Depends(get_db)) -> UserResponse:
    """
    現在のユーザー情報を取得
    """
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        company_name=user.company_name,
        user_name=user.user_name,
        job_title=user.job_title,
        avatar_data=user.avatar_data,
        coin=user.coin,
        ruby=user.ruby,
        rank=user.rank,
        office_level=user.office_level,
    )


@app.post("/api/user/god-mode")
async def activate_god_mode(db: Session = Depends(get_db)):
    """
    デバッグ用: God Modeを発動してコインとルビーを大量付与
    """
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.coin += 10000
    user.ruby += 100
    db.commit()
    db.refresh(user)

    logger.info(f"GOD MODE ACTIVATED! User now has {user.coin} coins and {user.ruby} rubies")

    return {
        "success": True,
        "message": "DEBUG MODE ACTIVATED: You are rich now!",
        "coin": user.coin,
        "ruby": user.ruby,
    }


class AddCoinRequest(BaseModel):
    """コイン加算リクエスト"""
    amount: int


@app.post("/api/user/add-coin")
async def add_coin(request: AddCoinRequest, db: Session = Depends(get_db)):
    """
    ユーザーのコインを加算（クルー独立時の祝い金など）
    """
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.coin += request.amount
    db.commit()
    db.refresh(user)

    logger.info(f"Added {request.amount} coins to user. New balance: {user.coin}")

    return {
        "success": True,
        "coin": user.coin,
    }


@app.get("/api/partner")
async def get_partner(db: Session = Depends(get_db)) -> PartnerResponse | None:
    """
    現在の相棒クルーを取得（挨拶メッセージ付き）
    """
    partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
    if not partner:
        return None

    # 固定のフォールバック挨拶を使用（API呼び出しを避けてレスポンスを高速化）
    fallback_greetings = {
        "フレイミー": "よっしゃ！今日も燃えていこうぜ！🔥",
        "アクアン": "いつもお疲れ様でございます。今日も一緒に頑張りましょう✨",
        "ロッキー": "...準備は万端だ。今日も確実に任務を遂行しよう。",
        "ウィンディ": "やっほー♪ 今日も楽しくやっていこ〜！✨",
        "スパーキー": "おはようっす！今日も新しい発見があるといいっすね！⚡",
        "シャドウ": "...今日も、確実にこなしていくぞ...",
    }
    greeting = fallback_greetings.get(
        partner.name,
        f"今日も一緒に頑張りましょう！ - {partner.name}"
    )

    return PartnerResponse(
        id=partner.id,
        name=partner.name,
        role=partner.role,
        level=partner.level,
        # Base64がある場合はそれを優先
        image=partner.image_base64 if partner.image_base64 else partner.image_url,
        personality=partner.personality,
        greeting=greeting,
    )


@app.get("/api/crews/{crew_id}/logs")
async def get_crew_logs(
    crew_id: int,
    db: Session = Depends(get_db),
):
    """
    指定したクルーのタスク履歴を取得
    """
    # クルーが存在するか確認
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # タスクログを取得（新しい順）
    logs = (
        db.query(TaskLog)
        .filter(TaskLog.crew_id == crew_id)
        .order_by(TaskLog.created_at.desc())
        .limit(50)
        .all()
    )

    return [
        {
            "id": log.id,
            "task": log.task,
            "result": log.result,
            "status": log.status,
            "exp_earned": log.exp_earned,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }
        for log in logs
    ]


@app.post("/api/crews/{crew_id}/set-partner")
async def set_partner(
    crew_id: int,
    db: Session = Depends(get_db),
) -> CrewResponse:
    """
    指定したクルーを相棒に設定する

    - crew_id: 相棒にするクルーのID
    - 他のクルーのis_partnerはすべてFalseにする
    """
    # 指定されたクルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 全クルーのis_partnerをFalseに
    db.query(CrewModel).update({CrewModel.is_partner: False})

    # 指定されたクルーをTrueに
    crew.is_partner = True
    db.commit()
    db.refresh(crew)

    logger.info(f"Set partner: {crew.name} (ID: {crew.id})")

    return CrewResponse(
        id=crew.id,
        name=crew.name,
        role=crew.role,
        level=crew.level,
        exp=crew.exp,
        image=crew.image_url,
        personality=crew.personality,
        is_partner=crew.is_partner,
    )


@app.post("/api/execute-task")
async def execute_task(
    request: ExecuteTaskRequest,
    db: Session = Depends(get_db),
) -> ExecuteTaskResponse:
    """
    クルーにタスクを実行させる

    - crew_id: タスクを実行するクルーのID
    - task: 実行するタスクの内容
    """
    # クルーをDBから取得
    crew = db.query(CrewModel).filter(CrewModel.id == request.crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # デフォルトの性格設定
    personality = crew.personality or "真面目で丁寧な対応を心がける。"

    # スライド作成タスクかどうかを判定
    slide_keywords = ['スライド', 'プレゼン', 'presentation', 'slide', 'ppt', 'パワポ', 'パワーポイント']
    is_slide_task = any(keyword in request.task.lower() for keyword in slide_keywords)

    # シート作成タスクかどうかを判定（プロンプト拡張用）
    sheet_keywords_for_prompt = ['スプレッドシート', 'シート', '表', '一覧', 'リスト', 'spreadsheet', 'sheet', 'excel', 'csv']
    is_sheet_task_for_prompt = any(keyword in request.task.lower() for keyword in sheet_keywords_for_prompt)

    # タスク指示を拡張
    task_for_ai = request.task

    # シート作成タスクの場合（スライドより先に判定）
    if is_sheet_task_for_prompt and not is_slide_task and request.google_access_token:
        task_for_ai = f"""{request.task}

【スプレッドシート作成の指示】
データを整理してスプレッドシートに適した表形式で出力してください。

■ 出力フォーマット（必ずMarkdown表形式で）：

| 列1 | 列2 | 列3 |
|-----|-----|-----|
| データ1 | データ2 | データ3 |
| データ4 | データ5 | データ6 |

■ 表作成のルール：
1. 必ずMarkdown表形式（| で区切る）で出力する
2. 1行目はヘッダー行（項目名）にする
3. データは具体的かつ実用的な内容にする
4. 10〜20行程度のデータを作成する
5. 数値データは単位を明記する（円、%、個など）"""

    # スライド作成タスクの場合
    elif is_slide_task and request.google_access_token:
        task_for_ai = f"""{request.task}

【プレゼンテーション作成の指示】
魅力的で説得力のあるスライドを作成してください。以下の形式で出力してください：

■ 出力フォーマット（必ずこの形式で）：

スライド1: [インパクトのあるタイトル]
📌 キーメッセージ
• ポイント1（具体的な数字やデータがあれば含める）
• ポイント2
• ポイント3

スライド2: [セクションタイトル]
💡 サブタイトルや補足
• 要点を簡潔に（1行20文字以内推奨）
• 具体例や事例があれば追加
• 数値データは「〇〇%」「〇〇倍」など強調

■ スライド作成のルール：
1. 各スライドは「スライドN:」で始める
2. 1スライドあたり3〜5個の箇条書き（多すぎない）
3. 絵文字を見出しに1つ使用（📌💡🎯✅📊🚀💪🔑📈など）
4. 数字やデータは具体的に（「多い」ではなく「80%」など）
5. 最後のスライドはまとめ or アクションを促す内容
6. 5〜8枚程度のスライドを作成

■ スライド構成の参考：
- スライド1: タイトル + サブタイトル
- スライド2: 課題・背景
- スライド3-5: 主要ポイント（各1テーマ）
- スライド6-7: 具体例・データ
- スライド8: まとめ・次のアクション"""

    # Bedrock APIでタスクを実行
    logger.info(f"Executing task with Bedrock: crew={crew.name}, personality={personality[:20]}...")
    result = await execute_task_with_crew(
        crew_name=crew.name,
        crew_role=crew.role,
        personality=personality,
        task=task_for_ai,
    )

    # EXP/レベル情報
    exp_gained = 0
    old_level = crew.level
    new_exp = crew.exp
    new_level = crew.level
    leveled_up = False

    # コイン報酬
    coin_gained = 0
    new_coin = 0

    # ルビー報酬（レベルアップ時）
    ruby_gained = 0
    new_ruby = 0

    # 成功時は経験値を加算 & TaskLogを保存 & コイン付与
    if result["success"]:
        exp_gained = 15  # +15 EXP（固定）
        crew.exp += exp_gained

        # レベルアップ判定（100 EXP で 1 レベルアップ）
        # 安全策: 1回のタスクで最大1レベルまで（余剰EXPは次回に持ち越し）
        if crew.exp >= 100:
            crew.exp -= 100
            crew.level += 1

        new_exp = crew.exp
        new_level = crew.level
        leveled_up = crew.level > old_level

        # コイン報酬（50コイン）
        coin_gained = 50
        user = db.query(UserModel).first()
        if user:
            user.coin += coin_gained
            new_coin = user.coin
            logger.info(f"Added {coin_gained} coins to user. New balance: {new_coin}")

            # レベルアップ時はルビーを5個付与 + スキルレベルアップ
            if leveled_up:
                ruby_gained = 5
                user.ruby += ruby_gained
                new_ruby = user.ruby
                logger.info(f"Level up bonus! Added {ruby_gained} rubies. New balance: {new_ruby}")

                # スキルレベルアップ（ランダムで1つ選んでレベルアップ）
                import random
                crew_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew.id).all()
                if crew_skills:
                    # レベル10未満のスキルからランダムで1つ選ぶ
                    upgradable_skills = [s for s in crew_skills if s.level < 10]
                    if upgradable_skills:
                        skill_to_upgrade = random.choice(upgradable_skills)
                        skill_to_upgrade.level += 1
                        logger.info(f"Skill level up! {skill_to_upgrade.skill.name} -> Lv.{skill_to_upgrade.level}")

        # TaskLogを保存
        task_log = TaskLog(
            crew_id=crew.id,
            user_input=request.task,
            ai_response=result["result"] or "",
            exp_gained=exp_gained,
        )
        db.add(task_log)

        db.commit()
        logger.info(
            f"Added {exp_gained} EXP to {crew.name}. "
            f"Level: {old_level} -> {new_level}, EXP: {new_exp}, LeveledUp: {leveled_up}"
        )

    # スライド生成の実行（is_slide_taskは上で既に判定済み）
    slide_url = None
    slide_id = None

    if result["success"] and is_slide_task and request.google_access_token:
        logger.info(f"Detected slide creation task. Attempting to create Google Slides...")
        try:
            # AIの出力からスライドのページを抽出
            ai_output = result["result"] or ""
            pages = _parse_slides_from_ai_output(ai_output)

            if pages:
                # タイトルを抽出（タスクから生成）
                title = _extract_slide_title(request.task, ai_output)

                # Google Slidesを作成
                slide_result = create_presentation(
                    access_token=request.google_access_token,
                    title=title,
                    pages=pages
                )
                slide_url = slide_result["presentationUrl"]
                slide_id = slide_result["presentationId"]
                logger.info(f"Google Slides created successfully: {slide_url}")

                # 結果にスライドURLを追加
                result["result"] = f"{ai_output}\n\n📊 **Googleスライドを作成しました！**\n{slide_url}"
            else:
                logger.warning("Could not parse slides from AI output")
        except Exception as e:
            logger.error(f"Failed to create Google Slides: {e}")
            # スライド作成に失敗しても、テキスト結果は返す

    # スプレッドシート生成の実行
    sheet_url = None
    sheet_id = None
    sheet_keywords = ['スプレッドシート', 'シート', '表', '一覧', 'リスト', 'spreadsheet', 'sheet', 'excel', 'csv']
    is_sheet_task = any(keyword in request.task.lower() for keyword in sheet_keywords)

    # スライドタスクではない場合のみシート生成を試みる
    if result["success"] and is_sheet_task and not is_slide_task and request.google_access_token:
        logger.info(f"Detected sheet creation task. Attempting to create Google Sheets...")
        try:
            ai_output = result["result"] or ""
            table_data = parse_table_from_text(ai_output)

            if table_data:
                title = extract_sheet_title(request.task, ai_output)
                sheet_result = create_spreadsheet(
                    access_token=request.google_access_token,
                    title=title,
                    data=table_data
                )
                sheet_url = sheet_result["spreadsheetUrl"]
                sheet_id = sheet_result["spreadsheetId"]
                logger.info(f"Google Sheets created successfully: {sheet_url}")

                # 結果にシートURLを追加
                result["result"] = f"{ai_output}\n\n📋 **Googleスプレッドシートを作成しました！**\n{sheet_url}"
            else:
                logger.warning("Could not parse table data from AI output")
        except Exception as e:
            logger.error(f"Failed to create Google Sheets: {e}")

    return ExecuteTaskResponse(
        success=result["success"],
        result=result["result"],
        crew_name=crew.name,
        crew_id=crew.id,
        error=result["error"],
        old_level=old_level,
        new_level=new_level,
        new_exp=new_exp,
        exp_gained=exp_gained,
        leveled_up=leveled_up,
        coin_gained=coin_gained if result["success"] else None,
        new_coin=new_coin if result["success"] else None,
        ruby_gained=ruby_gained if leveled_up else None,
        new_ruby=new_ruby if leveled_up else None,
        slide_url=slide_url,
        slide_id=slide_id,
        sheet_url=sheet_url,
        sheet_id=sheet_id,
    )


@app.post("/api/execute-task-with-files")
async def execute_task_with_files(
    crew_id: int = Form(...),
    task: str = Form(...),
    google_access_token: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> ExecuteTaskResponse:
    """
    クルーにタスクを実行させる（ファイル添付対応版）

    - crew_id: タスクを実行するクルーのID
    - task: 実行するタスクの内容
    - files: 添付ファイル（画像、Excel、CSV）
    """
    from services.file_utils import process_file, get_file_type

    # クルーをDBから取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # デフォルトの性格設定
    personality = crew.personality or "真面目で丁寧な対応を心がける。"

    # 添付ファイルを処理
    file_contexts = []
    image_data_list = []  # 画像データ用（Vision API用）

    for uploaded_file in files:
        try:
            file_content = await uploaded_file.read()
            file_type = get_file_type(uploaded_file.filename or "unknown")

            if file_type in ('excel', 'csv', 'text', 'json', 'xml', 'word', 'powerpoint'):
                # テキストベースのファイルはテキスト変換
                processed = process_file(file_content, uploaded_file.filename or "file")
                file_contexts.append(f"\n\n【添付ファイル: {uploaded_file.filename}】\n{processed['text']}")
                logger.info(f"Processed {file_type} file: {uploaded_file.filename}")

            elif file_type == 'image':
                # 画像はBase64エンコード
                processed = process_file(file_content, uploaded_file.filename or "image.png")
                image_data_list.append({
                    'filename': uploaded_file.filename,
                    'base64': processed['base64'],
                    'media_type': processed['media_type'],
                })
                file_contexts.append(f"\n\n【添付画像: {uploaded_file.filename}】(画像を分析してください)")
                logger.info(f"Processed image file: {uploaded_file.filename}")

            elif file_type == 'pdf':
                # PDFは既存の処理
                from pypdf import PdfReader
                import io as io_module
                reader = PdfReader(io_module.BytesIO(file_content))
                pdf_text = ""
                for page in reader.pages[:10]:  # 最大10ページ
                    pdf_text += page.extract_text() or ""
                if pdf_text:
                    file_contexts.append(f"\n\n【添付PDF: {uploaded_file.filename}】\n{pdf_text[:5000]}")
                logger.info(f"Processed PDF file: {uploaded_file.filename}")

            else:
                file_contexts.append(f"\n\n【添付ファイル: {uploaded_file.filename}】サポートされていないファイル形式です")
                logger.warning(f"Unsupported file type: {uploaded_file.filename}")

        except Exception as e:
            logger.error(f"File processing error: {e}")
            file_contexts.append(f"\n\n【添付ファイル: {uploaded_file.filename}】読み込みエラー: {str(e)}")

    # タスク内容にファイルコンテキストを追加
    task_with_files = task
    if file_contexts:
        task_with_files = task + "\n".join(file_contexts)

    # スライド作成タスクかどうかを判定
    slide_keywords = ['スライド', 'プレゼン', 'presentation', 'slide', 'ppt', 'パワポ', 'パワーポイント']
    is_slide_task = any(keyword in task.lower() for keyword in slide_keywords)

    # シート作成タスクかどうかを判定
    sheet_keywords_for_prompt = ['スプレッドシート', 'シート', '表', '一覧', 'リスト', 'spreadsheet', 'sheet', 'excel', 'csv']
    is_sheet_task_for_prompt = any(keyword in task.lower() for keyword in sheet_keywords_for_prompt)

    # タスク指示を拡張
    task_for_ai = task_with_files

    # シート作成タスクの場合
    if is_sheet_task_for_prompt and not is_slide_task and google_access_token:
        task_for_ai = f"""{task_with_files}

【スプレッドシート作成の指示】
データを整理してスプレッドシートに適した表形式で出力してください。

■ 出力フォーマット（必ずMarkdown表形式で）：

| 列1 | 列2 | 列3 |
|-----|-----|-----|
| データ1 | データ2 | データ3 |
| データ4 | データ5 | データ6 |

■ 表作成のルール：
1. 必ずMarkdown表形式（| で区切る）で出力する
2. 1行目はヘッダー行（項目名）にする
3. データは具体的かつ実用的な内容にする
4. 10〜20行程度のデータを作成する
5. 数値データは単位を明記する（円、%、個など）"""

    # スライド作成タスクの場合
    elif is_slide_task and google_access_token:
        task_for_ai = f"""{task_with_files}

【スライド作成の指示】
以下のフォーマットでスライド内容を生成してください。

■ 出力フォーマット：
---PAGE---
# スライドのタイトル

- ポイント1
- ポイント2
- ポイント3
---PAGE---
# 次のスライドのタイトル
...

■ ルール：
1. 各スライドは `---PAGE---` で区切る
2. タイトルは `#` で始める（1行目）
3. 内容は箇条書き（`-` で始める）
4. 5〜10枚程度のスライドを作成
5. 具体的で分かりやすい内容にする"""

    # AIにタスク実行を依頼（画像がある場合は画像付きで）
    if image_data_list:
        # 画像付きのBedrock呼び出し
        result = await execute_task_with_crew_and_images(
            crew_name=crew.name,
            personality=personality,
            task=task_for_ai,
            images=image_data_list,
        )
    else:
        result = await execute_task_with_crew(crew.name, crew.role or "", personality, task_for_ai)

    # 以下は既存のexecute_taskと同じ処理
    old_level = crew.level
    old_exp = crew.exp
    exp_gained = 15 if result["success"] else 0
    new_exp = old_exp + exp_gained
    new_level = old_level
    leveled_up = False

    while new_exp >= 100:
        new_exp -= 100
        new_level += 1
        leveled_up = True

    # コインとルビー付与
    coin_gained = 10 if result["success"] else 0
    ruby_gained = 5 if leveled_up else 0
    new_coin = None
    new_ruby = None

    if result["success"]:
        user = db.query(UserModel).first()
        if user:
            user.coin += coin_gained
            if leveled_up:
                user.ruby += ruby_gained
            new_coin = user.coin
            new_ruby = user.ruby

        crew.exp = new_exp
        crew.level = new_level

        # TaskLogに保存
        task_log = TaskLog(
            crew_id=crew.id,
            user_input=task[:2000] if task else "",
            ai_response=result["result"][:2000] if result["result"] else "",
            exp_gained=exp_gained,
        )
        db.add(task_log)
        db.commit()

    return ExecuteTaskResponse(
        success=result["success"],
        result=result["result"],
        crew_name=crew.name,
        crew_id=crew.id,
        error=result["error"],
        old_level=old_level,
        new_level=new_level,
        new_exp=new_exp,
        exp_gained=exp_gained,
        leveled_up=leveled_up,
        coin_gained=coin_gained if result["success"] else None,
        new_coin=new_coin if result["success"] else None,
        ruby_gained=ruby_gained if leveled_up else None,
        new_ruby=new_ruby if leveled_up else None,
    )


@app.post("/api/route-task")
async def route_task(
    request: RouteTaskRequest,
    db: Session = Depends(get_db),
) -> RouteTaskResponse:
    """
    相棒（マネージャー）がタスクに最適なクルーを選定する
    """
    # 相棒を取得
    partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
    if not partner:
        raise HTTPException(status_code=400, detail="相棒が設定されていません")

    # 全クルーを取得
    crews = db.query(CrewModel).all()
    crew_list = [{"id": c.id, "name": c.name, "role": c.role} for c in crews]

    # 相棒にルーティングを依頼
    personality = partner.personality or "真面目で丁寧な対応を心がける。"
    result = await route_task_with_partner(
        partner_name=partner.name,
        partner_personality=personality,
        crews=crew_list,
        task=request.task,
    )

    return RouteTaskResponse(
        success=result["success"],
        selected_crew_id=result["selected_crew_id"],
        selected_crew_name=result["selected_crew_name"],
        partner_comment=result["partner_comment"],
        partner_name=partner.name,
        error=result.get("error"),
    )


def roll_rarity() -> int:
    """
    レアリティを抽選する

    確率:
    - ★1: 40%
    - ★2: 30%
    - ★3: 20%
    - ★4: 8%
    - ★5: 2%
    """
    import random
    roll = random.random() * 100
    if roll < 2:
        return 5
    elif roll < 10:
        return 4
    elif roll < 30:
        return 3
    elif roll < 60:
        return 2
    else:
        return 1


# 性格定義
FREE_PERSONALITIES = {
    "熱血": "熱血で情熱的。語尾に「〜だぜ！」を使う。",
    "おだやか": "穏やかで優しい。丁寧な敬語を使う。",
    "明るい": "明るくフレンドリー。「〜だよ！」「〜じゃん！」を使う。",
    "クール": "クールで寡黙。「...」を多用する。",
    "頭脳派": "真面目で責任感が強い。断定的な表現を使う。",
}

PREMIUM_PERSONALITIES = {
    "ナルシスト": {"description": "自分大好きナルシスト。「〜な俺様」「美しい」を多用。", "cost": 50},
    "王様": {"description": "王様気質で尊大。「〜であるぞ」「余は〜」を使う。", "cost": 50},
    "ツンデレ": {"description": "ツンデレ口調。「べ、別に〜じゃないんだから！」を多用。", "cost": 50},
    "お嬢様": {"description": "お嬢様言葉。「〜ですわ」「おほほ」を使う。", "cost": 50},
    "科学者": {"description": "マッドサイエンティスト風。「〜なのだ！」「仮説では〜」を使う。", "cost": 50},
    "忍者": {"description": "忍者口調。「〜でござる」「拙者は〜」を使う。", "cost": 50},
}


def calculate_base_stats(role: str, level: int = 1) -> dict:
    """
    役割とレベルに基づいてステータスを計算
    """
    base_value = 30 + level * 5  # 基本値

    role_info = ROLES.get(role)
    if not role_info:
        # フォールバック: バランス型
        return {"speed": base_value, "creativity": base_value, "mood": base_value}

    weights = role_info["stats_weight"]
    return {
        "speed": int(base_value * weights["speed"]),
        "creativity": int(base_value * weights["creativity"]),
        "mood": int(base_value * weights["mood"]),
    }


def assign_skills_to_crew(db: Session, crew_id: int, role: str) -> list[SkillInfo]:
    """
    クルーにスキルを3つ付与する

    1. 必須スキル: 役割に相応しいスキルから1つ
    2. サブスキル: ランダムで1つ
    3. ランダム: 完全ランダムで1つ（意外な組み合わせ用）
    """
    import random

    # 全スキルを取得
    all_skills = db.query(Skill).all()
    if not all_skills:
        return []

    skill_by_name = {s.name: s for s in all_skills}
    assigned_skill_ids: set[int] = set()
    result: list[SkillInfo] = []

    # 役割情報を取得
    role_info = ROLES.get(role, ROLES["Engineer"])  # フォールバック: Engineer
    primary_skill_names = role_info.get("primary_skills", [])

    # 1. 必須スキル（役割に相応しいスキル）
    available_primary = [skill_by_name[n] for n in primary_skill_names if n in skill_by_name]
    if available_primary:
        primary_skill = random.choice(available_primary)
        assigned_skill_ids.add(primary_skill.id)
        crew_skill = CrewSkill(
            crew_id=crew_id,
            skill_id=primary_skill.id,
            level=1,
            slot_type="primary",
        )
        db.add(crew_skill)
        result.append(SkillInfo(
            name=primary_skill.name,
            level=1,
            skill_type=primary_skill.skill_type,
            description=primary_skill.description,
            bonus_effect=primary_skill.bonus_effect,
            slot_type="primary",
        ))

    # 2. サブスキル（ランダム、必須スキルと重複しない）
    remaining_skills = [s for s in all_skills if s.id not in assigned_skill_ids]
    if remaining_skills:
        sub_skill = random.choice(remaining_skills)
        assigned_skill_ids.add(sub_skill.id)
        crew_skill = CrewSkill(
            crew_id=crew_id,
            skill_id=sub_skill.id,
            level=1,
            slot_type="sub",
        )
        db.add(crew_skill)
        result.append(SkillInfo(
            name=sub_skill.name,
            level=1,
            skill_type=sub_skill.skill_type,
            description=sub_skill.description,
            bonus_effect=sub_skill.bonus_effect,
            slot_type="sub",
        ))

    # 3. ランダムスキル（完全ランダム、既に付与されたものと重複しない）
    remaining_skills = [s for s in all_skills if s.id not in assigned_skill_ids]
    if remaining_skills:
        random_skill = random.choice(remaining_skills)
        assigned_skill_ids.add(random_skill.id)
        crew_skill = CrewSkill(
            crew_id=crew_id,
            skill_id=random_skill.id,
            level=1,
            slot_type="random",
        )
        db.add(crew_skill)
        result.append(SkillInfo(
            name=random_skill.name,
            level=1,
            skill_type=random_skill.skill_type,
            description=random_skill.description,
            bonus_effect=random_skill.bonus_effect,
            slot_type="random",
        ))

    return result


@app.post("/api/scout")
async def scout_crew(
    db: Session = Depends(get_db),
) -> ScoutResponse:
    """
    コインを消費して新しいクルーをスカウト（ガチャ）

    - 300コインを消費
    - レアリティを抽選（★1〜★5）
    - 役割・性格・スキルをランダム付与
    - レアリティに応じた豪華キーワードで画像生成
    - ★4以上で相棒が特別コメント
    """
    import random
    SCOUT_COST = 300

    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # コイン残高を確認
    if user.coin < SCOUT_COST:
        return ScoutResponse(
            success=False,
            error=f"コインが足りません（必要: {SCOUT_COST}、現在: {user.coin}）",
            new_coin=user.coin,
        )

    # コインを消費
    user.coin -= SCOUT_COST
    new_coin = user.coin

    # レアリティを抽選
    rarity = roll_rarity()
    logger.info(f"Rolled rarity: ★{rarity}")

    # ランダムな名前を生成
    first_names = ["ブレイズ", "ミスト", "サンダー", "フロスト", "ストーム", "シャイン", "ダーク", "ライト", "ゴールド", "シルバー"]
    last_names = ["ィ", "ン", "ー", "ス", "ト", "ク", "ル", "ア", "オ", "エ"]

    # レアリティが高いほど豪華な名前のプレフィックスを追加
    rarity_prefixes = {
        1: "",
        2: "",
        3: "★",
        4: "【金】",
        5: "【伝説】",
    }

    name = rarity_prefixes[rarity] + random.choice(first_names) + random.choice(last_names)

    # 役割をランダム決定（新しいROLESから）
    role = random.choice(list(ROLES.keys()))
    role_label = ROLES[role]["label"]

    # 性格をランダム決定（新しいPERSONALITIESから）
    personality_key = random.choice(list(PERSONALITIES.keys()))
    personality_info = PERSONALITIES[personality_key]
    personality_label = personality_info["label"]
    personality_tone = personality_info["tone"]

    # AI画像生成（役割・性格・レアリティを渡す）
    logger.info(f"Scouting new crew: {name} (Role: {role}, Personality: {personality_key}, ★{rarity})")
    image_url, image_base64 = await generate_crew_image_with_fallback(
        crew_name=name,
        role=role,
        personality=personality_key,
        rarity=rarity,
    )

    # クルーをDBに保存
    new_crew = CrewModel(
        name=name,
        role=role,
        personality=personality_key,  # キーを保存
        image_url=image_url,
        image_base64=image_base64,
        level=1,
        exp=0,
        rarity=rarity,
    )
    db.add(new_crew)
    db.commit()
    db.refresh(new_crew)

    # スキルを付与
    assigned_skills = assign_skills_to_crew(db, new_crew.id, role)
    db.commit()

    logger.info(f"Scouted new crew: {new_crew.name} (ID: {new_crew.id}, Role: {role}, ★{rarity})")
    logger.info(f"Assigned skills: {[s.name for s in assigned_skills]}")

    # ステータスを計算
    stats = calculate_base_stats(role, level=1)

    # 入社挨拶を生成（性格のトーンを使用）
    greeting = await generate_greeting(
        crew_name=name,
        crew_role=role_label,
        personality=personality_tone,
    )

    # ★4以上の場合、相棒の反応を追加
    partner_reaction = None
    if rarity >= 4:
        partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
        if partner:
            if rarity == 5:
                partner_reaction = f"{partner.name}「とんでもない逸材をスカウトしたぜ！これは伝説級だ！！」"
            else:
                partner_reaction = f"{partner.name}「おおっ！かなりの実力者をスカウトできたな！」"

    return ScoutResponse(
        success=True,
        crew=ScoutedCrewResponse(
            id=new_crew.id,
            name=new_crew.name,
            role=new_crew.role,
            role_label=role_label,
            level=new_crew.level,
            exp=new_crew.exp,
            # Base64がある場合はそれを優先
            image=new_crew.image_base64 if new_crew.image_base64 else new_crew.image_url,
            personality=personality_key,
            personality_label=personality_label,
            rarity=new_crew.rarity,
            stats=StatsInfo(**stats),
            skills=assigned_skills,
        ),
        greeting=greeting,
        new_coin=new_coin,
        rarity=rarity,
        partner_reaction=partner_reaction,
    )


@app.get("/api/personalities")
async def get_personalities(db: Session = Depends(get_db)) -> PersonalitiesResponse:
    """
    利用可能な性格一覧を取得
    無料性格とプレミアム性格（アンロック状態を含む）
    """
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # アンロック済み性格を取得
    unlocked_keys = set()
    unlocked = db.query(UnlockedPersonality).filter(
        UnlockedPersonality.user_id == user.id
    ).all()
    for u in unlocked:
        unlocked_keys.add(u.personality_key)

    # 無料性格リスト
    free_list = [
        PersonalityInfo(
            key=key,
            name=key,
            description=desc,
            cost=0,
            is_unlocked=True,
        )
        for key, desc in FREE_PERSONALITIES.items()
    ]

    # プレミアム性格リスト
    premium_list = [
        PersonalityInfo(
            key=key,
            name=key,
            description=info["description"],
            cost=info["cost"],
            is_unlocked=(key in unlocked_keys),
        )
        for key, info in PREMIUM_PERSONALITIES.items()
    ]

    return PersonalitiesResponse(
        free_personalities=free_list,
        premium_personalities=premium_list,
    )


@app.post("/api/personalities/unlock")
async def unlock_personality(
    request: UnlockPersonalityRequest,
    db: Session = Depends(get_db),
) -> UnlockPersonalityResponse:
    """
    プレミアム性格をアンロックする（ルビー消費）
    """
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 性格が存在するか確認
    if request.personality_key not in PREMIUM_PERSONALITIES:
        return UnlockPersonalityResponse(
            success=False,
            error=f"不明な性格: {request.personality_key}",
        )

    # すでにアンロック済みかチェック
    existing = db.query(UnlockedPersonality).filter(
        UnlockedPersonality.user_id == user.id,
        UnlockedPersonality.personality_key == request.personality_key
    ).first()
    if existing:
        return UnlockPersonalityResponse(
            success=False,
            error=f"性格「{request.personality_key}」はすでにアンロック済みです",
            new_ruby=user.ruby,
        )

    # コストを取得
    cost = PREMIUM_PERSONALITIES[request.personality_key]["cost"]

    # ルビー残高を確認
    if user.ruby < cost:
        return UnlockPersonalityResponse(
            success=False,
            error=f"ルビーが足りません（必要: {cost}、現在: {user.ruby}）",
            new_ruby=user.ruby,
        )

    # ルビーを消費
    user.ruby -= cost

    # アンロック記録を保存
    unlock = UnlockedPersonality(
        user_id=user.id,
        personality_key=request.personality_key,
    )
    db.add(unlock)
    db.commit()

    logger.info(f"Unlocked personality: {request.personality_key} for user {user.id}")

    return UnlockPersonalityResponse(
        success=True,
        new_ruby=user.ruby,
    )


@app.post("/api/partner/greeting")
async def get_partner_whimsical_talk(
    request: WhimsicalTalkRequest,
    db: Session = Depends(get_db),
) -> WhimsicalTalkResponse:
    """
    相棒の「気まぐれトーク」を取得

    時間帯・資産状況・性格を考慮してセリフを生成
    """
    # 相棒を取得
    partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
    if not partner:
        return WhimsicalTalkResponse(
            success=False,
            error="相棒が設定されていません",
        )

    # ユーザー情報を取得
    user = db.query(UserModel).first()
    if not user:
        return WhimsicalTalkResponse(
            success=False,
            error="ユーザーが見つかりません",
        )

    # 時間帯の検証
    valid_times = ["morning", "afternoon", "evening", "night"]
    time_of_day = request.time_of_day if request.time_of_day in valid_times else "afternoon"

    # 気まぐれトークを生成
    personality = partner.personality or "フレンドリーで明るい"
    talk = await generate_whimsical_talk(
        crew_name=partner.name,
        crew_role=partner.role,
        personality=personality,
        time_of_day=time_of_day,
        coin=user.coin,
        ruby=user.ruby,
    )

    return WhimsicalTalkResponse(
        success=True,
        talk=talk,
        partner_name=partner.name,
        partner_image=partner.image_url,
    )


@app.get("/api/daily-report")
async def get_daily_report(
    db: Session = Depends(get_db),
) -> DailyReportResponse:
    """
    日報（デイリーレポート）を取得

    - 本日のタスク数・獲得コインを集計
    - 過去7日分のスタンプ情報を返す
    - 初回アクセス時はログインボーナス（100コイン）を付与
    - 相棒の労いの言葉を生成
    """
    LOGIN_BONUS = 100

    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 相棒を取得
    partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()

    # 今日の日付
    today = date.today()
    today_str = today.isoformat()

    # 今日のDailyLogを取得（なければ作成）
    daily_log = db.query(DailyLog).filter(
        DailyLog.user_id == user.id,
        DailyLog.date == today,
    ).first()

    # 今日のタスク数を集計（TaskLogから）
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    task_count = db.query(TaskLog).filter(
        TaskLog.created_at >= today_start,
        TaskLog.created_at <= today_end,
    ).count()

    # 今日の獲得コイン（タスク1件につき50コイン）
    earned_coins = task_count * 50

    # ログインボーナスの処理
    login_bonus_given = False
    if daily_log is None:
        # 新規作成（初回アクセス）
        daily_log = DailyLog(
            user_id=user.id,
            date=today,
            task_count=task_count,
            earned_coins=earned_coins,
            login_stamp=True,
        )
        db.add(daily_log)

        # ログインボーナスを付与
        user.coin += LOGIN_BONUS
        login_bonus_given = True
        logger.info(f"Login bonus given: +{LOGIN_BONUS} coins")
    else:
        # 既存レコードを更新
        daily_log.task_count = task_count
        daily_log.earned_coins = earned_coins

    db.commit()
    db.refresh(daily_log)

    # 過去7日分のスタンプ情報を取得
    stamps: list[StampInfo] = []
    for i in range(6, -1, -1):  # 7日前から今日まで
        target_date = today - timedelta(days=i)
        log = db.query(DailyLog).filter(
            DailyLog.user_id == user.id,
            DailyLog.date == target_date,
        ).first()
        stamps.append(StampInfo(
            date=target_date.isoformat(),
            has_stamp=log is not None and log.login_stamp,
        ))

    # 連続ログイン日数を計算
    consecutive_days = 0
    for i in range(7):  # 最大7日まで
        target_date = today - timedelta(days=i)
        log = db.query(DailyLog).filter(
            DailyLog.user_id == user.id,
            DailyLog.date == target_date,
            DailyLog.login_stamp == True,
        ).first()
        if log:
            consecutive_days += 1
        else:
            break

    # 相棒の労いの言葉を生成
    labor_words = "お疲れ様でした！"
    partner_name = None
    partner_image = None

    if partner:
        partner_name = partner.name
        partner_image = partner.image_url
        personality = partner.personality or "フレンドリーで明るい"
        labor_words = await generate_labor_words(
            crew_name=partner.name,
            personality=personality,
            task_count=task_count,
            earned_coins=earned_coins,
            consecutive_days=consecutive_days,
        )

    # 労いの言葉をDailyLogに保存
    daily_log.partner_comment = labor_words
    db.commit()

    return DailyReportResponse(
        success=True,
        date=today_str,
        task_count=task_count,
        earned_coins=earned_coins,
        login_bonus_given=login_bonus_given,
        login_bonus_amount=LOGIN_BONUS if login_bonus_given else 0,
        stamps=stamps,
        consecutive_days=consecutive_days,
        labor_words=labor_words,
        partner_name=partner_name,
        partner_image=partner_image,
        new_coin=user.coin,
    )


@app.post("/api/crews/{crew_id}/evolve")
async def evolve_crew(
    crew_id: int,
    db: Session = Depends(get_db),
) -> EvolveCrewResponse:
    """
    クルーを昇進（進化）させる

    条件:
    - クルーのレベルが10以上
    - ユーザーが10ルビー以上所持（消費する）

    処理:
    - 現在の画像をベースにNova CanvasのImage-to-Imageで進化後の画像を生成
    - 役職に "Senior " プレフィックスを追加
    - 進化フラグをセット（レアリティを1上げる）
    """
    EVOLVE_COST = 10  # 必要ルビー
    REQUIRED_LEVEL = 10  # 必要レベル

    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # クルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # レベル条件をチェック
    if crew.level < REQUIRED_LEVEL:
        return EvolveCrewResponse(
            success=False,
            error=f"レベルが足りません（必要: Lv.{REQUIRED_LEVEL}、現在: Lv.{crew.level}）",
            new_ruby=user.ruby,
        )

    # ルビー残高をチェック
    if user.ruby < EVOLVE_COST:
        return EvolveCrewResponse(
            success=False,
            error=f"ルビーが足りません（必要: {EVOLVE_COST}💎、現在: {user.ruby}💎）",
            new_ruby=user.ruby,
        )

    # 既に進化済み（役職が "Senior " で始まる）かチェック
    if crew.role.startswith("Senior "):
        return EvolveCrewResponse(
            success=False,
            error=f"{crew.name}は既に昇進済みです",
            new_ruby=user.ruby,
        )

    # ルビーを消費
    user.ruby -= EVOLVE_COST
    logger.info(f"Evolving crew: {crew.name} (ID: {crew.id}), consuming {EVOLVE_COST} rubies")

    # 進化前の状態を保存
    old_image = crew.image_url
    old_role = crew.role

    try:
        # 進化画像を生成
        new_image = await evolve_crew_image(crew.image_url, crew.name)
        logger.info(f"Generated evolved image: {new_image}")

        # 役職をランクアップ
        new_role = f"Senior {crew.role}"

        # レアリティを1上げる（最大5）
        new_rarity = min(crew.rarity + 1, 5)

        # DBを更新
        crew.image_url = new_image
        crew.role = new_role
        crew.rarity = new_rarity

        db.commit()
        db.refresh(crew)

        logger.info(f"Crew evolved: {crew.name} -> {new_role} (rarity: {new_rarity})")

        return EvolveCrewResponse(
            success=True,
            crew=CrewResponse(
                id=crew.id,
                name=crew.name,
                role=crew.role,
                level=crew.level,
                exp=crew.exp,
                image=crew.image_url,
                personality=crew.personality,
                is_partner=crew.is_partner,
                rarity=crew.rarity,
            ),
            old_image=old_image,
            new_image=new_image,
            old_role=old_role,
            new_role=new_role,
            new_ruby=user.ruby,
        )

    except Exception as e:
        # 画像生成に失敗した場合はロールバック
        db.rollback()
        user.ruby += EVOLVE_COST  # ルビーを返却
        db.commit()

        logger.error(f"Evolution failed: {e}")
        return EvolveCrewResponse(
            success=False,
            error=f"昇進に失敗しました: {str(e)}",
            new_ruby=user.ruby,
        )


# ==============================
# 連携デモ（CrewAI風）API
# ==============================

class CollaborationRequest(BaseModel):
    youtube_url: str


class CollaborationStep(BaseModel):
    agent_id: int
    agent_name: str
    agent_image: str
    role: str  # "analyst" or "writer"
    status: str  # "thinking", "done", "writing"
    output: str | None = None


class CollaborationResponse(BaseModel):
    success: bool
    steps: list[CollaborationStep]
    final_article: str | None = None
    error: str | None = None


@app.post("/api/demo/collaboration")
async def demo_collaboration(
    request: CollaborationRequest,
    db: Session = Depends(get_db),
) -> CollaborationResponse:
    """
    複数クルーが連携してYouTube動画をブログ記事にするデモ

    Agent A (Analyst): 動画の内容を要約
    Agent B (Writer): 要約をブログ記事に変換

    字幕取得に成功した場合は実際の内容を使用、
    失敗した場合はダミーのAIトピックで生成を続行
    """
    from services.bedrock_service import get_bedrock_client, MODEL_ID
    import json

    logger.info(f"Collaboration demo started with URL: {request.youtube_url}")

    # ========== Step 0: YouTube字幕を取得 ==========
    transcript, status_message = get_transcript_from_url(request.youtube_url)

    if transcript:
        print(f"[Collaboration] ✅ 字幕取得成功: {len(transcript)} chars")
        logger.info(f"Transcript fetched successfully: {len(transcript)} chars")
        use_real_transcript = True
    else:
        print(f"[Collaboration] ⚠️ 字幕取得失敗: {status_message} - ダミーモードで続行")
        logger.warning(f"Transcript fetch failed: {status_message} - using dummy mode")
        use_real_transcript = False

    # 担当クルーを取得（ロッキー=分析担当、アクアン=ライター担当）
    analyst = db.query(CrewModel).filter(CrewModel.name == "ロッキー").first()
    writer = db.query(CrewModel).filter(CrewModel.name == "アクアン").first()

    if not analyst or not writer:
        # フォールバック: 最初の2人を使用
        all_crews = db.query(CrewModel).limit(2).all()
        if len(all_crews) < 2:
            return CollaborationResponse(
                success=False,
                steps=[],
                error="クルーが不足しています",
            )
        analyst, writer = all_crews[0], all_crews[1]

    steps: list[CollaborationStep] = []

    try:
        client = get_bedrock_client()

        # ========== 1回のAPI呼び出しで両方の処理を実行 ==========
        logger.info(f"Running collaboration demo: {analyst.name} -> {writer.name}")

        # 字幕取得成功時と失敗時でプロンプトを分岐
        if use_real_transcript:
            # 実際の字幕を使用
            combined_prompt = f"""あなたは2人のエキスパートになりきって、順番にタスクを実行してください。

【タスク概要】
以下のYouTube動画の字幕テキストを元に、ブログ記事を作成します。

【動画URL】
{request.youtube_url}

【字幕テキスト】
{transcript}

=== Step 1: 分析担当（{analyst.name}）===
上記の字幕テキストを読み、動画の重要なポイントを3-5つの箇条書きで要約してください。

=== Step 2: ライター担当（{writer.name}）===
Step 1の要約を元に、400-600字程度の魅力的なブログ記事を書いてください。
- キャッチーなタイトル
- 読者を引きつける導入
- 各ポイントの展開
- 行動を促す締め

【出力フォーマット】
## 分析結果（{analyst.name}）
- ポイント1: ...
- ポイント2: ...
（以下省略）

## ブログ記事（{writer.name}）
# タイトル

（本文）
"""
        else:
            # ダミーモード（字幕取得失敗時）
            combined_prompt = f"""あなたは2人のエキスパートになりきって、順番にタスクを実行してください。

【タスク概要】
YouTube動画URL「{request.youtube_url}」の内容をブログ記事にします。

※注意: 動画の字幕を取得できませんでしたが、デモを継続します。
「AIと仕事の未来」についての動画だと仮定して、一般的な内容で記事を作成してください。

=== Step 1: 分析担当（{analyst.name}）===
「AIと仕事の未来」というテーマで、動画に含まれていそうな重要なポイントを3-5つの箇条書きで要約してください。

=== Step 2: ライター担当（{writer.name}）===
Step 1の要約を元に、400-600字程度の魅力的なブログ記事を書いてください。
- キャッチーなタイトル
- 読者を引きつける導入
- 各ポイントの展開
- 行動を促す締め

【出力フォーマット】
## 分析結果（{analyst.name}）
- ポイント1: ...
- ポイント2: ...
（以下省略）

## ブログ記事（{writer.name}）
# タイトル

（本文）
"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2000,
            "temperature": 0.7,
            "messages": [{"role": "user", "content": combined_prompt}],
        }

        response = client.invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        result = json.loads(response["body"].read())
        full_output = result.get("content", [{}])[0].get("text", "").strip()

        logger.info(f"Combined output length: {len(full_output)}")

        # 出力を分割
        analyst_output = ""
        final_article = ""

        if f"## 分析結果（{analyst.name}）" in full_output and f"## ブログ記事（{writer.name}）" in full_output:
            parts = full_output.split(f"## ブログ記事（{writer.name}）")
            analyst_output = parts[0].replace(f"## 分析結果（{analyst.name}）", "").strip()
            final_article = parts[1].strip() if len(parts) > 1 else ""
        else:
            # フォールバック: 全体を記事として扱う
            analyst_output = "動画の分析が完了しました。"
            final_article = full_output

        # Step情報を構築
        steps.append(CollaborationStep(
            agent_id=analyst.id,
            agent_name=analyst.name,
            agent_image=analyst.image_url,
            role="analyst",
            status="done",
            output=analyst_output,
        ))

        steps.append(CollaborationStep(
            agent_id=writer.id,
            agent_name=writer.name,
            agent_image=writer.image_url,
            role="writer",
            status="done",
            output=final_article,
        ))

        print(f"[Collaboration] ✅ デモ完了 (字幕モード: {'リアル' if use_real_transcript else 'ダミー'})")
        logger.info(f"Collaboration demo completed successfully! (transcript mode: {'real' if use_real_transcript else 'dummy'})")

        return CollaborationResponse(
            success=True,
            steps=steps,
            final_article=final_article,
        )

    except Exception as e:
        print(f"[Collaboration] ❌ エラー発生: {e}")
        logger.error(f"Collaboration demo failed: {e}")
        return CollaborationResponse(
            success=False,
            steps=steps,
            error=str(e),
        )


# ==============================
# ガジェットシステム API
# ==============================

class GadgetResponse(BaseModel):
    id: int
    name: str
    description: str
    icon: str
    effect_type: str
    base_effect_value: int
    base_cost: int

    model_config = {"from_attributes": True}


class CrewGadgetResponse(BaseModel):
    id: int
    gadget_id: int
    gadget_name: str
    gadget_icon: str
    gadget_description: str
    effect_type: str
    level: int
    slot_index: int
    current_effect: int  # 現在の効果値（レベル補正後）

    model_config = {"from_attributes": True}


class EquipGadgetRequest(BaseModel):
    gadget_id: int
    slot_index: int  # 0, 1, 2


class EquipGadgetResponse(BaseModel):
    success: bool
    error: str | None = None
    equipped_gadget: CrewGadgetResponse | None = None
    new_coin: int | None = None


class UpgradeGadgetResponse(BaseModel):
    success: bool
    error: str | None = None
    upgraded_gadget: CrewGadgetResponse | None = None
    new_coin: int | None = None
    old_level: int | None = None
    new_level: int | None = None
    old_effect: int | None = None
    new_effect: int | None = None


def calculate_gadget_effect(base_value: int, level: int) -> int:
    """ガジェットの効果値を計算（レベル補正）"""
    # 効果 = base_effect_value * (1 + 0.2 * (level - 1))
    return int(base_value * (1 + 0.2 * (level - 1)))


def calculate_upgrade_cost(base_cost: int, current_level: int) -> int:
    """ガジェットの強化コストを計算"""
    # コスト = base_cost * 0.5 * current_level
    return int(base_cost * 0.5 * current_level)


@app.get("/api/gadgets")
async def get_gadgets(db: Session = Depends(get_db)) -> list[GadgetResponse]:
    """
    購入可能なガジェット一覧を取得
    """
    gadgets = db.query(Gadget).all()
    return [
        GadgetResponse(
            id=g.id,
            name=g.name,
            description=g.description,
            icon=g.icon,
            effect_type=g.effect_type,
            base_effect_value=g.base_effect_value,
            base_cost=g.base_cost,
        )
        for g in gadgets
    ]


@app.get("/api/crews/{crew_id}/gadgets")
async def get_crew_gadgets(
    crew_id: int,
    db: Session = Depends(get_db),
) -> list[CrewGadgetResponse]:
    """
    指定したクルーの装備中ガジェット一覧を取得
    """
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    crew_gadgets = db.query(CrewGadget).filter(CrewGadget.crew_id == crew_id).all()

    return [
        CrewGadgetResponse(
            id=cg.id,
            gadget_id=cg.gadget.id,
            gadget_name=cg.gadget.name,
            gadget_icon=cg.gadget.icon,
            gadget_description=cg.gadget.description,
            effect_type=cg.gadget.effect_type,
            level=cg.level,
            slot_index=cg.slot_index,
            current_effect=calculate_gadget_effect(cg.gadget.base_effect_value, cg.level),
        )
        for cg in crew_gadgets
    ]


@app.post("/api/crews/{crew_id}/gadgets/equip")
async def equip_gadget(
    crew_id: int,
    request: EquipGadgetRequest,
    db: Session = Depends(get_db),
) -> EquipGadgetResponse:
    """
    購入済みガジェットを装備する（購入はショップで行う）

    - gadget_id: 装備するガジェットのID
    - slot_index: 装備するスロット（0, 1, 2）
    - 他のクルーが装備中の場合は交換（スワップ）
    """
    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # クルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # ガジェットを取得
    gadget = db.query(Gadget).filter(Gadget.id == request.gadget_id).first()
    if not gadget:
        raise HTTPException(status_code=404, detail="Gadget not found")

    # スロット番号の検証
    if request.slot_index not in [0, 1, 2]:
        return EquipGadgetResponse(
            success=False,
            error="無効なスロット番号です（0, 1, 2のいずれか）",
        )

    # スロット解放条件のチェック
    slot_unlock_levels = {0: 1, 1: 10, 2: 20}
    required_level = slot_unlock_levels[request.slot_index]
    if crew.level < required_level:
        return EquipGadgetResponse(
            success=False,
            error=f"スロット{request.slot_index + 1}はLv.{required_level}で解放されます（現在: Lv.{crew.level}）",
        )

    # ユーザーがガジェットを所持しているかチェック
    user_gadget = db.query(UserGadget).filter(
        UserGadget.user_id == user.id,
        UserGadget.gadget_id == request.gadget_id,
    ).first()

    if not user_gadget:
        return EquipGadgetResponse(
            success=False,
            error="このガジェットを所持していません。ショップで購入してください。",
        )

    # 他のクルーがこのガジェットを装備中かチェック
    other_crew_equipped = db.query(CrewGadget).filter(
        CrewGadget.gadget_id == request.gadget_id,
        CrewGadget.crew_id != crew_id,
    ).first()

    if other_crew_equipped:
        # 他のクルーから装備解除
        db.delete(other_crew_equipped)
        logger.info(f"Unequipped gadget from crew_id={other_crew_equipped.crew_id} for swap")

    # 既存の装備を確認（同じスロットに装備がある場合は上書き）
    existing = db.query(CrewGadget).filter(
        CrewGadget.crew_id == crew_id,
        CrewGadget.slot_index == request.slot_index,
    ).first()

    if existing:
        # 既存装備を削除
        db.delete(existing)

    # UserGadgetからレベルを取得（強化済みのレベルを引き継ぐ）
    gadget_level = user_gadget.level if user_gadget else 1

    # 新しい装備を作成
    new_crew_gadget = CrewGadget(
        crew_id=crew_id,
        gadget_id=gadget.id,
        level=gadget_level,  # UserGadgetのレベルを引き継ぐ
        slot_index=request.slot_index,
    )
    db.add(new_crew_gadget)
    db.commit()
    db.refresh(new_crew_gadget)

    logger.info(f"Equipped gadget: {gadget.name} (Lv.{gadget_level}) to {crew.name} slot {request.slot_index}")

    return EquipGadgetResponse(
        success=True,
        equipped_gadget=CrewGadgetResponse(
            id=new_crew_gadget.id,
            gadget_id=gadget.id,
            gadget_name=gadget.name,
            gadget_icon=gadget.icon,
            gadget_description=gadget.description,
            effect_type=gadget.effect_type,
            level=new_crew_gadget.level,
            slot_index=new_crew_gadget.slot_index,
            current_effect=calculate_gadget_effect(gadget.base_effect_value, new_crew_gadget.level),
        ),
        new_coin=user.coin,
    )


@app.post("/api/crews/{crew_id}/gadgets/{gadget_id}/upgrade")
async def upgrade_gadget(
    crew_id: int,
    gadget_id: int,
    db: Session = Depends(get_db),
) -> UpgradeGadgetResponse:
    """
    装備中のガジェットをレベルアップする

    - コストはレベルに応じて上昇
    - 効果は base_effect_value * (1 + 0.2 * (level - 1))
    """
    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # クルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 装備中のガジェットを取得
    crew_gadget = db.query(CrewGadget).filter(
        CrewGadget.crew_id == crew_id,
        CrewGadget.gadget_id == gadget_id,
    ).first()

    if not crew_gadget:
        return UpgradeGadgetResponse(
            success=False,
            error="このガジェットは装備されていません",
        )

    gadget = crew_gadget.gadget
    current_level = crew_gadget.level
    old_effect = calculate_gadget_effect(gadget.base_effect_value, current_level)

    # 最大レベルチェック（最大10）
    if current_level >= 10:
        return UpgradeGadgetResponse(
            success=False,
            error="ガジェットは最大レベルに達しています",
        )

    # 強化コストを計算
    upgrade_cost = calculate_upgrade_cost(gadget.base_cost, current_level)

    # コイン残高をチェック
    if user.coin < upgrade_cost:
        return UpgradeGadgetResponse(
            success=False,
            error=f"コインが足りません（必要: {upgrade_cost}、現在: {user.coin}）",
            new_coin=user.coin,
        )

    # コインを消費
    user.coin -= upgrade_cost

    # レベルアップ（CrewGadget）
    crew_gadget.level += 1
    new_level = crew_gadget.level
    new_effect = calculate_gadget_effect(gadget.base_effect_value, new_level)

    # UserGadgetのレベルも同期（他のクルーに装備する際に引き継ぐため）
    user_gadget = db.query(UserGadget).filter(
        UserGadget.user_id == user.id,
        UserGadget.gadget_id == gadget_id,
    ).first()
    if user_gadget:
        user_gadget.level = new_level

    db.commit()
    db.refresh(crew_gadget)

    logger.info(f"Upgraded gadget: {gadget.name} Lv.{current_level} -> Lv.{new_level}")

    return UpgradeGadgetResponse(
        success=True,
        upgraded_gadget=CrewGadgetResponse(
            id=crew_gadget.id,
            gadget_id=gadget.id,
            gadget_name=gadget.name,
            gadget_icon=gadget.icon,
            gadget_description=gadget.description,
            effect_type=gadget.effect_type,
            level=new_level,
            slot_index=crew_gadget.slot_index,
            current_effect=new_effect,
        ),
        new_coin=user.coin,
        old_level=current_level,
        new_level=new_level,
        old_effect=old_effect,
        new_effect=new_effect,
    )


class UnequipGadgetResponse(BaseModel):
    success: bool
    error: str | None = None


@app.post("/api/crews/{crew_id}/gadgets/{gadget_id}/unequip")
async def unequip_gadget(
    crew_id: int,
    gadget_id: int,
    db: Session = Depends(get_db),
) -> UnequipGadgetResponse:
    """
    装備中のガジェットを外す
    """
    # クルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 装備中のガジェットを取得
    crew_gadget = db.query(CrewGadget).filter(
        CrewGadget.crew_id == crew_id,
        CrewGadget.gadget_id == gadget_id,
    ).first()

    if not crew_gadget:
        return UnequipGadgetResponse(
            success=False,
            error="このガジェットは装備されていません",
        )

    # ガジェット情報をログ用に取得
    gadget = db.query(Gadget).filter(Gadget.id == gadget_id).first()
    gadget_name = gadget.name if gadget else f"gadget_{gadget_id}"

    # 装備を削除
    db.delete(crew_gadget)
    db.commit()

    logger.info(f"Unequipped gadget: {gadget_name} from {crew.name}")

    return UnequipGadgetResponse(success=True)


# ============================================================
# スキル強化API
# ============================================================

class SkillUpgradeResult(BaseModel):
    skill_name: str
    old_level: int
    new_level: int
    increase: int


class UpgradeSkillsResponse(BaseModel):
    success: bool
    error: str | None = None
    new_coin: int | None = None
    cost: int | None = None
    upgraded_skills: list[SkillUpgradeResult] = []


@app.post("/api/crews/{crew_id}/upgrade-skills")
async def upgrade_crew_skills(
    crew_id: int,
    db: Session = Depends(get_db),
) -> UpgradeSkillsResponse:
    """
    クルーのスキルをランダムに強化する（100コイン消費）

    - 各スキルが1〜5ランダムに上昇
    - 最大レベル10を超えない
    """
    import random

    UPGRADE_COST = 100

    # ユーザーを取得
    user = db.query(UserModel).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # クルーを取得
    crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # コイン残高をチェック
    if user.coin < UPGRADE_COST:
        return UpgradeSkillsResponse(
            success=False,
            error=f"コインが足りません（必要: {UPGRADE_COST}、現在: {user.coin}）",
            new_coin=user.coin,
        )

    # クルーのスキルを取得
    crew_skills = db.query(CrewSkill).filter(CrewSkill.crew_id == crew_id).all()

    if not crew_skills:
        return UpgradeSkillsResponse(
            success=False,
            error="スキルがありません",
            new_coin=user.coin,
        )

    # コインを消費
    user.coin -= UPGRADE_COST

    # 各スキルをランダムに1〜5上昇
    upgraded_skills = []
    for crew_skill in crew_skills:
        old_level = crew_skill.level
        increase = random.randint(1, 5)
        new_level = min(old_level + increase, 10)  # 最大10
        actual_increase = new_level - old_level

        if actual_increase > 0:
            crew_skill.level = new_level
            upgraded_skills.append(SkillUpgradeResult(
                skill_name=crew_skill.skill.name,
                old_level=old_level,
                new_level=new_level,
                increase=actual_increase,
            ))

    db.commit()

    logger.info(f"Upgraded skills for crew {crew.name}: {[s.skill_name for s in upgraded_skills]}")

    return UpgradeSkillsResponse(
        success=True,
        new_coin=user.coin,
        cost=UPGRADE_COST,
        upgraded_skills=upgraded_skills,
    )


# ============================================================
# Web記事要約API
# ============================================================

class WebSummaryRequest(BaseModel):
    url: str


class WebSummaryResponse(BaseModel):
    success: bool
    summary: str | None = None
    page_title: str | None = None
    crew_id: int | None = None
    crew_name: str | None = None
    crew_image: str | None = None
    error: str | None = None
    # EXP/レベル関連
    exp_gained: int | None = None
    old_level: int | None = None
    new_level: int | None = None
    new_exp: int | None = None
    leveled_up: bool = False
    # コイン報酬
    coin_gained: int | None = None


@app.post("/api/tools/web-summary")
async def summarize_web_article(
    request: WebSummaryRequest,
    db: Session = Depends(get_db),
) -> WebSummaryResponse:
    """
    URLからWebページの内容を取得し、AIが要約する

    - 「情報収集」スキルを持つクルー、またはランダムなクルーを担当に選出
    - ビジネスパーソン向けに重要ポイント3点で要約
    """
    import random
    import boto3
    import json

    try:
        # 1. Webページからテキストを抽出
        logger.info(f"Fetching web content from: {request.url}")
        try:
            content = fetch_web_content(request.url)
        except ValueError as e:
            return WebSummaryResponse(
                success=False,
                error=str(e),
            )

        # 2. 担当クルーを選定（「情報収集」スキル持ちを優先）
        # 「情報収集」スキルを持つクルーを探す
        research_skill = db.query(Skill).filter(Skill.name == "情報収集").first()
        assigned_crew = None

        if research_skill:
            # このスキルを持つクルーを取得
            crew_with_skill = (
                db.query(CrewModel)
                .join(CrewSkill)
                .filter(CrewSkill.skill_id == research_skill.id)
                .first()
            )
            if crew_with_skill:
                assigned_crew = crew_with_skill

        # スキル持ちがいなければランダム選択
        if not assigned_crew:
            all_crews = db.query(CrewModel).all()
            if all_crews:
                assigned_crew = random.choice(all_crews)

        if not assigned_crew:
            return WebSummaryResponse(
                success=False,
                error="担当できるクルーがいません。先にクルーを作成してください。",
            )

        # 3. Bedrockで要約を生成
        prompt = f"""以下のWeb記事の内容を読み、ビジネスパーソン向けに重要なポイントを3点の箇条書きで要約してください。
出力は日本語で行ってください。各ポイントは具体的かつ簡潔に記述してください。

【記事本文】
{content}

【出力形式】
• ポイント1: ...
• ポイント2: ...
• ポイント3: ..."""

        from botocore.config import Config
        bedrock_config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 2})
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=bedrock_config)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.5,
        })

        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=body
        )

        response_body = json.loads(response["body"].read())
        summary = response_body["content"][0]["text"]

        # ページタイトルを抽出（コンテンツの最初の行から）
        page_title = None
        if content.startswith("【タイトル】"):
            first_line = content.split("\n")[0]
            page_title = first_line.replace("【タイトル】", "").strip()

        # EXP付与とTaskLog保存
        exp_gained = 15
        old_level = assigned_crew.level
        assigned_crew.exp += exp_gained

        # レベルアップ判定
        leveled_up = False
        if assigned_crew.exp >= 100:
            assigned_crew.exp -= 100
            assigned_crew.level += 1
            leveled_up = True

        new_level = assigned_crew.level
        new_exp = assigned_crew.exp

        # TaskLogを保存
        task_log = TaskLog(
            crew_id=assigned_crew.id,
            user_input=f"[URL要約] {request.url}",
            ai_response=summary[:1000] if summary else "",
            exp_gained=exp_gained,
        )
        db.add(task_log)

        # コイン報酬
        coin_gained = 50
        user = db.query(UserModel).first()
        if user:
            user.coin += coin_gained
            if leveled_up:
                user.ruby += 5

        db.commit()
        logger.info(f"Web summary generated by {assigned_crew.name}. +{exp_gained} EXP")

        return WebSummaryResponse(
            success=True,
            summary=summary,
            page_title=page_title,
            crew_id=assigned_crew.id,
            crew_name=assigned_crew.name,
            # Base64がある場合はそれを優先、なければimage_urlを使用
            crew_image=assigned_crew.image_base64 if assigned_crew.image_base64 else assigned_crew.image_url,
            exp_gained=exp_gained,
            old_level=old_level,
            new_level=new_level,
            new_exp=new_exp,
            leveled_up=leveled_up,
            coin_gained=coin_gained,
        )

    except ClientError as e:
        logger.error(f"Bedrock API error: {e}")
        return WebSummaryResponse(
            success=False,
            error="AI要約の生成に失敗しました。しばらく待ってから再度お試しください。",
        )
    except Exception as e:
        logger.error(f"Web summary error: {e}")
        return WebSummaryResponse(
            success=False,
            error=f"要約処理中にエラーが発生しました: {str(e)}",
        )


# ============================================================
# PDFファイル要約API
# ============================================================

class FileSummaryResponse(BaseModel):
    success: bool
    summary: str | None = None
    filename: str | None = None
    page_count: int | None = None
    crew_id: int | None = None
    crew_name: str | None = None
    crew_image: str | None = None
    error: str | None = None
    # EXP/レベル関連
    exp_gained: int | None = None
    old_level: int | None = None
    new_level: int | None = None
    new_exp: int | None = None
    leveled_up: bool = False
    # コイン報酬
    coin_gained: int | None = None


# 最大ファイルサイズ（10MB）
MAX_FILE_SIZE = 10 * 1024 * 1024


@app.post("/api/tools/file-summary")
async def summarize_pdf_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> FileSummaryResponse:
    """
    PDFファイルからテキストを抽出し、AIが要約する

    - 「データ分析」または「情報収集」スキルを持つクルー、またはランダムなクルーを担当に選出
    - ビジネスパーソン向けに重要ポイントを箇条書きで要約
    """
    import random
    import boto3
    import json

    try:
        # 1. ファイル形式チェック
        if not file.filename:
            return FileSummaryResponse(
                success=False,
                error="ファイル名が取得できませんでした。",
            )

        if not file.filename.lower().endswith('.pdf'):
            return FileSummaryResponse(
                success=False,
                error="PDFファイルのみ対応しています。",
            )

        # 2. ファイルサイズチェック
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return FileSummaryResponse(
                success=False,
                error=f"ファイルサイズが大きすぎます（最大10MB）。現在のサイズ: {len(file_content) / (1024 * 1024):.1f}MB",
            )

        # 3. PDFからテキスト抽出
        logger.info(f"Extracting text from PDF: {file.filename}")
        from io import BytesIO
        file_stream = BytesIO(file_content)

        try:
            content = extract_text_from_pdf(file_stream)
        except ValueError as e:
            return FileSummaryResponse(
                success=False,
                error=str(e),
            )

        if not content or not content.strip():
            return FileSummaryResponse(
                success=False,
                error="PDFからテキストを抽出できませんでした。画像のみのPDFの可能性があります。",
            )

        # ページ数を取得
        file_stream.seek(0)
        from pypdf import PdfReader
        try:
            reader = PdfReader(file_stream)
            page_count = len(reader.pages)
        except Exception:
            page_count = None

        # 4. 担当クルーを選定（「データ分析」または「情報収集」スキル持ちを優先）
        assigned_crew = None
        for skill_name in ["データ分析", "情報収集"]:
            skill = db.query(Skill).filter(Skill.name == skill_name).first()
            if skill:
                crew_with_skill = (
                    db.query(CrewModel)
                    .join(CrewSkill)
                    .filter(CrewSkill.skill_id == skill.id)
                    .first()
                )
                if crew_with_skill:
                    assigned_crew = crew_with_skill
                    break

        # スキル持ちがいなければランダム選択
        if not assigned_crew:
            all_crews = db.query(CrewModel).all()
            if all_crews:
                assigned_crew = random.choice(all_crews)

        if not assigned_crew:
            return FileSummaryResponse(
                success=False,
                error="担当できるクルーがいません。先にクルーを作成してください。",
            )

        # 5. Bedrockで要約を生成
        prompt = f"""以下の資料（PDF）の内容を読み、ビジネスパーソン向けに重要なポイントを箇条書きで分かりやすく要約してください。
出力は日本語で行ってください。各ポイントは具体的かつ簡潔に記述してください。

【資料テキスト】
{content}

【出力形式】
• ポイント1: ...
• ポイント2: ...
• ポイント3: ...
（必要に応じて追加）"""

        from botocore.config import Config
        bedrock_config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 2})
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=bedrock_config)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.5,
        })

        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=body
        )

        response_body = json.loads(response["body"].read())
        summary = response_body["content"][0]["text"]

        # EXP付与とTaskLog保存
        exp_gained = 15
        old_level = assigned_crew.level
        assigned_crew.exp += exp_gained

        # レベルアップ判定
        leveled_up = False
        if assigned_crew.exp >= 100:
            assigned_crew.exp -= 100
            assigned_crew.level += 1
            leveled_up = True

        new_level = assigned_crew.level
        new_exp = assigned_crew.exp

        # TaskLogを保存
        task_log = TaskLog(
            crew_id=assigned_crew.id,
            user_input=f"[PDF要約] {file.filename}",
            ai_response=summary[:1000] if summary else "",
            exp_gained=exp_gained,
        )
        db.add(task_log)

        # コイン報酬
        coin_gained = 50
        user = db.query(UserModel).first()
        if user:
            user.coin += coin_gained
            if leveled_up:
                user.ruby += 5

        db.commit()
        logger.info(f"PDF summary generated by {assigned_crew.name} ({file.filename}). +{exp_gained} EXP")

        return FileSummaryResponse(
            success=True,
            summary=summary,
            filename=file.filename,
            page_count=page_count,
            crew_id=assigned_crew.id,
            crew_name=assigned_crew.name,
            # Base64がある場合はそれを優先、なければimage_urlを使用
            crew_image=assigned_crew.image_base64 if assigned_crew.image_base64 else assigned_crew.image_url,
            exp_gained=exp_gained,
            old_level=old_level,
            new_level=new_level,
            new_exp=new_exp,
            leveled_up=leveled_up,
            coin_gained=coin_gained,
        )

    except ClientError as e:
        logger.error(f"Bedrock API error: {e}")
        return FileSummaryResponse(
            success=False,
            error="AI要約の生成に失敗しました。しばらく待ってから再度お試しください。",
        )
    except Exception as e:
        logger.error(f"PDF summary error: {e}")
        return FileSummaryResponse(
            success=False,
            error=f"要約処理中にエラーが発生しました: {str(e)}",
        )


# ============================================================
# Director Mode API（プロジェクト自動構築）
# ============================================================

class RequiredInputSchema(BaseModel):
    key: str
    label: str
    type: str  # file/url/text


class TaskSchema(BaseModel):
    role: str
    assigned_crew_id: int
    assigned_crew_name: str
    assigned_crew_image: str
    instruction: str


class DirectorPlanRequest(BaseModel):
    user_goal: str


class DirectorPlanResponse(BaseModel):
    success: bool
    project_title: str | None = None
    description: str | None = None
    required_inputs: list[RequiredInputSchema] = []
    tasks: list[TaskSchema] = []
    partner_name: str | None = None
    partner_image: str | None = None
    error: str | None = None


@app.post("/api/director/plan")
async def create_project_plan(
    request: DirectorPlanRequest,
    db: Session = Depends(get_db),
) -> DirectorPlanResponse:
    """
    ユーザーのゴールからプロジェクト計画を自動生成する（Director Mode）

    - 相棒がPMとして、最適なクルー編成とタスクリストを作成
    - 必要な入力情報（ファイル/URL等）を特定
    """
    import boto3
    import json
    import re

    try:
        # 1. 相棒を取得
        partner = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
        if not partner:
            return DirectorPlanResponse(
                success=False,
                error="相棒が設定されていません。先に相棒を任命してください。",
            )

        # 2. 全クルーの情報を取得
        all_crews = db.query(CrewModel).all()
        if len(all_crews) < 2:
            return DirectorPlanResponse(
                success=False,
                error="プロジェクトを実行するには2人以上のクルーが必要です。",
            )

        # クルー情報をリスト化
        crew_info_list = []
        for crew in all_crews:
            skills = []
            for crew_skill in crew.skills:
                skills.append(f"{crew_skill.skill.name}(Lv.{crew_skill.level})")

            crew_info_list.append({
                "id": crew.id,
                "name": crew.name,
                "role": crew.role,
                "personality": crew.personality,
                "skills": skills,
                "is_partner": crew.is_partner,
            })

        crew_info_json = json.dumps(crew_info_list, ensure_ascii=False, indent=2)

        # 3. Bedrockでプロジェクト計画を生成
        prompt = f"""あなたは効率的なプロジェクトマネージャーです。
ユーザーの目標を達成するために、最小限のタスクで最大の成果を出すプランを作成してください。

## ユーザーの目標
{request.user_goal}

## 利用可能なクルー
{crew_info_json}

## プラン作成のルール（厳守）
1. **タスクは1〜2個が基本**（絶対に3個以上作らない）
   - 単純な作成タスク → 1タスク
   - 作成＋変換（例：記事執筆→HTML化）→ 2タスク
   - 情報収集・校正などの事前/事後作業は不要
2. **フォーマット変換は別タスクにする**
   - ユーザーが「HTMLで」「WordPress用に」等を指示 → 「執筆」+「HTML変換」の2タスク
   - ユーザーが「スライド化」「Excel化」等を指示 → 「作成」+「変換」の2タスク
3. 各タスクに最適なクルーを1人ずつ割り当てる（異なるクルーを使う）
4. **required_inputs は積極的に設定する**（成果物の品質向上のため）
   - 記事作成 → 「想定読者層」「希望文字数」「トーン（カジュアル/フォーマル）」など
   - 資料作成 → 「対象者」「用途」「ページ数目安」など
   - 分析系 → 「分析の観点」「重視するポイント」など

## 重要：成果物の文体について
記事・レポート・資料などのビジネス文書を作成する場合は、instructionに以下を必ず含めてください：
「※成果物は丁寧語・敬体で記述し、口語表現（だぜ、っす、じゃん等）は使用しないこと」

## 出力形式（必ずこのJSON形式で出力）
```json
{{
  "project_title": "プロジェクト名（20文字以内）",
  "description": "プロジェクトの簡潔な説明（50文字以内）",
  "required_inputs": [
    {{ "key": "input_key_1", "label": "ユーザーへの表示ラベル", "type": "file" }},
    {{ "key": "input_key_2", "label": "ユーザーへの表示ラベル", "type": "url" }},
    {{ "key": "input_key_3", "label": "ユーザーへの表示ラベル", "type": "text" }}
  ],
  "tasks": [
    {{ "role": "担当役割", "assigned_crew_id": クルーID, "instruction": "{{input_key_1}}を使って〜してください" }},
    {{ "role": "担当役割", "assigned_crew_id": クルーID, "instruction": "前のタスク結果を元に〜してください" }}
  ]
}}
```

## 注意事項
- typeは "file", "url", "text" のいずれか
- instructionには必要に応じて {{key}} でインプットを参照
- タスクは実行順に並べる
- 必ず有効なJSONのみを出力"""

        from botocore.config import Config
        bedrock_config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 2})
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=bedrock_config)

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
        })

        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
            body=body
        )

        response_body = json.loads(response["body"].read())
        ai_response = response_body["content"][0]["text"]

        # 4. JSONを抽出してパース
        # ```json ... ``` で囲まれている場合は抽出
        json_match = re.search(r'```json\s*(.*?)\s*```', ai_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # JSON形式の部分を探す
            json_match = re.search(r'\{[\s\S]*\}', ai_response)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError("AIからの応答にJSONが含まれていません")

        plan_data = json.loads(json_str)

        # 5. タスクにクルー情報を追加
        tasks_with_crew = []
        crew_map = {crew.id: crew for crew in all_crews}

        for task in plan_data.get("tasks", []):
            crew_id = task.get("assigned_crew_id")
            if crew_id in crew_map:
                crew = crew_map[crew_id]
                tasks_with_crew.append(TaskSchema(
                    role=task.get("role", ""),
                    assigned_crew_id=crew_id,
                    assigned_crew_name=crew.name,
                    assigned_crew_image=crew.image_url,
                    instruction=task.get("instruction", ""),
                ))
            else:
                # 存在しないクルーIDの場合、ランダムに割り当て
                fallback_crew = all_crews[0]
                tasks_with_crew.append(TaskSchema(
                    role=task.get("role", ""),
                    assigned_crew_id=fallback_crew.id,
                    assigned_crew_name=fallback_crew.name,
                    assigned_crew_image=fallback_crew.image_url,
                    instruction=task.get("instruction", ""),
                ))

        # 6. 必須入力をパース
        required_inputs = []
        for inp in plan_data.get("required_inputs", []):
            required_inputs.append(RequiredInputSchema(
                key=inp.get("key", ""),
                label=inp.get("label", ""),
                type=inp.get("type", "text"),
            ))

        logger.info(f"Director plan created: {plan_data.get('project_title')} with {len(tasks_with_crew)} tasks")

        return DirectorPlanResponse(
            success=True,
            project_title=plan_data.get("project_title", "新規プロジェクト"),
            description=plan_data.get("description", ""),
            required_inputs=required_inputs,
            tasks=tasks_with_crew,
            partner_name=partner.name,
            partner_image=partner.image_url,
        )

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in director plan: {e}")
        return DirectorPlanResponse(
            success=False,
            error="プロジェクト計画の解析に失敗しました。もう一度お試しください。",
        )
    except ClientError as e:
        logger.error(f"Bedrock API error in director plan: {e}")
        return DirectorPlanResponse(
            success=False,
            error="AI処理に失敗しました。しばらく待ってから再度お試しください。",
        )
    except Exception as e:
        logger.error(f"Director plan error: {e}")
        return DirectorPlanResponse(
            success=False,
            error=f"プロジェクト計画の作成中にエラーが発生しました: {str(e)}",
        )


class StartProjectRequest(BaseModel):
    project_title: str
    description: str
    user_goal: str
    required_inputs: list[RequiredInputSchema]
    tasks: list[TaskSchema]
    input_values: dict[str, str]  # key: value のマップ


class StartProjectResponse(BaseModel):
    success: bool
    project_id: int | None = None
    error: str | None = None


@app.post("/api/director/start")
async def start_project(
    request: StartProjectRequest,
    db: Session = Depends(get_db),
) -> StartProjectResponse:
    """
    プロジェクトを開始する（データベースに保存）
    """
    try:
        # 1. プロジェクトを作成
        project = Project(
            title=request.project_title,
            description=request.description,
            user_goal=request.user_goal,
            status="planning",
        )
        db.add(project)
        db.flush()  # IDを取得するため

        # 2. 入力データを保存
        for inp in request.required_inputs:
            value = request.input_values.get(inp.key)
            project_input = ProjectInput(
                project_id=project.id,
                key=inp.key,
                label=inp.label,
                input_type=inp.type,
                value=value if inp.type != "file" else None,
                file_path=value if inp.type == "file" else None,
            )
            db.add(project_input)

        # 3. タスクを保存
        for order, task in enumerate(request.tasks):
            project_task = ProjectTask(
                project_id=project.id,
                crew_id=task.assigned_crew_id,
                role=task.role,
                instruction=task.instruction,
                order=order,
                status="pending",
            )
            db.add(project_task)

        db.commit()

        logger.info(f"Project started: {project.title} (ID: {project.id})")

        return StartProjectResponse(
            success=True,
            project_id=project.id,
        )

    except Exception as e:
        logger.error(f"Start project error: {e}")
        db.rollback()
        return StartProjectResponse(
            success=False,
            error=f"プロジェクトの保存に失敗しました: {str(e)}",
        )


# ============================================================
# Director Mode - プロジェクト実行API
# ============================================================

class ExecuteProjectTaskResult(BaseModel):
    """タスク実行結果"""
    task_index: int
    role: str
    crew_name: str
    crew_image: str
    instruction: str
    result: str
    status: str  # completed / error


class ExecuteProjectResponse(BaseModel):
    """プロジェクト実行レスポンス"""
    success: bool
    project_title: str | None = None
    task_results: list[ExecuteProjectTaskResult] = []
    error: str | None = None


@app.post("/api/director/execute")
async def execute_project(
    project_title: str = Form(...),
    description: str = Form(...),
    user_goal: str = Form(...),
    required_inputs_json: str = Form(...),
    tasks_json: str = Form(...),
    input_values_json: str = Form(...),
    files: Optional[list[UploadFile]] = File(None),
    db: Session = Depends(get_db),
) -> ExecuteProjectResponse:
    """
    プロジェクトを実行する（タスクを順次処理）

    - 入力データ（PDF/URL/テキスト）を処理してコンテキスト構築
    - タスクを順番にBedrock AIで実行
    - 前のタスクの結果を次のタスクに引き継ぎ
    """
    from services.pdf_reader import extract_text_from_pdf
    from services.web_reader import fetch_web_content
    import io

    try:
        # JSONをパース
        required_inputs = json.loads(required_inputs_json)
        tasks = json.loads(tasks_json)
        input_values = json.loads(input_values_json)

        # ファイルをキーでマッピング
        file_map: dict[str, UploadFile] = {}
        if files is None:
            files = []
        logger.info(f"Received {len(files)} files")
        for f in files:
            logger.info(f"File received: filename={f.filename}, content_type={f.content_type}")
            # ファイル名からキーを取得（フロントエンドで key:::filename 形式で送信）
            if f.filename and ":::" in f.filename:
                key = f.filename.split(":::")[0]
                file_map[key] = f
                logger.info(f"Mapped file key: {key}")

        logger.info(f"File map keys: {list(file_map.keys())}")

        # 1. コンテキスト構築（入力データのテキスト化）
        context: dict[str, str] = {}
        logger.info(f"Required inputs: {required_inputs}")

        for inp in required_inputs:
            key = inp["key"]
            input_type = inp["type"]
            label = inp["label"]

            try:
                if input_type == "file":
                    # PDFファイルからテキスト抽出
                    logger.info(f"Looking for file with key '{key}' in file_map")
                    if key in file_map:
                        file = file_map[key]
                        content = await file.read()
                        logger.info(f"Read {len(content)} bytes from file")
                        text = extract_text_from_pdf(io.BytesIO(content))
                        context[key] = text
                        logger.info(f"Extracted text from PDF '{label}': {len(text)} chars")
                    else:
                        logger.warning(f"File not found for key '{key}'. Available keys: {list(file_map.keys())}")
                        context[key] = f"（{label}のファイルが見つかりませんでした）"

                elif input_type == "url":
                    # URLからコンテンツ取得
                    url = input_values.get(key, "")
                    if url:
                        # Google Sheetsの場合は専用サービスを使用
                        from services.sheet_service import is_google_sheets_url, read_public_sheet, format_csv_for_prompt
                        if is_google_sheets_url(url):
                            try:
                                csv_text = read_public_sheet(url)
                                text = format_csv_for_prompt(csv_text)
                                logger.info(f"Fetched Google Sheet from '{url}': {len(text)} chars")
                            except ValueError as e:
                                text = f"（スプレッドシートの読み込みに失敗しました: {str(e)}）"
                                logger.warning(f"Failed to read Google Sheet: {e}")
                        else:
                            text = fetch_web_content(url)
                            logger.info(f"Fetched web content from '{url}': {len(text)} chars")
                        context[key] = text
                    else:
                        context[key] = f"（{label}のURLが入力されていません）"

                elif input_type == "text":
                    # テキストをそのまま使用
                    context[key] = input_values.get(key, "")

            except Exception as e:
                logger.error(f"Error processing input '{key}': {e}")
                context[key] = f"（{label}の読み込みに失敗しました: {str(e)}）"

        # 2. タスクを順次実行
        task_results: list[ExecuteProjectTaskResult] = []
        previous_output = ""

        # Bedrockクライアント（タイムアウトを延長: デフォルト60秒→5分）
        from botocore.config import Config
        bedrock_config = Config(read_timeout=300, connect_timeout=10, retries={'max_attempts': 2})
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=bedrock_config)

        for idx, task in enumerate(tasks):
            role = task["role"]
            crew_id = task["assigned_crew_id"]
            crew_name = task["assigned_crew_name"]
            crew_image = task["assigned_crew_image"]
            instruction = task["instruction"]

            # クルー情報を取得（性格など）
            crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
            personality = crew.personality if crew else ""

            # 変数置換: {key} を context[key] で置換
            processed_instruction = instruction
            for key, value in context.items():
                processed_instruction = processed_instruction.replace(f"{{{key}}}", value)

            # プロンプト構築
            system_prompt = f"""あなたは「{crew_name}」という名前のクルー（社員）です。
役割: {role}
性格: {personality}

あなたはプロジェクトチームの一員として、与えられたタスクを遂行してください。
前のタスクの成果物がある場合は、それを参考にして作業を進めてください。"""

            user_prompt = f"""## あなたのタスク
{processed_instruction}

"""
            if previous_output:
                user_prompt += f"""## 前のタスクの成果物
{previous_output}

"""
            user_prompt += "上記の指示に従って、タスクを実行してください。"

            try:
                # Bedrock呼び出し
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7,
                })

                response = bedrock.invoke_model(
                    modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
                    body=body
                )

                response_body = json.loads(response["body"].read())
                result_text = response_body["content"][0]["text"]

                # 結果を保存
                task_results.append(ExecuteProjectTaskResult(
                    task_index=idx,
                    role=role,
                    crew_name=crew_name,
                    crew_image=crew_image,
                    instruction=instruction,
                    result=result_text,
                    status="completed"
                ))

                # 次のタスクへの引き継ぎ
                previous_output = result_text

                logger.info(f"Task {idx + 1} completed: {role} by {crew_name}")

            except Exception as e:
                logger.error(f"Error executing task {idx + 1}: {e}")
                task_results.append(ExecuteProjectTaskResult(
                    task_index=idx,
                    role=role,
                    crew_name=crew_name,
                    crew_image=crew_image,
                    instruction=instruction,
                    result=f"エラーが発生しました: {str(e)}",
                    status="error"
                ))
                # エラーでも次のタスクは続行
                previous_output = f"（前のタスクでエラーが発生しました: {str(e)}）"

        logger.info(f"Project execution completed: {project_title}")

        # 3. Slack通知を送信（指示に「Slack」が含まれている場合のみ）
        # ユーザーの目標やタスクの指示に「Slack」「slack」が含まれているかチェック
        should_notify_slack = False
        slack_keywords = ["slack", "Slack", "SLACK", "スラック"]

        # ユーザーゴールをチェック
        if any(keyword in user_goal for keyword in slack_keywords):
            should_notify_slack = True

        # タスクの指示をチェック
        if not should_notify_slack:
            for task in tasks:
                if any(keyword in task.get("instruction", "") for keyword in slack_keywords):
                    should_notify_slack = True
                    break

        if should_notify_slack:
            try:
                from services.slack_service import send_project_completion
                task_summaries = [
                    {
                        "role": r.role,
                        "crew_name": r.crew_name,
                        "result": r.result
                    }
                    for r in task_results
                ]
                send_project_completion(project_title, task_summaries)
                logger.info("Slack notification sent (keyword detected in instructions)")
            except Exception as slack_error:
                logger.warning(f"Failed to send Slack notification: {slack_error}")
        else:
            logger.info("Slack notification skipped (no 'Slack' keyword in instructions)")

        return ExecuteProjectResponse(
            success=True,
            project_title=project_title,
            task_results=task_results,
        )

    except Exception as e:
        logger.error(f"Execute project error: {e}")
        return ExecuteProjectResponse(
            success=False,
            error=f"プロジェクトの実行に失敗しました: {str(e)}",
        )


@app.post("/api/director/execute-stream")
async def execute_project_stream(
    project_title: str = Form(...),
    description: str = Form(...),
    user_goal: str = Form(...),
    required_inputs_json: str = Form(...),
    tasks_json: str = Form(...),
    input_values_json: str = Form(...),
    google_access_token: Optional[str] = Form(None),
    files: Optional[list[UploadFile]] = File(None),
    db: Session = Depends(get_db),
):
    """
    プロジェクトを実行し、SSEでタスクごとに進捗を返す
    スライド作成タスクの場合はGoogle Slides APIでスライドを生成
    """
    from starlette.responses import StreamingResponse
    from services.pdf_reader import extract_text_from_pdf
    from services.web_reader import fetch_web_content
    import io
    import asyncio

    async def generate():
        try:
            # JSONをパース
            required_inputs = json.loads(required_inputs_json)
            tasks = json.loads(tasks_json)
            input_values = json.loads(input_values_json)

            # ファイルをキーでマッピング
            file_map: dict[str, UploadFile] = {}
            if files:
                for f in files:
                    if f.filename and ":::" in f.filename:
                        key = f.filename.split(":::")[0]
                        file_map[key] = f

            # 1. コンテキスト構築
            context: dict[str, str] = {}

            for inp in required_inputs:
                key = inp["key"]
                input_type = inp["type"]
                label = inp["label"]

                try:
                    if input_type == "file":
                        if key in file_map:
                            file = file_map[key]
                            content = await file.read()
                            text = extract_text_from_pdf(io.BytesIO(content))
                            context[key] = text
                        else:
                            context[key] = f"（{label}のファイルが見つかりませんでした）"

                    elif input_type == "url":
                        url = input_values.get(key, "")
                        if url:
                            from services.sheet_service import is_google_sheets_url, read_public_sheet, format_csv_for_prompt
                            if is_google_sheets_url(url):
                                try:
                                    csv_text = read_public_sheet(url)
                                    text = format_csv_for_prompt(csv_text)
                                except ValueError as e:
                                    text = f"（スプレッドシートの読み込みに失敗しました: {str(e)}）"
                            else:
                                text = fetch_web_content(url)
                            context[key] = text
                        else:
                            context[key] = f"（{label}のURLが入力されていません）"

                    elif input_type == "text":
                        context[key] = input_values.get(key, "")

                except Exception as e:
                    context[key] = f"（{label}の読み込みに失敗しました: {str(e)}）"

            # 開始イベントを送信
            total_tasks = len(tasks)
            yield f"data: {json.dumps({'type': 'start', 'total_tasks': total_tasks})}\n\n"
            await asyncio.sleep(0)  # イベントループに制御を戻してフラッシュ

            # 2. タスクを順次実行
            task_results = []
            previous_output = ""

            # タイムアウトを延長（デフォルト60秒→5分）
            from botocore.config import Config
            bedrock_config = Config(
                read_timeout=300,  # 5分
                connect_timeout=10,
                retries={'max_attempts': 2}
            )
            bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=bedrock_config)

            for idx, task in enumerate(tasks):
                role = task["role"]
                crew_id = task["assigned_crew_id"]
                crew_name = task["assigned_crew_name"]
                crew_image = task["assigned_crew_image"]
                instruction = task["instruction"]

                # クルー情報を取得（削除されている可能性があるため確認）
                crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()

                # クルーが存在しない場合（削除済み）は別のクルーにフォールバック
                if not crew:
                    # 最初に見つかったクルーを代わりに割り当て
                    fallback_crew = db.query(CrewModel).first()
                    if fallback_crew:
                        crew = fallback_crew
                        crew_id = crew.id
                        crew_name = crew.name
                        crew_image = crew.image_url
                        logger.warning(f"Crew {task['assigned_crew_id']} not found. Fallback to {crew.name}")
                    else:
                        # クルーが1人もいない場合はエラー
                        yield f"data: {json.dumps({'type': 'error', 'error': 'クルーが見つかりません'})}\n\n"
                        return

                personality = crew.personality if crew else ""

                # 開始通知を送信
                yield f"data: {json.dumps({'type': 'task_start', 'task_index': idx, 'crew_name': crew_name, 'role': role})}\n\n"
                await asyncio.sleep(0)  # イベントループに制御を戻してフラッシュ

                # 変数置換
                processed_instruction = instruction
                for key, value in context.items():
                    processed_instruction = processed_instruction.replace(f"{{{key}}}", value)

                # スライド作成タスクかどうかを判定
                slide_keywords = ['スライド', 'プレゼン', 'presentation', 'slide', 'ppt', 'パワポ', 'パワーポイント']
                is_slide_task = any(keyword in processed_instruction.lower() for keyword in slide_keywords)

                # シート作成タスクかどうかを判定
                sheet_keywords = ['スプレッドシート', 'シート', '表', '一覧', 'リスト', 'spreadsheet', 'sheet', 'excel', 'csv']
                is_sheet_task = any(keyword in processed_instruction.lower() for keyword in sheet_keywords)

                # プロンプト構築
                system_prompt = f"""あなたは「{crew_name}」という名前のクルー（社員）です。
役割: {role}
性格: {personality}

あなたはプロジェクトチームの一員として、与えられたタスクを遂行してください。
前のタスクの成果物がある場合は、それを参考にして作業を進めてください。"""

                # タスク指示を拡張
                task_instruction = processed_instruction

                # シート作成タスクの場合（スライドより先に判定）
                if is_sheet_task and not is_slide_task and google_access_token:
                    task_instruction += """

【スプレッドシート作成の指示】
データを整理してスプレッドシートに適した表形式で出力してください。

■ 出力フォーマット（必ずMarkdown表形式で）：

| 列1 | 列2 | 列3 |
|-----|-----|-----|
| データ1 | データ2 | データ3 |

■ 表作成のルール：
1. 必ずMarkdown表形式（| で区切る）で出力する
2. 1行目はヘッダー行（項目名）にする
3. 10〜20行程度のデータを作成する"""

                # スライド作成タスクの場合
                elif is_slide_task and google_access_token:
                    task_instruction += """

【プレゼンテーション作成の指示】
魅力的で説得力のあるスライドを作成してください。以下の形式で出力してください：

■ 出力フォーマット（必ずこの形式で）：

スライド1: [インパクトのあるタイトル]
📌 キーメッセージ
• ポイント1（具体的な数字やデータがあれば含める）
• ポイント2
• ポイント3

スライド2: [セクションタイトル]
💡 サブタイトルや補足
• 要点を簡潔に
• 具体例や事例
• 数値データは「〇〇%」など強調

■ スライド作成のルール：
1. 各スライドは「スライドN:」で始める
2. 1スライドあたり3〜5個の箇条書き
3. 絵文字を見出しに1つ使用（📌💡🎯✅📊🚀💪など）
4. 5〜8枚程度のスライドを作成"""

                user_prompt = f"""## あなたのタスク
{task_instruction}

"""
                if previous_output:
                    user_prompt += f"""## 前のタスクの成果物
{previous_output}

"""
                user_prompt += "上記の指示に従って、タスクを実行してください。"

                try:
                    body = json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4096,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                        "temperature": 0.7,
                    })

                    # Bedrockの同期呼び出しを別スレッドで実行（ストリーミングをブロックしないため）
                    loop = asyncio.get_event_loop()
                    def call_bedrock():
                        return bedrock.invoke_model(
                            modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
                            body=body
                        )
                    response = await loop.run_in_executor(None, call_bedrock)

                    response_body = json.loads(response["body"].read())
                    result_text = response_body["content"][0]["text"]

                    # スライド生成（スライドタスク + Google認証済みの場合）
                    slide_url = None
                    slide_id = None
                    if is_slide_task and google_access_token:
                        try:
                            logger.info(f"Attempting to create Google Slides for task {idx + 1}...")
                            pages = _parse_slides_from_ai_output(result_text)
                            if pages:
                                title = _extract_slide_title(instruction, result_text)
                                slide_result = create_presentation(
                                    access_token=google_access_token,
                                    title=title,
                                    pages=pages
                                )
                                slide_url = slide_result["presentationUrl"]
                                slide_id = slide_result["presentationId"]
                                logger.info(f"Google Slides created: {slide_url}")
                                # 結果にスライドURLを追加
                                result_text = f"{result_text}\n\n📊 **Googleスライドを作成しました！**\n{slide_url}"
                            else:
                                logger.warning("Could not parse slides from AI output")
                        except Exception as slide_error:
                            logger.error(f"Failed to create Google Slides: {slide_error}")

                    # シート生成（シートタスク + Google認証済み + スライドタスクではない場合）
                    sheet_url = None
                    sheet_id = None
                    if is_sheet_task and not is_slide_task and google_access_token:
                        try:
                            logger.info(f"Attempting to create Google Sheets for task {idx + 1}...")
                            table_data = parse_table_from_text(result_text)
                            if table_data:
                                title = extract_sheet_title(instruction, result_text)
                                sheet_result = create_spreadsheet(
                                    access_token=google_access_token,
                                    title=title,
                                    data=table_data
                                )
                                sheet_url = sheet_result["spreadsheetUrl"]
                                sheet_id = sheet_result["spreadsheetId"]
                                logger.info(f"Google Sheets created: {sheet_url}")
                                result_text = f"{result_text}\n\n📋 **Googleスプレッドシートを作成しました！**\n{sheet_url}"
                            else:
                                logger.warning("Could not parse table data from AI output")
                        except Exception as sheet_error:
                            logger.error(f"Failed to create Google Sheets: {sheet_error}")

                    # EXP付与とTaskLog保存（クルーが存在する場合）
                    exp_gained = 0
                    leveled_up = False
                    old_level = crew.level if crew else 1
                    new_level = old_level
                    new_exp = crew.exp if crew else 0

                    if crew:
                        exp_gained = 15  # +15 EXP（固定）
                        crew.exp += exp_gained

                        # レベルアップ判定（100 EXP で 1 レベルアップ）
                        if crew.exp >= 100:
                            crew.exp -= 100
                            crew.level += 1
                            leveled_up = True

                        new_exp = crew.exp
                        new_level = crew.level

                        # TaskLogを保存
                        task_log = TaskLog(
                            crew_id=crew.id,
                            user_input=f"[プロジェクト: {project_title}] {instruction}",
                            ai_response=result_text[:1000],  # 長すぎる場合は切り詰め
                            exp_gained=exp_gained,
                        )
                        db.add(task_log)

                        # コイン報酬（50コイン）
                        user = db.query(UserModel).first()
                        if user:
                            user.coin += 50
                            if leveled_up:
                                user.ruby += 5

                        db.commit()
                        logger.info(f"Added {exp_gained} EXP to {crew_name}. Level: {old_level} -> {new_level}")

                    task_result = {
                        "task_index": idx,
                        "role": role,
                        "crew_name": crew_name,
                        "crew_id": crew_id,
                        "crew_image": crew_image,
                        "instruction": instruction,
                        "result": result_text,
                        "status": "completed",
                        "slide_url": slide_url,
                        "slide_id": slide_id,
                        "sheet_url": sheet_url,
                        "sheet_id": sheet_id,
                        "exp_gained": exp_gained,
                        "old_level": old_level,
                        "new_level": new_level,
                        "new_exp": new_exp,
                        "leveled_up": leveled_up,
                    }
                    task_results.append(task_result)
                    previous_output = result_text

                    # 完了通知を送信
                    yield f"data: {json.dumps({'type': 'task_complete', 'task_index': idx, 'task_result': task_result})}\n\n"
                    await asyncio.sleep(0)  # イベントループに制御を戻してフラッシュ

                    logger.info(f"Task {idx + 1} completed: {role} by {crew_name}")

                except Exception as e:
                    logger.error(f"Error executing task {idx + 1}: {e}")
                    task_result = {
                        "task_index": idx,
                        "role": role,
                        "crew_name": crew_name,
                        "crew_image": crew_image,
                        "instruction": instruction,
                        "result": f"エラーが発生しました: {str(e)}",
                        "status": "error"
                    }
                    task_results.append(task_result)
                    previous_output = f"（前のタスクでエラーが発生しました: {str(e)}）"

                    yield f"data: {json.dumps({'type': 'task_complete', 'task_index': idx, 'task_result': task_result})}\n\n"
                    await asyncio.sleep(0)  # イベントループに制御を戻してフラッシュ

            # Slack通知
            should_notify_slack = False
            slack_keywords = ["slack", "Slack", "SLACK", "スラック"]
            for keyword in slack_keywords:
                if keyword in user_goal:
                    should_notify_slack = True
                    break
                for task in tasks:
                    if keyword in task.get("instruction", ""):
                        should_notify_slack = True
                        break
                if should_notify_slack:
                    break

            if should_notify_slack:
                try:
                    from services.slack_notifier import send_project_completion
                    task_summaries = [
                        {"crew_name": r["crew_name"], "role": r["role"], "status": r["status"]}
                        for r in task_results
                    ]
                    send_project_completion(project_title, task_summaries)
                except Exception as slack_error:
                    logger.warning(f"Failed to send Slack notification: {slack_error}")

            # 完了イベントを送信
            yield f"data: {json.dumps({'type': 'complete', 'success': True, 'project_title': project_title, 'task_results': task_results})}\n\n"

            logger.info(f"Project execution completed: {project_title}")

        except Exception as e:
            logger.error(f"Execute project stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ============================================================
# LangGraph ベースのディレクターモード（自己修正ループ）
# ============================================================

class LangGraphDirectorRequest(BaseModel):
    """LangGraphディレクターモードのリクエスト"""
    task: str
    crew_id: Optional[int] = None  # 指定しない場合は相棒を使用
    max_revisions: int = 3


class LangGraphDirectorResponse(BaseModel):
    """LangGraphディレクターモードのレスポンス"""
    success: bool
    final_result: Optional[str] = None
    score: int = 0
    critique: str = ""
    revision_count: int = 0
    crew_name: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/director/langgraph", response_model=LangGraphDirectorResponse)
async def execute_langgraph_director(
    request: LangGraphDirectorRequest,
    db: Session = Depends(get_db),
):
    """
    LangGraphベースのディレクターモード（自己修正ループ）

    クルーが成果物を作成 → ディレクターが評価 → 80点未満なら修正指示 → 再作成
    このループを最大max_revisions回繰り返し、品質の高い成果物を生成する。

    Features:
    - 自動品質評価（0-100点スコアリング）
    - 改善フィードバックの自動生成
    - 最大修正回数の制限
    - クルーの性格を反映した回答

    Args:
        request: タスク内容、クルーID（オプション）、最大修正回数

    Returns:
        最終成果物、スコア、評価コメント、修正回数
    """
    logger.info(f"[LangGraph Director] Request: task={request.task[:50]}..., crew_id={request.crew_id}")

    try:
        # クルーを取得（指定がなければ相棒を使用）
        if request.crew_id:
            crew = db.query(CrewModel).filter(CrewModel.id == request.crew_id).first()
            if not crew:
                return LangGraphDirectorResponse(
                    success=False,
                    error=f"クルーID {request.crew_id} が見つかりません",
                )
        else:
            # 相棒を取得
            crew = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
            if not crew:
                return LangGraphDirectorResponse(
                    success=False,
                    error="相棒が設定されていません。先に相棒を任命してください。",
                )

        logger.info(f"[LangGraph Director] Using crew: {crew.name} (ID: {crew.id})")

        # LangGraphワークフローを実行
        result = await run_director_workflow(
            task=request.task,
            crew_name=crew.name,
            crew_personality=crew.personality or "",
            max_revisions=request.max_revisions,
        )

        if result["success"]:
            # タスクログを記録
            try:
                task_log = TaskLog(
                    crew_id=crew.id,
                    task=f"[LangGraph Director] {request.task[:200]}",
                    result=result["final_result"][:2000] if result["final_result"] else None,
                    status="completed",
                    exp_earned=15,  # ディレクターモードは通常より多めのEXP
                )
                db.add(task_log)

                # EXP付与
                crew.exp += 15
                if crew.exp >= crew.level * 100:
                    crew.exp -= crew.level * 100
                    crew.level += 1
                    logger.info(f"[LangGraph Director] {crew.name} leveled up to {crew.level}!")

                db.commit()
            except Exception as db_error:
                logger.warning(f"[LangGraph Director] Failed to save task log: {db_error}")

            return LangGraphDirectorResponse(
                success=True,
                final_result=result["final_result"],
                score=result["score"],
                critique=result["critique"],
                revision_count=result["revision_count"],
                crew_name=result["crew_name"],
                error=None,
            )
        else:
            return LangGraphDirectorResponse(
                success=False,
                error=result.get("error", "Unknown error"),
            )

    except Exception as e:
        logger.error(f"[LangGraph Director] Error: {e}")
        return LangGraphDirectorResponse(
            success=False,
            error=str(e),
        )


@app.post("/api/director/langgraph-stream")
async def execute_langgraph_director_stream(
    request: LangGraphDirectorRequest,
    db: Session = Depends(get_db),
):
    """
    LangGraphベースのディレクターモード（SSEストリーミング版）

    進捗をリアルタイムで通知しながら実行する。

    Events:
    - start: 実行開始
    - draft: 成果物作成完了
    - evaluation: 評価完了
    - revision: 修正開始
    - complete: 全体完了
    - error: エラー発生
    """
    from fastapi.responses import StreamingResponse

    async def generate():
        try:
            # クルーを取得
            if request.crew_id:
                crew = db.query(CrewModel).filter(CrewModel.id == request.crew_id).first()
                if not crew:
                    yield f"data: {json.dumps({'type': 'error', 'error': f'クルーID {request.crew_id} が見つかりません'})}\n\n"
                    return
            else:
                crew = db.query(CrewModel).filter(CrewModel.is_partner == True).first()
                if not crew:
                    yield f"data: {json.dumps({'type': 'error', 'error': '相棒が設定されていません'})}\n\n"
                    return

            # 開始イベント
            yield f"data: {json.dumps({'type': 'start', 'crew_name': crew.name, 'max_revisions': request.max_revisions})}\n\n"

            # LangGraphワークフローを実行（進捗を通知）
            from graphs.workflow import build_director_graph
            from graphs.state import create_initial_state

            app_graph = build_director_graph().compile()
            initial_state = create_initial_state(
                task=request.task,
                crew_name=crew.name,
                crew_personality=crew.personality or "",
                max_revisions=request.max_revisions,
            )

            final_state = None
            async for state in app_graph.astream(initial_state):
                for node_name, node_state in state.items():
                    final_state = node_state

                    if node_name == "generator":
                        yield f"data: {json.dumps({'type': 'draft', 'revision': node_state.get('revision_count', 0), 'draft_preview': node_state.get('draft', '')[:200]})}\n\n"
                    elif node_name == "reflector":
                        yield f"data: {json.dumps({'type': 'evaluation', 'score': node_state.get('score', 0), 'critique': node_state.get('critique', ''), 'is_complete': node_state.get('is_complete', False)})}\n\n"

                        if not node_state.get('is_complete', False):
                            yield f"data: {json.dumps({'type': 'revision', 'next_revision': node_state.get('revision_count', 0) + 1})}\n\n"

            if final_state:
                # タスクログを記録
                try:
                    task_log = TaskLog(
                        crew_id=crew.id,
                        task=f"[LangGraph Director] {request.task[:200]}",
                        result=final_state.get("final_result", final_state.get("draft", ""))[:2000],
                        status="completed",
                        exp_earned=15,
                    )
                    db.add(task_log)
                    crew.exp += 15
                    if crew.exp >= crew.level * 100:
                        crew.exp -= crew.level * 100
                        crew.level += 1
                    db.commit()
                except Exception as db_error:
                    logger.warning(f"[LangGraph Director Stream] DB error: {db_error}")

                # 完了イベント
                yield f"data: {json.dumps({'type': 'complete', 'success': True, 'final_result': final_state.get('final_result') or final_state.get('draft', ''), 'score': final_state.get('score', 0), 'critique': final_state.get('critique', ''), 'revision_count': final_state.get('revision_count', 0), 'crew_name': crew.name})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': 'ワークフローが結果を返しませんでした'})}\n\n"

        except Exception as e:
            logger.error(f"[LangGraph Director Stream] Error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# ============================================================
# プロジェクト実行 v2 (LangGraph + 自己修正ループ)
# ============================================================

@app.post("/api/director/execute-stream-v2")
async def execute_project_stream_v2(
    project_title: str = Form(...),
    description: str = Form(...),
    user_goal: str = Form(...),
    required_inputs_json: str = Form(...),
    tasks_json: str = Form(...),
    input_values_json: str = Form(...),
    files: Optional[list[UploadFile]] = File(None),
    google_access_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    プロジェクト実行 v2（Generatorのみ = シンプル高速版）

    各タスクでクルーが1回だけ成果物を作成:
    - Reflectorを使わないためAPIコール数を大幅削減
    - タスク数を2-3に抑えることでさらに高速化

    SSEで進捗をリアルタイム通知:
    - start: プロジェクト開始
    - task_start: タスク開始
    - generation_complete: クルーが成果物作成完了
    - task_complete: タスク完了
    - complete: プロジェクト全体完了
    - error: エラー発生
    """
    from services.pdf_reader import extract_text_from_pdf
    from services.web_reader import fetch_web_content
    from graphs import run_generator_only_stream
    from starlette.responses import StreamingResponse
    import io

    async def generate():
        try:
            # JSONをパース
            required_inputs = json.loads(required_inputs_json)
            tasks = json.loads(tasks_json)
            input_values = json.loads(input_values_json)

            logger.info(f"[Director v2] Starting project: {project_title} with {len(tasks)} tasks")

            # ファイルをキーでマッピング
            file_map: dict[str, UploadFile] = {}
            if files is None:
                files_list = []
            else:
                files_list = files
            for f in files_list:
                if f.filename and ":::" in f.filename:
                    key = f.filename.split(":::")[0]
                    file_map[key] = f

            # 1. 入力データを処理してコンテキスト構築
            context: dict[str, str] = {}
            for input_def in required_inputs:
                key = input_def["key"]
                label = input_def["label"]
                input_type = input_def["type"]

                try:
                    if input_type == "file":
                        if key in file_map:
                            file = file_map[key]
                            content = await file.read()
                            text = extract_text_from_pdf(io.BytesIO(content))
                            context[key] = text
                        else:
                            context[key] = f"（{label}のファイルが提供されていません）"

                    elif input_type == "url":
                        url = input_values.get(key, "")
                        if url:
                            text = await fetch_web_content(url)
                            context[key] = text
                        else:
                            context[key] = f"（{label}のURLが入力されていません）"

                    elif input_type == "text":
                        context[key] = input_values.get(key, "")

                except Exception as e:
                    logger.error(f"[Director v2] Error processing input '{key}': {e}")
                    context[key] = f"（{label}の読み込みに失敗しました: {str(e)}）"

            # 開始イベント
            yield f"data: {json.dumps({'type': 'start', 'total_tasks': len(tasks), 'project_title': project_title})}\n\n"

            # 2. タスクを順次実行（LangGraphで自己修正ループ）
            task_results = []
            previous_output = ""
            import asyncio

            for idx, task in enumerate(tasks):
                # タスク間に遅延を入れてレート制限を回避（2タスク目以降）
                if idx > 0:
                    logger.info(f"[Director v2] Waiting 15 seconds before task {idx + 1}...")
                    await asyncio.sleep(15)  # 15秒待機（レート制限回避）
                role = task["role"]
                crew_id = task["assigned_crew_id"]
                crew_name = task["assigned_crew_name"]
                crew_image = task["assigned_crew_image"]
                instruction = task["instruction"]

                # クルー情報を取得
                crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
                personality = crew.personality if crew else ""

                # 変数置換
                processed_instruction = instruction
                for key, value in context.items():
                    processed_instruction = processed_instruction.replace(f"{{{key}}}", value)

                # 前のタスクの成果物を追加
                if previous_output:
                    full_task = f"""## あなたのタスク
{processed_instruction}

## 前のタスクの成果物
{previous_output}

上記の指示に従って、タスクを実行してください。"""
                else:
                    full_task = f"""## あなたのタスク
{processed_instruction}

上記の指示に従って、タスクを実行してください。"""

                # タスク開始イベント
                yield f"data: {json.dumps({'type': 'task_start', 'task_index': idx, 'role': role, 'crew_name': crew_name, 'crew_image': crew_image, 'total_tasks': len(tasks)})}\n\n"

                try:
                    # Generatorのみを実行（Reflectorなし = APIコール削減）
                    final_result = ""
                    final_score = 0
                    final_critique = ""
                    revision_count = 0

                    async for event in run_generator_only_stream(
                        task=full_task,
                        crew_name=crew_name,
                        crew_personality=personality,
                        crew_image=crew_image,
                    ):
                        event_type = event.get("type", "")

                        # イベントをフロントエンドに転送
                        if event_type in ["generation_complete", "reflection_complete", "revision_start"]:
                            # タスクインデックスを追加
                            event["task_index"] = idx
                            yield f"data: {json.dumps(event)}\n\n"

                        elif event_type == "workflow_complete":
                            final_result = event.get("final_result", "")
                            final_score = event.get("score", 0)
                            final_critique = event.get("critique", "")
                            revision_count = event.get("revision_count", 0)

                        elif event_type == "workflow_error":
                            raise Exception(event.get("error", "Unknown error"))

                    # タスク完了イベント
                    task_result = {
                        "task_index": idx,
                        "role": role,
                        "crew_name": crew_name,
                        "crew_image": crew_image,
                        "instruction": instruction,
                        "result": final_result,
                        "score": final_score,
                        "critique": final_critique,
                        "revision_count": revision_count,
                        "status": "completed",
                    }
                    task_results.append(task_result)

                    yield f"data: {json.dumps({'type': 'task_complete', 'task_result': task_result})}\n\n"

                    # 次のタスクへの引き継ぎ
                    previous_output = final_result

                    # クルーのEXP加算（スコアに応じてボーナス）
                    if crew:
                        base_exp = 15
                        score_bonus = max(0, (final_score - 60) // 10) * 5  # 70点で+5, 80点で+10, 90点で+15
                        total_exp = base_exp + score_bonus

                        old_level = crew.level
                        crew.exp += total_exp

                        # レベルアップチェック
                        while crew.exp >= crew.level * 100:
                            crew.exp -= crew.level * 100
                            crew.level += 1

                        db.commit()
                        logger.info(f"[Director v2] Added {total_exp} EXP to {crew_name} (score bonus: {score_bonus}). Level: {old_level} -> {crew.level}")

                    logger.info(f"[Director v2] Task {idx + 1} completed: {role} by {crew_name}, score={final_score}, revisions={revision_count}")

                except Exception as e:
                    logger.error(f"[Director v2] Error executing task {idx + 1}: {e}")
                    task_result = {
                        "task_index": idx,
                        "role": role,
                        "crew_name": crew_name,
                        "crew_image": crew_image,
                        "instruction": instruction,
                        "result": f"エラーが発生しました: {str(e)}",
                        "score": 0,
                        "revision_count": 0,
                        "status": "error",
                    }
                    task_results.append(task_result)
                    yield f"data: {json.dumps({'type': 'task_complete', 'task_result': task_result})}\n\n"

            # 完了イベント
            yield f"data: {json.dumps({'type': 'complete', 'project_title': project_title, 'total_tasks': len(tasks)})}\n\n"
            logger.info(f"[Director v2] Project execution completed: {project_title}")

        except Exception as e:
            logger.error(f"[Director v2] Project error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

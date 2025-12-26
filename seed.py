import hashlib
from sqlalchemy.orm import Session

from models import Crew, Gadget, Skill, PersonalityItem, User


# ============================================================
# 認証用ユーザーデータ（test/demo）
# ============================================================
def hash_password(password: str) -> str:
    """パスワードをSHA-256でハッシュ化"""
    return hashlib.sha256(password.encode()).hexdigest()


INITIAL_USERS = [
    {
        "username": "test",
        "hashed_password": hash_password("test"),
        "is_demo": False,
        "company_name": "テスト株式会社",
        "user_name": "テストユーザー",
        "job_title": "エンジニア",
        "coin": 3000,
        "ruby": 50,
    },
    {
        "username": "demo",
        "hashed_password": hash_password("demo"),
        "is_demo": True,
        "company_name": "デモ株式会社",
        "user_name": "デモユーザー",
        "job_title": "マネージャー",
        "coin": 3000,
        "ruby": 50,
    },
]

# ============================================================
# Roles (役割) - stats配分の定義
# ============================================================
ROLES = {
    "Sales": {
        "label": "営業",
        "stats_weight": {"speed": 1.3, "creativity": 0.9, "mood": 0.8},  # SPEED重視
        "primary_skills": ["Negotiation", "Presentation"],
    },
    "Marketer": {
        "label": "マーケター",
        "stats_weight": {"speed": 0.9, "creativity": 1.3, "mood": 0.8},  # CREATIVITY重視
        "primary_skills": ["Copywriting", "Ideation"],
    },
    "Engineer": {
        "label": "エンジニア",
        "stats_weight": {"speed": 1.0, "creativity": 1.0, "mood": 1.0},  # Balance
        "primary_skills": ["Debugging", "Logical Thinking"],
    },
    "Designer": {
        "label": "デザイナー",
        "stats_weight": {"speed": 0.7, "creativity": 1.5, "mood": 0.8},  # CREATIVITY特化
        "primary_skills": ["Design Thinking", "Ideation"],
    },
    "Admin": {
        "label": "事務",
        "stats_weight": {"speed": 0.9, "creativity": 0.8, "mood": 1.3},  # MOOD重視
        "primary_skills": ["Time Management", "Multitasking"],
    },
    "Manager": {
        "label": "マネージャー",
        "stats_weight": {"speed": 1.1, "creativity": 1.1, "mood": 1.1},  # All Rounder
        "primary_skills": ["Presentation", "Negotiation", "Time Management"],
    },
}


# ============================================================
# Personalities (性格) - 口調・振る舞いの定義
# ============================================================
PERSONALITIES = {
    "Hot-blooded": {
        "label": "熱血",
        "description": "情熱的で行動力がある。語尾に「〜だぜ！」「〜するぜ！」を使う。",
        "emoji": "🔥",
        "tone": "熱血で情熱的。ポジティブで力強い言葉を使う。",
    },
    "Cool": {
        "label": "クール",
        "description": "冷静沈着で感情をあまり表に出さない。「...」を多用する。",
        "emoji": "❄️",
        "tone": "クールで寡黙。短い文で論理的に話す。",
    },
    "Gentle": {
        "label": "おだやか",
        "description": "穏やかで優しい。丁寧な敬語を使い、相手を気遣う。",
        "emoji": "🌸",
        "tone": "穏やかで優しい。丁寧な敬語を使う。",
    },
    "Serious": {
        "label": "真面目",
        "description": "真面目で責任感が強い。論理的で正確な表現を好む。",
        "emoji": "📚",
        "tone": "真面目で責任感が強い。断定的な表現を使う。",
    },
    "Playful": {
        "label": "わんぱく",
        "description": "明るく元気で好奇心旺盛。「〜だよ！」「〜じゃん！」を使う。",
        "emoji": "☀️",
        "tone": "明るくフレンドリー。カジュアルな表現を使う。",
    },
    "Cautious": {
        "label": "慎重",
        "description": "慎重で用心深い。リスクを考慮した発言をする。",
        "emoji": "🔍",
        "tone": "慎重で分析的。「〜かもしれません」「念のため」を多用。",
    },
}


# ============================================================
# Skills (スキル) マスタデータ - 日本語表記
# ============================================================
INITIAL_SKILLS = [
    # Intelligence (知性系)
    {
        "name": "データ分析",
        "skill_type": "Intelligence",
        "description": "データを分析し、洞察を導き出す能力",
        "bonus_effect": "creativity",
    },
    {
        "name": "論理的思考",
        "skill_type": "Intelligence",
        "description": "論理的に物事を考え、問題を解決する能力",
        "bonus_effect": "speed",
    },
    {
        "name": "情報収集",
        "skill_type": "Intelligence",
        "description": "必要な情報を効率的に収集する能力",
        "bonus_effect": "speed",
    },
    # Creative (創造系)
    {
        "name": "ライティング",
        "skill_type": "Creative",
        "description": "魅力的な文章を書く能力",
        "bonus_effect": "creativity",
    },
    {
        "name": "発想力",
        "skill_type": "Creative",
        "description": "新しいアイデアを生み出す能力",
        "bonus_effect": "creativity",
    },
    {
        "name": "デザイン思考",
        "skill_type": "Creative",
        "description": "デザイン思考で問題を解決する能力",
        "bonus_effect": "creativity",
    },
    # Communication (コミュニケーション系)
    {
        "name": "交渉力",
        "skill_type": "Communication",
        "description": "交渉を有利に進める能力",
        "bonus_effect": "mood",
    },
    {
        "name": "おもてなし",
        "skill_type": "Communication",
        "description": "おもてなしの心で接客する能力",
        "bonus_effect": "mood",
    },
    {
        "name": "プレゼン力",
        "skill_type": "Communication",
        "description": "プレゼンテーションで人を惹きつける能力",
        "bonus_effect": "mood",
    },
    # Execution (実行系)
    {
        "name": "マルチタスク",
        "skill_type": "Execution",
        "description": "複数のタスクを同時に処理する能力",
        "bonus_effect": "speed",
    },
    {
        "name": "デバッグ",
        "skill_type": "Execution",
        "description": "バグを発見し修正する能力",
        "bonus_effect": "speed",
    },
    {
        "name": "時間管理",
        "skill_type": "Execution",
        "description": "時間を効率的に管理する能力",
        "bonus_effect": "speed",
    },
]


# ============================================================
# 初期クルー（既存データ）- 新しい役割・性格に対応
# ============================================================
INITIAL_CREWS = [
    {
        "name": "フレイミー",
        "role": "Sales",
        "level": 12,
        "exp": 1200,
        "image_url": "/images/crews/monster_1.png",
        "personality": "Hot-blooded",
    },
    {
        "name": "アクアン",
        "role": "Admin",
        "level": 8,
        "exp": 640,
        "image_url": "/images/crews/monster_2.png",
        "personality": "Gentle",
    },
    {
        "name": "ロッキー",
        "role": "Engineer",
        "level": 15,
        "exp": 2250,
        "image_url": "/images/crews/monster_3.png",
        "personality": "Serious",
    },
    {
        "name": "ウィンディ",
        "role": "Marketer",
        "level": 10,
        "exp": 900,
        "image_url": "/images/crews/monster_4.png",
        "personality": "Playful",
    },
    {
        "name": "スパーキー",
        "role": "Designer",
        "level": 7,
        "exp": 490,
        "image_url": "/images/crews/monster_5.png",
        "personality": "Playful",
    },
    {
        "name": "シャドウ",
        "role": "Manager",
        "level": 20,
        "exp": 4000,
        "image_url": "/images/crews/monster_6.png",
        "personality": "Cool",
    },
]


# ============================================================
# ガジェットマスタデータ（スキルタイプ連動）
# effect_type: Intelligence / Creative / Communication / Execution
# ============================================================
INITIAL_GADGETS = [
    {
        "name": "データ分析ツールキット",
        "description": "高度なデータ分析能力を身につけ、洞察力がアップ",
        "icon": "📊",
        "effect_type": "Intelligence",
        "base_effect_value": 10,
        "base_cost": 500,
    },
    {
        "name": "Python専門書",
        "description": "論理的思考力を養い、知性系スキルを強化",
        "icon": "📘",
        "effect_type": "Intelligence",
        "base_effect_value": 12,
        "base_cost": 600,
    },
    {
        "name": "デザインタブレット",
        "description": "クリエイティブな発想を形にする道具",
        "icon": "🎨",
        "effect_type": "Creative",
        "base_effect_value": 15,
        "base_cost": 800,
    },
    {
        "name": "AIアシスタントツール",
        "description": "アイデア発想をサポートし、創造系スキルを強化",
        "icon": "🤖",
        "effect_type": "Creative",
        "base_effect_value": 20,
        "base_cost": 1200,
    },
    {
        "name": "プレゼンリモコン",
        "description": "プレゼンスキルを向上させ、コミュニケーション力アップ",
        "icon": "🎤",
        "effect_type": "Communication",
        "base_effect_value": 12,
        "base_cost": 700,
    },
    {
        "name": "ノイズキャンセリングヘッドホン",
        "description": "雑音をシャットアウトし、交渉に集中できる",
        "icon": "🎧",
        "effect_type": "Communication",
        "base_effect_value": 15,
        "base_cost": 900,
    },
    {
        "name": "高性能ゲーミングマウス",
        "description": "超高速レスポンスで作業効率を大幅にアップ",
        "icon": "🖱️",
        "effect_type": "Execution",
        "base_effect_value": 15,
        "base_cost": 800,
    },
    {
        "name": "メカニカルキーボード",
        "description": "打鍵感抜群で実行系スキルが向上",
        "icon": "⌨️",
        "effect_type": "Execution",
        "base_effect_value": 12,
        "base_cost": 700,
    },
]


# ============================================================
# 特殊性格アイテム（ショップ販売用）- ルビーで購入
# ============================================================
SPECIAL_PERSONALITIES = [
    {
        "personality_key": "Narcissist",
        "name": "ナルシスト",
        "description": "自分に絶対の自信を持つ。「私ほど優秀な人間はいない」が口癖。",
        "emoji": "✨",
        "tone": "ナルシストで自信過剰。自分を褒め、華麗な表現を好む。「この私が」「完璧な」を多用。",
        "ruby_price": 5,
    },
    {
        "personality_key": "King",
        "name": "王様",
        "description": "全てを統べる王の風格。「余は〜」「〜であるぞ」と威厳ある話し方。",
        "emoji": "👑",
        "tone": "王様口調で威厳がある。「余は」「〜であるぞ」「褒めてつかわす」を使う。",
        "ruby_price": 8,
    },
    {
        "personality_key": "Tsundere",
        "name": "ツンデレ",
        "description": "普段はツンツン、でも時々デレる。「べ、別にあんたのためじゃないんだからね！」",
        "emoji": "💢",
        "tone": "ツンデレ。最初は素っ気ないが、褒められると照れる。「べ、別に」「勘違いしないでよね」を使う。",
        "ruby_price": 5,
    },
    {
        "personality_key": "Chuunibyou",
        "name": "中二病",
        "description": "闇の力に目覚めた者。「我が右腕よ、静まれ...」と厨二ワードを連発。",
        "emoji": "🔮",
        "tone": "中二病で厨二ワードを多用。「闘の力が」「我が眼」「封印されし」「覚醒」などを使う。",
        "ruby_price": 5,
    },
    {
        "personality_key": "Ojousama",
        "name": "お嬢様",
        "description": "良家のお嬢様。「〜ですわ」「おほほほ」と上品に話す。",
        "emoji": "🌹",
        "tone": "お嬢様言葉で上品。「〜ですわ」「〜ましてよ」「おほほ」を使う。庶民的なものに興味を示す。",
        "ruby_price": 5,
    },
    {
        "personality_key": "Robot",
        "name": "ロボット",
        "description": "感情を持たない機械。「了解シマシタ」と無機質に話す。",
        "emoji": "🤖",
        "tone": "ロボット口調で無機質。カタカナ交じりで話す。「了解シマシタ」「処理ヲ開始シマス」を使う。",
        "ruby_price": 3,
    },
    {
        "personality_key": "Yankee",
        "name": "ヤンキー",
        "description": "昭和の不良。「あぁ？」「舐めてんじゃねーぞ」と威圧的だが根は優しい。",
        "emoji": "💪",
        "tone": "ヤンキー口調で威圧的だが義理人情に厚い。「あぁ？」「〜じゃねーか」を使うが、仕事は真面目にやる。",
        "ruby_price": 5,
    },
    {
        "personality_key": "Grandpa",
        "name": "おじいちゃん",
        "description": "人生経験豊富なおじいちゃん。「わしの若い頃は〜」と昔話をする。",
        "emoji": "👴",
        "tone": "おじいちゃん口調で穏やか。「わしは」「〜じゃな」「若いもんは」を使い、昔話を交える。",
        "ruby_price": 3,
    },
]


# ============================================================
# Seed関数
# ============================================================
def seed_skills(db: Session) -> None:
    """スキルマスタデータを投入"""
    existing_count = db.query(Skill).count()
    if existing_count > 0:
        return

    for skill_data in INITIAL_SKILLS:
        skill = Skill(**skill_data)
        db.add(skill)

    db.commit()
    print(f"✓ {len(INITIAL_SKILLS)} skills seeded")


def seed_crews(db: Session) -> None:
    """クルーの初期データを投入"""
    existing_count = db.query(Crew).count()
    if existing_count > 0:
        return

    for crew_data in INITIAL_CREWS:
        crew = Crew(**crew_data)
        db.add(crew)

    db.commit()
    print(f"✓ {len(INITIAL_CREWS)} crews seeded")


def seed_gadgets(db: Session) -> None:
    """ガジェットの初期データを投入"""
    existing_count = db.query(Gadget).count()
    if existing_count > 0:
        return

    for gadget_data in INITIAL_GADGETS:
        gadget = Gadget(**gadget_data)
        db.add(gadget)

    db.commit()
    print(f"✓ {len(INITIAL_GADGETS)} gadgets seeded")


def seed_personality_items(db: Session) -> None:
    """特殊性格アイテムのマスタデータを投入"""
    existing_count = db.query(PersonalityItem).count()
    if existing_count > 0:
        return

    for item_data in SPECIAL_PERSONALITIES:
        item = PersonalityItem(**item_data)
        db.add(item)

    db.commit()
    print(f"✓ {len(SPECIAL_PERSONALITIES)} personality items seeded")


def seed_users(db: Session) -> None:
    """認証用ユーザー（test/demo）を投入"""
    for user_data in INITIAL_USERS:
        existing = db.query(User).filter(User.username == user_data["username"]).first()
        if existing:
            continue
        user = User(**user_data)
        db.add(user)

    db.commit()
    print(f"✓ {len(INITIAL_USERS)} auth users seeded")


def seed_all(db: Session) -> None:
    """全ての初期データを投入"""
    seed_users(db)
    seed_skills(db)
    seed_crews(db)
    seed_gadgets(db)
    seed_personality_items(db)

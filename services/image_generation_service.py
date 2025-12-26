"""
Bedrock 画像生成サービス

- 新規作成・スカウト: Nova Canvas (us-east-1)
- 進化: Stability AI SD3.5 Large (us-west-2)

rembg で背景を透過して保存する。
"""

import base64
import json
import logging
import os
import random
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from rembg import remove
from PIL import Image
import io

load_dotenv()

logger = logging.getLogger(__name__)

# AWS設定
AWS_REGION_NOVA = os.getenv("AWS_REGION", "us-east-1")  # Nova Canvas
AWS_REGION_STABILITY = "us-west-2"  # Stability AI SD3.5

NOVA_MODEL_ID = "amazon.nova-canvas-v1:0"
STABILITY_MODEL_ID = "stability.sd3-5-large-v1:0"

# パス設定
BASE_DIR = Path(__file__).parent.parent
ASSETS_DIR = BASE_DIR / "assets" / "base_monsters"
# フロントエンドの public/images/crews に保存
OUTPUT_DIR = BASE_DIR.parent / "frontend" / "public" / "images" / "crews" / "generated"


# ============================================================
# 改良版プロンプト構成 - モンスター×動物ミックス
# ============================================================

# ベーススタイル（元のモンスタースタイルを復活）
BASE_STYLE = "A cute 3D rendered toy figure of a fantasy monster creature, smooth matte plastic texture, rounded friendly form, solid chunky body"

# 役割（Role）→ 見た目・姿勢
ROLE_VISUAL_MAPPING = {
    "Sales": "confident stance, bright energetic eyes",
    "Marketer": "creative pose, inspired expression",
    "Engineer": "tilted head, focused gaze",
    "Designer": "elegant stance, artistic gaze",
    "Admin": "reliable stance, attentive expression",
    "Manager": "confident posture, commanding presence",
}

# 性格（Personality）→ 表情・雰囲気
PERSONALITY_VISUAL_MAPPING = {
    "Hot-blooded": "fiery expression, bold pose",
    "Cool": "calm expression, serene demeanor",
    "Gentle": "soft smile, kind eyes",
    "Serious": "determined look, sharp eyes",
    "Playful": "bright smile, mischievous eyes",
    "Cautious": "careful expression, thoughtful gaze",
}

# モンスター種族（架空の生物 - 動物名を避ける）
CREATURE_TYPES = [
    "blob monster with tiny horns",
    "round slime creature with antenna",
    "fluffy spirit with small wings",
    "pudgy imp with pointed ears",
    "chubby goblin with big nose",
    "round ghost with stubby arms",
    "squishy elemental with glowing marks",
    "chunky golem with crystal eyes",
]

# 体型バリエーション
BODY_VARIATIONS = [
    "round chubby body",
    "oval shaped body",
    "pear shaped body",
    "blob shaped body",
]

# 特徴バリエーション（モンスターらしい奇抜さ）
FEATURE_VARIATIONS = [
    "single eye, tiny horns",
    "three eyes, fin on head",
    "big floppy ears, no nose",
    "multiple small horns, wide mouth",
    "antenna on head, stubby tail",
    "crystal growth on back, glowing eyes",
    "floating orbs around, spiral marks",
    "wing-like fins, spotted pattern",
]

# カラーバリエーション
COLOR_VARIATIONS = [
    "teal and orange gradient",
    "blue and gold accent",
    "purple and silver tones",
    "coral and cream colors",
    "mint and pink blend",
    "navy and gold highlights",
    "grey and orange accent",
    "lavender and white tones",
]

# 装飾品バリエーション（必ず1つ付ける - 頭周りのみで空洞を避ける）
ACCESSORY_VARIATIONS = [
    "wearing small round glasses",
    "wearing a tiny crown on head",
    "wearing a cute headband",
    "wearing a small bow tie",
    "wearing a tiny scarf",
    "wearing a mini cape",
    "holding a small wand",
    "wearing a tiny hat",
]

# レアリティ別強化
RARITY_ENHANCEMENTS = {
    1: ["simple matte finish"],
    2: ["subtle glow", "polished finish"],
    3: ["soft ambient glow", "premium finish"],
    4: ["golden accent glow", "luxury finish"],
    5: ["ethereal glow", "light particles", "legendary aura"],
}

# 固定キーワード
FIXED_KEYWORDS = [
    "high quality 3D render",
    "soft studio lighting",
    "clean white background",
]

# ネガティブプロンプト（リアル動物と空洞を防止）
NEGATIVE_PROMPT = "realistic animal, real cat, real dog, real fox, hollow body, empty torso, holes, transparent, watercolor, painting, 2D, flat, sketchy, anime, NFT style, scary, horror, realistic human, low quality, blurry"


def get_bedrock_client(region: str = AWS_REGION_NOVA):
    """Bedrock Runtime クライアントを取得（画像生成用）"""
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def get_stability_client():
    """Stability AI 用の Bedrock クライアントを取得"""
    return get_bedrock_client(region=AWS_REGION_STABILITY)


def get_random_base_image() -> Path:
    """ベース画像からランダムに1枚選択"""
    if not ASSETS_DIR.exists():
        raise FileNotFoundError(f"Base monsters directory not found: {ASSETS_DIR}")

    images = list(ASSETS_DIR.glob("*.png"))
    if not images:
        raise FileNotFoundError(f"No PNG images found in {ASSETS_DIR}")

    return random.choice(images)


def generate_variation_prompt(
    role: str = "Engineer",
    personality: str = "Serious",
    rarity: int = 1
) -> tuple[str, str]:
    """
    モンスター×動物ミックスのプロンプトを生成

    Args:
        role: クルーの役割 (Sales, Marketer, Engineer, Designer, Admin, Manager)
        personality: クルーの性格 (Hot-blooded, Cool, Gentle, Serious, Playful, Cautious)
        rarity: レアリティ（1-5）

    Returns:
        tuple: (positive_prompt, negative_prompt)
    """
    # 役割に応じた見た目
    role_visual = ROLE_VISUAL_MAPPING.get(role, ROLE_VISUAL_MAPPING["Engineer"])

    # 性格に応じた表情
    personality_visual = PERSONALITY_VISUAL_MAPPING.get(personality, PERSONALITY_VISUAL_MAPPING["Serious"])

    # モンスター種族（架空の生物）
    creature = random.choice(CREATURE_TYPES)

    # 体型
    body = random.choice(BODY_VARIATIONS)

    # 奇抜な特徴
    feature = random.choice(FEATURE_VARIATIONS)

    # 装飾品（必ず1つ付ける）
    accessory = random.choice(ACCESSORY_VARIATIONS)

    # カラー
    color = random.choice(COLOR_VARIATIONS)

    # レアリティ
    rarity_keywords = RARITY_ENHANCEMENTS.get(rarity, RARITY_ENHANCEMENTS[1])
    rarity_text = ", ".join(rarity_keywords)

    # プロンプト組み立て
    prompt_parts = [
        BASE_STYLE,
        creature,
        body,
        feature,
        accessory,  # 装飾品を追加
        role_visual,
        personality_visual,
        color,
        rarity_text,
        *FIXED_KEYWORDS,
    ]

    positive_prompt = ", ".join(prompt_parts)

    return positive_prompt, NEGATIVE_PROMPT


def image_to_base64(image_path: Path) -> str:
    """画像をBase64エンコード（透過画像は白背景に変換）"""
    img = Image.open(image_path)

    # 透過画像（RGBA）の場合、白背景を追加
    if img.mode == 'RGBA':
        # 白背景を作成
        background = Image.new('RGB', img.size, (255, 255, 255))
        # アルファチャンネルを使って合成
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # JPEG形式でBase64エンコード（Nova Canvasが透過を受け付けないため）
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def base64_to_image(base64_string: str) -> Image.Image:
    """Base64から PIL Image に変換"""
    image_data = base64.b64decode(base64_string)
    return Image.open(io.BytesIO(image_data))


def remove_background(image: Image.Image) -> Image.Image:
    """rembg を使って背景を透過"""
    # PIL Image を bytes に変換
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()

    # rembg で背景除去
    output_bytes = remove(img_bytes)

    # bytes を PIL Image に戻す
    return Image.open(io.BytesIO(output_bytes))


async def generate_crew_image(
    crew_name: str,
    role: str = "Engineer",
    personality: str = "Serious",
    rarity: int = 1
) -> tuple[str, str | None]:
    """
    クルー用の画像を生成する

    1. ベース画像をランダムに選択
    2. Nova Canvas で Image-to-Image 変換
    3. rembg で背景透過
    4. Base64データを返す（本番環境対応）

    Args:
        crew_name: クルーの名前（ログ用）
        role: クルーの役割 (Sales, Marketer, Engineer, Designer, Admin, Manager)
        personality: クルーの性格 (Hot-blooded, Cool, Gentle, Serious, Playful, Cautious)
        rarity: レアリティ（1-5）。高いほど豪華な画像を生成

    Returns:
        tuple: (image_url, image_base64)
            - image_url: フォールバック用のデフォルト画像パス
            - image_base64: 生成された画像のBase64データ（data:image/png;base64,... 形式）
    """
    try:
        # ベース画像を選択
        base_image_path = get_random_base_image()
        logger.info(f"Selected base image: {base_image_path.name}")

        # Base64エンコード
        base_image_b64 = image_to_base64(base_image_path)

        # バリエーションプロンプトを生成（役割・性格・レアリティを反映）
        positive_prompt, negative_prompt = generate_variation_prompt(role, personality, rarity)
        logger.info(f"Generated prompt for {crew_name} (role={role}, personality={personality}, rarity={rarity})")
        logger.info(f"Prompt: {positive_prompt[:200]}...")
        logger.info(f"Negative prompt: {negative_prompt}")

        # Bedrock クライアント
        client = get_bedrock_client()

        # Nova Canvas Image-to-Image リクエスト
        request_body = {
            "taskType": "IMAGE_VARIATION",
            "imageVariationParams": {
                "images": [base_image_b64],
                "text": positive_prompt,
                "negativeText": negative_prompt,
                "similarityStrength": 0.45,  # さらに下げて形状の多様性を許容
            },
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "width": 512,
                "height": 512,
                "cfgScale": 8.5,  # 少し下げて自然な仕上がりに
            }
        }

        logger.info(f"Calling Nova Canvas for crew: {crew_name}")

        response = client.invoke_model(
            modelId=NOVA_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())

        # 生成された画像を取得
        if "images" not in response_body or len(response_body["images"]) == 0:
            raise ValueError("No images generated by Nova Canvas")

        generated_image_b64 = response_body["images"][0]
        generated_image = base64_to_image(generated_image_b64)

        logger.info(f"Image generated, removing background...")

        # 背景を透過
        transparent_image = remove_background(generated_image)

        # PNG形式でBase64エンコード
        img_byte_arr = io.BytesIO()
        transparent_image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        final_base64 = base64.b64encode(img_byte_arr.getvalue()).decode("utf-8")

        # data URI形式で返す
        image_data_uri = f"data:image/png;base64,{final_base64}"

        logger.info(f"Generated image for {crew_name} (Base64 length: {len(final_base64)})")

        # フォールバック用のデフォルトパスも返す
        default_path = f"/images/crews/monster_{random.randint(1, 6)}.png"
        return default_path, image_data_uri

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        logger.error(f"Bedrock API error ({error_code}): {e}")
        raise

    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        raise


async def generate_crew_image_with_fallback(
    crew_name: str,
    role: str = "Engineer",
    personality: str = "Serious",
    rarity: int = 1
) -> tuple[str, str | None]:
    """
    画像生成を試み、失敗時はデフォルト画像を返す

    Args:
        crew_name: クルーの名前
        role: クルーの役割
        personality: クルーの性格
        rarity: レアリティ（1-5）

    Returns:
        tuple: (image_url, image_base64)
            - image_url: 画像パス（フォールバック用）
            - image_base64: 生成された画像のBase64データ（失敗時はNone）
    """
    try:
        return await generate_crew_image(crew_name, role, personality, rarity)
    except Exception as e:
        logger.warning(f"Image generation failed for {crew_name}, using default: {e}")
        # デフォルト画像をランダムに選択
        default_images = [
            "/images/crews/monster_1.png",
            "/images/crews/monster_2.png",
            "/images/crews/monster_3.png",
            "/images/crews/monster_4.png",
            "/images/crews/monster_5.png",
            "/images/crews/monster_6.png",
        ]
        return random.choice(default_images), None


# 進化用プロンプト（Stability AI SD3.5 Large用）
EVOLUTION_PROMPT = "A cute 3D rendered toy figure of an AI agent character wearing a luxurious golden business suit, glowing golden aura around body, evolved powerful majestic form, keeping same color scheme, premium collectible figure, professional executive presence, studio lighting, clean white background"

# 進化時の変化度合い（0.0-1.0、高いほど変化が大きい）
EVOLUTION_STRENGTH = 0.5


def load_existing_image(image_path: str) -> str:
    """
    既存のクルー画像をBase64エンコードして読み込む

    Args:
        image_path: 画像の相対パス（/images/crews/generated/xxx.png）

    Returns:
        str: Base64エンコードされた画像データ
    """
    # フロントエンドのpublicディレクトリからの相対パス
    if image_path.startswith("/"):
        image_path = image_path[1:]  # 先頭の "/" を除去

    full_path = BASE_DIR.parent / "frontend" / "public" / image_path

    if not full_path.exists():
        raise FileNotFoundError(f"Image not found: {full_path}")

    return image_to_base64(full_path)


async def evolve_crew_image(current_image_path: str, crew_name: str) -> str:
    """
    クルーを進化させた画像を生成する（Stability AI SD3.5 Large）

    1. 現在のクルー画像を読み込み
    2. SD3.5 Large で進化後の姿に変換（Image-to-Image）
    3. rembg で背景透過
    4. 保存してパスを返す

    Args:
        current_image_path: 現在の画像パス（/images/crews/generated/xxx.png）
        crew_name: クルーの名前

    Returns:
        str: 生成された画像の相対パス
    """
    try:
        # 出力ディレクトリを作成
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 現在の画像を読み込み
        logger.info(f"Loading current image: {current_image_path}")
        base_image_b64 = load_existing_image(current_image_path)

        logger.info(f"Evolution prompt: {EVOLUTION_PROMPT[:80]}...")
        logger.info(f"Evolution strength: {EVOLUTION_STRENGTH}")

        # Stability AI 用クライアント（us-west-2）
        client = get_stability_client()

        # SD3.5 Large Image-to-Image リクエスト
        request_body = {
            "prompt": EVOLUTION_PROMPT,
            "mode": "image-to-image",
            "image": base_image_b64,
            "strength": EVOLUTION_STRENGTH,  # 0.5 で適度な変化
            "output_format": "png",
        }

        logger.info(f"Calling Stability AI SD3.5 for evolution: {crew_name}")

        response = client.invoke_model(
            modelId=STABILITY_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())

        # 生成された画像を取得
        if "images" not in response_body or len(response_body["images"]) == 0:
            raise ValueError("No images generated by Stability AI")

        generated_image_b64 = response_body["images"][0]
        generated_image = base64_to_image(generated_image_b64)

        logger.info(f"Evolution image generated ({generated_image.size}), removing background...")

        # 背景を透過
        transparent_image = remove_background(generated_image)

        # ファイルを保存（進化版は "evolved_" プレフィックスを付ける）
        file_name = f"evolved_{uuid.uuid4()}.png"
        output_path = OUTPUT_DIR / file_name
        transparent_image.save(output_path, "PNG")

        logger.info(f"Saved evolved image: {output_path}")

        # フロントエンドから参照できる相対パス
        relative_path = f"/images/crews/generated/{file_name}"
        return relative_path

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        logger.error(f"Stability AI API error ({error_code}): {e}")
        raise

    except Exception as e:
        logger.error(f"Evolution image generation failed: {e}")
        raise

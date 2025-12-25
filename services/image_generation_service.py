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

# ランダムな色変更プロンプト
COLOR_VARIATIONS = [
    "blue color scheme",
    "red color scheme",
    "green color scheme",
    "purple color scheme",
    "golden color scheme",
    "silver metallic color",
    "pink and white colors",
    "dark shadow colors",
    "rainbow gradient colors",
    "ice blue frozen colors",
    "fire orange and red colors",
    "forest green nature colors",
]

# ランダムなアクセサリー・装飾プロンプト
ACCESSORY_VARIATIONS = [
    "wearing a tiny crown",
    "with sparkles and stars around",
    "wearing a wizard hat",
    "with angel wings",
    "wearing sunglasses",
    "with a magical aura glow",
    "wearing a bow tie",
    "with lightning effects",
    "wearing a flower crown",
    "with crystal decorations",
    "wearing a cape",
    "with flame effects",
]

# 3Dトイフィギュア風スタイル（ブロスタ/あつ森風）
BASE_STYLE = "A cute 3D rendered toy figure of a monster, smooth plastic texture, soft lighting, rounded edges, isometric view"

# ネガティブプロンプト（水彩画風を排除）
NEGATIVE_PROMPT = "watercolor, painting, 2D, flat, brush strokes, sketchy, hand-drawn, illustration, anime, cartoon"


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


# レアリティ別の豪華キーワード（3Dフィギュア風に対応）
RARITY_ENHANCEMENTS = {
    1: [],  # ★1: 通常
    2: ["subtle glossy finish", "slight metallic sheen"],  # ★2: 少し光沢
    3: ["glowing LED eyes", "shiny metallic accents", "premium plastic finish"],  # ★3: プレミアム感
    4: ["golden chrome parts", "luxury collectible figure", "holographic shimmer effect"],  # ★4: コレクタブル
    5: ["diamond encrusted details", "golden throne base", "divine light rays", "legendary ultra rare collectible"],  # ★5: 伝説級
}


def generate_variation_prompt(rarity: int = 1) -> tuple[str, str]:
    """
    ランダムなバリエーションプロンプトを生成（3Dトイフィギュア風）

    Args:
        rarity: レアリティ（1-5）。高いほど豪華なキーワードを追加

    Returns:
        tuple: (positive_prompt, negative_prompt)
    """
    color = random.choice(COLOR_VARIATIONS)
    accessory = random.choice(ACCESSORY_VARIATIONS)

    # レアリティに応じた豪華キーワードを追加
    rarity_keywords = RARITY_ENHANCEMENTS.get(rarity, [])
    rarity_text = ", ".join(rarity_keywords) if rarity_keywords else ""

    # 3Dトイフィギュア風のベーススタイルを使用
    prompt_parts = [
        BASE_STYLE,
        color,
        accessory,
        "high quality render",
        "studio lighting",
        "clean white background",
        "collectible toy aesthetic",
    ]

    if rarity_text:
        prompt_parts.append(rarity_text)

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


async def generate_crew_image(crew_name: str, rarity: int = 1) -> tuple[str, str | None]:
    """
    クルー用の画像を生成する

    1. ベース画像をランダムに選択
    2. Nova Canvas で Image-to-Image 変換
    3. rembg で背景透過
    4. Base64データを返す（本番環境対応）

    Args:
        crew_name: クルーの名前（ログ用）
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

        # バリエーションプロンプトを生成（レアリティを考慮、3Dフィギュア風）
        positive_prompt, negative_prompt = generate_variation_prompt(rarity)
        logger.info(f"Generated prompt (rarity={rarity}): {positive_prompt}")
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
                "similarityStrength": 0.6,
            },
            "imageGenerationConfig": {
                "numberOfImages": 1,
                "width": 512,
                "height": 512,
                "cfgScale": 9.0,
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


async def generate_crew_image_with_fallback(crew_name: str, rarity: int = 1) -> tuple[str, str | None]:
    """
    画像生成を試み、失敗時はデフォルト画像を返す

    Args:
        crew_name: クルーの名前
        rarity: レアリティ（1-5）

    Returns:
        tuple: (image_url, image_base64)
            - image_url: 画像パス（フォールバック用）
            - image_base64: 生成された画像のBase64データ（失敗時はNone）
    """
    try:
        return await generate_crew_image(crew_name, rarity)
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
EVOLUTION_PROMPT = "A cute 3D rendered toy figure of a monster wearing a luxurious golden business suit, glowing golden aura around body, evolved powerful majestic form, keeping same color scheme, premium collectible figure, studio lighting, clean white background"

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

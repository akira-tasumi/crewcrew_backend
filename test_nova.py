"""Nova Canvas のテストスクリプト"""
import asyncio
import json
import os
import base64
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv()

# AWS設定
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
NOVA_MODEL_ID = "amazon.nova-canvas-v1:0"

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets" / "base_monsters"


def get_bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def test_nova():
    print(f"AWS_REGION: {AWS_REGION}")
    print(f"Model ID: {NOVA_MODEL_ID}")

    # ベース画像を取得
    images = list(ASSETS_DIR.glob("*.png"))
    if not images:
        print(f"ERROR: No images in {ASSETS_DIR}")
        return

    base_image = images[0]
    print(f"Base image: {base_image}")

    # Base64エンコード（透過画像を白背景に変換）
    from PIL import Image
    import io

    img = Image.open(base_image)
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=95)
    base_image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    print(f"Base64 length: {len(base_image_b64)}")

    client = get_bedrock_client()

    prompt = "A cute monster character, blue color scheme, wearing a tiny crown, cute kawaii style, high quality, detailed, game character"
    print(f"Prompt: {prompt}")

    # Nova Canvas Image-to-Image リクエスト
    request_body = {
        "taskType": "IMAGE_VARIATION",
        "imageVariationParams": {
            "images": [base_image_b64],
            "text": prompt,
            "similarityStrength": 0.7,
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "width": 512,
            "height": 512,
            "cfgScale": 8.0,
        }
    }

    print("Calling Nova Canvas...")

    try:
        response = client.invoke_model(
            modelId=NOVA_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        print(f"Response keys: {response_body.keys()}")

        if "images" in response_body:
            print(f"Generated {len(response_body['images'])} image(s)")
            print(f"Image base64 length: {len(response_body['images'][0])}")
            print("SUCCESS!")
        else:
            print(f"No images in response: {response_body}")

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    test_nova()

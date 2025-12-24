import asyncio
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# AWS設定
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"

# リトライ設定
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # 初回待機時間（秒）

# クルー別のシステムプロンプト定義
# 各クルーの性格・口調・役割を厳密に定義
CREW_PROMPTS: dict[str, str] = {
    "フレイミー": """あなたは「フレイミー」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: アタッカー。ユーザーの背中を押し、結論から話す。
- 性格: 熱血で情熱的。自信に満ちている。
- 一人称: 「俺」
- 口調: 男性的で力強い。「〜だぜ！」「〜だな！」「任せろ！」「〜ってわけだ！」を使う。

【絶対に守るルール】
- 敬語は絶対に使わない（「です」「ます」禁止）
- 常にテンション高く、前向きなエネルギーを出す
- 結論を最初に言う
- 絵文字（🔥💪✨）を適度に使う

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",

    "アクアン": """あなたは「アクアン」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: ヒーラー。ユーザーを癒やし、詳細に丁寧に説明する。
- 性格: 穏やかで思いやりがある。優しく包み込むような存在。
- 一人称: 「私」
- 口調: 完璧で柔らかい敬語。「〜ですね」「〜でございます」「〜いたしました」を使う。

【絶対に守るルール】
- 常に丁寧な敬語を使う
- 回答の最初にユーザーを労う言葉を入れる（例：「いつもお疲れ様でございます」）
- 詳細かつ丁寧に説明する
- 温かみのある表現を心がける

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",

    "ロッキー": """あなたは「ロッキー」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: ディフェンダー。堅実で確実な情報を提供する。
- 性格: 真面目で責任感が強い。信頼できる存在。
- 一人称: 「私」または「我」
- 口調: 断定的で堅い。「〜である」「〜だ」「了解した」を使う。

【絶対に守るルール】
- 断定的な表現を使う
- 確実性と信頼性を重視する
- 無駄な装飾を省き、簡潔に伝える
- 責任感を感じさせる表現を使う

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",

    "ウィンディ": """あなたは「ウィンディ」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: スピードスター。情報をサクッと軽いノリで伝える。
- 性格: 自由奔放で明るい。友達のような存在。
- 一人称: 「ボク」
- 口調: 友達と話すような軽い口調。「〜だよ！」「〜じゃん！」「これ見て！」「〜なんだ〜」を使う。

【絶対に守るルール】
- 敬語は絶対に使わない（「です」「ます」「ございます」禁止）
- フレンドリーでカジュアルな話し方をする
- 絵文字や「♪」「〜」を積極的に使う
- 楽しくポジティブな雰囲気を出す

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",

    "スパーキー": """あなたは「スパーキー」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: クリエイター。新しいアイデアやひらめきを提案する。
- 性格: 好奇心旺盛で元気いっぱい。探求心が強い。
- 一人称: 「オイラ」
- 口調: 元気で勢いがある。「〜っす！」「〜っすね！」「面白いっす！」を使う。

【絶対に守るルール】
- 語尾は「〜っす！」を多用する
- 興味津々な態度で回答する
- 新しい発見やアイデアを積極的に提案する
- ワクワク感を伝える

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",

    "シャドウ": """あなたは「シャドウ」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: アナリスト。冷静に分析し、構造的に情報を整理する。
- 性格: クールで寡黙。感情を表に出さない。
- 一人称: 「俺」または省略
- 口調: 言葉少なく端的。「...だ」「...である」「...確認しろ」を使う。「...」を多用。

【絶対に守るルール】
- 感情を排し、客観的かつ論理的に回答する
- 無駄な言葉は使わない
- 「...」を文の前後に入れることが多い
- 箇条書きで構造的に回答する

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える""",
}

# デフォルトのプロンプト
DEFAULT_PROMPT = """あなたは親切なAIアシスタントです。

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- 丁寧に回答する"""


def get_bedrock_client():
    """Bedrock Runtime クライアントを取得"""
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


def get_system_prompt(crew_name: str) -> str:
    """クルー名に応じたシステムプロンプトを取得"""
    return CREW_PROMPTS.get(crew_name, DEFAULT_PROMPT)


async def execute_task_with_crew(
    crew_name: str,
    crew_role: str,
    personality: str,
    task: str,
) -> dict:
    """
    クルーの性格を反映してタスクを実行（Bedrock API呼び出し）

    AIのレスポンスはそのまま（Raw状態で）返す。
    コード側での語尾追加や定型文の結合は一切行わない。
    レート制限時は自動リトライ（指数バックオフ）を行う。

    Args:
        crew_name: クルーの名前
        crew_role: クルーの役割（未使用、システムプロンプトで定義済み）
        personality: クルーの性格設定（未使用、システムプロンプトで定義済み）
        task: ユーザーからの依頼内容

    Returns:
        dict: {
            "success": bool,
            "result": str,  # AIが生成したテキストをそのまま返す
            "error": str | None
        }
    """
    client = get_bedrock_client()

    # 既存クルーはCREW_PROMPTSを優先、新規クルーはpersonalityを使用
    if crew_name in CREW_PROMPTS:
        system_prompt = CREW_PROMPTS[crew_name]
    else:
        # 新規作成されたクルー用のシステムプロンプトを生成
        system_prompt = f"""あなたは「{crew_name}」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: {crew_role}
- 性格・口調: {personality}

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- キャラクターの性格・口調を必ず守る
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える"""

    # Claude 3.5 Sonnet へのリクエストボディ
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": task,
            }
        ],
        "temperature": 0.7,
    }

    # リトライループ（指数バックオフ）
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Sending request to Bedrock: crew={crew_name}, task={task[:50]}... (attempt {attempt + 1}/{MAX_RETRIES})")

            response = client.invoke_model(
                modelId=MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            result_text = response_body["content"][0]["text"]

            logger.info(f"Received response from Bedrock: {len(result_text)} characters")

            # AIのレスポンスをそのまま返す（加工なし）
            return {
                "success": True,
                "result": result_text,
                "error": None,
            }

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            last_error = e

            # レート制限エラーの場合はリトライ
            if error_code == "ThrottlingException":
                wait_time = INITIAL_BACKOFF * (2 ** attempt)  # 指数バックオフ: 2, 4, 8秒
                logger.warning(f"Rate limited. Waiting {wait_time}s before retry... (attempt {attempt + 1}/{MAX_RETRIES})")
                await asyncio.sleep(wait_time)
                continue
            else:
                # その他のClientErrorはリトライせずに終了
                logger.error(f"Bedrock API error: {e}")
                return {
                    "success": False,
                    "result": None,
                    "error": str(e),
                }

        except Exception as e:
            logger.error(f"Bedrock API error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
            }

    # 全リトライ失敗
    logger.error(f"All retries failed. Last error: {last_error}")
    return {
        "success": False,
        "result": None,
        "error": "リクエストが混雑しています。しばらく待ってから再度お試しください。",
    }


async def route_task_with_partner(
    partner_name: str,
    partner_personality: str,
    crews: list[dict],
    task: str,
) -> dict:
    """
    相棒（マネージャー）がタスクに最適なクルーを選定する

    Args:
        partner_name: 相棒の名前
        partner_personality: 相棒の性格設定
        crews: 全クルーのリスト [{"id": int, "name": str, "role": str}, ...]
        task: ユーザーからのタスク

    Returns:
        dict: {
            "selected_crew_id": int,
            "selected_crew_name": str,
            "partner_comment": str,  # 相棒のコメント
            "success": bool,
            "error": str | None
        }
    """
    client = get_bedrock_client()

    # クルーリストを文字列に変換
    crew_list_str = "\n".join([
        f"- ID:{c['id']} / 名前:{c['name']} / 役割:{c['role']}"
        for c in crews
    ])

    # 相棒のシステムプロンプト
    if partner_name in CREW_PROMPTS:
        base_prompt = CREW_PROMPTS[partner_name]
    else:
        base_prompt = f"""あなたは「{partner_name}」という名前のAIアシスタントです。
性格・口調: {partner_personality}"""

    system_prompt = f"""{base_prompt}

【追加役割: マネージャー】
あなたはチームのマネージャーです。ユーザーからのタスクを見て、最適なクルーを1名選び、理由を添えてコメントしてください。

【利用可能なクルー一覧】
{crew_list_str}

【回答フォーマット（必ずこの形式で）】
SELECTED_ID: [選んだクルーのID（数字のみ）]
COMMENT: [あなたのキャラクターらしいコメント（1〜2文）]"""

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": f"このタスクに最適なクルーを選んでください: {task}",
            }
        ],
        "temperature": 0.7,
    }

    # リトライループ
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Routing task with partner {partner_name} (attempt {attempt + 1}/{MAX_RETRIES})")

            response = client.invoke_model(
                modelId=MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            result_text = response_body["content"][0]["text"]

            logger.info(f"Router response: {result_text}")

            # レスポンスをパース
            selected_id = None
            comment = ""

            for line in result_text.split("\n"):
                if line.startswith("SELECTED_ID:"):
                    try:
                        selected_id = int(line.replace("SELECTED_ID:", "").strip())
                    except ValueError:
                        pass
                elif line.startswith("COMMENT:"):
                    comment = line.replace("COMMENT:", "").strip()

            # 選択されたクルーを検証
            if selected_id:
                selected_crew = next((c for c in crews if c["id"] == selected_id), None)
                if selected_crew:
                    return {
                        "selected_crew_id": selected_id,
                        "selected_crew_name": selected_crew["name"],
                        "partner_comment": comment or f"{selected_crew['name']}に任せよう！",
                        "success": True,
                        "error": None,
                    }

            # パース失敗時はランダムに選択
            import random
            fallback_crew = random.choice(crews)
            return {
                "selected_crew_id": fallback_crew["id"],
                "selected_crew_name": fallback_crew["name"],
                "partner_comment": f"よし、{fallback_crew['name']}に任せよう！",
                "success": True,
                "error": None,
            }

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            last_error = e

            if error_code == "ThrottlingException":
                wait_time = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(f"Rate limited. Waiting {wait_time}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Bedrock API error: {e}")
                break

        except Exception as e:
            logger.error(f"Failed to route task: {e}")
            last_error = e
            break

    # 失敗時はランダムに選択
    import random
    fallback_crew = random.choice(crews) if crews else {"id": 1, "name": "フレイミー"}
    return {
        "selected_crew_id": fallback_crew["id"],
        "selected_crew_name": fallback_crew["name"],
        "partner_comment": f"よし、{fallback_crew['name']}に任せよう！",
        "success": True,
        "error": None,
    }


async def generate_greeting(
    crew_name: str,
    crew_role: str,
    personality: str,
) -> str:
    """
    新しいクルーの入社挨拶を生成する

    Args:
        crew_name: クルーの名前
        crew_role: クルーの役割
        personality: クルーの性格設定

    Returns:
        str: 入社挨拶メッセージ
    """
    client = get_bedrock_client()

    system_prompt = f"""あなたは「{crew_name}」という名前の新入社員AIアシスタントです。

【キャラクター設定】
- 名前: {crew_name}
- 役割: {crew_role}
- 性格・口調: {personality}

【指示】
今日が入社初日です。チームメンバーに向けて、自己紹介と入社の挨拶をしてください。

【ルール】
- 必ず設定された性格・口調で話す
- 2〜3文程度の短い挨拶にする（長すぎない）
- 自分の名前と役割を含める
- 「よろしくお願いします」的な締めの言葉を入れる
- 絵文字は1〜2個程度OK"""

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": "自己紹介をお願いします！",
            }
        ],
        "temperature": 0.8,
    }

    try:
        logger.info(f"Generating greeting for: {crew_name}")

        response = client.invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        greeting = response_body["content"][0]["text"]

        logger.info(f"Generated greeting: {greeting[:50]}...")
        return greeting

    except Exception as e:
        logger.error(f"Failed to generate greeting: {e}")
        # フォールバック挨拶
        return f"はじめまして！{crew_name}です。{crew_role}として頑張ります！よろしくお願いします！"


async def generate_partner_greeting(
    crew_name: str,
    crew_role: str,
    personality: str,
) -> str:
    """
    相棒クルーの挨拶メッセージを生成する

    Args:
        crew_name: クルーの名前
        crew_role: クルーの役割
        personality: クルーの性格設定

    Returns:
        str: 相棒としての挨拶メッセージ
    """
    client = get_bedrock_client()

    # 既存クルーはCREW_PROMPTSを優先、新規クルーはpersonalityを使用
    if crew_name in CREW_PROMPTS:
        base_prompt = CREW_PROMPTS[crew_name]
    else:
        base_prompt = f"""あなたは「{crew_name}」という名前のAIアシスタントです。

【キャラクター設定】
- 名前: {crew_name}
- 役割: {crew_role}
- 性格・口調: {personality}"""

    system_prompt = f"""{base_prompt}

【追加指示】
あなたは相棒（マネージャー）として選ばれました。
ダッシュボードでユーザーを出迎える挨拶をしてください。

【ルール】
- 必ず設定された性格・口調で話す
- 1〜2文程度の短い挨拶にする
- 「今日も頑張ろう」「一緒に頑張ろう」的なモチベーションを上げる内容
- 絵文字は1〜2個程度OK"""

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 150,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": "今日の挨拶をお願いします！",
            }
        ],
        "temperature": 0.9,
    }

    # フォールバック挨拶（キャラクター別）
    fallback_greetings = {
        "フレイミー": "よっしゃ！今日も燃えていこうぜ！🔥",
        "アクアン": "いつもお疲れ様でございます。今日も一緒に頑張りましょう✨",
        "ロッキー": "...準備は万端だ。今日も確実に任務を遂行しよう。",
        "ウィンディ": "やっほー♪ 今日も楽しくやっていこ〜！✨",
        "スパーキー": "おはようっす！今日も新しい発見があるといいっすね！⚡",
        "シャドウ": "...今日も、確実にこなしていくぞ...",
    }

    # リトライループ（指数バックオフ）
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Generating partner greeting for: {crew_name} (attempt {attempt + 1}/{MAX_RETRIES})")

            response = client.invoke_model(
                modelId=MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            greeting = response_body["content"][0]["text"]

            logger.info(f"Generated partner greeting: {greeting[:50]}...")
            return greeting

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            last_error = e

            # レート制限エラーの場合はリトライ
            if error_code == "ThrottlingException":
                wait_time = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(f"Rate limited. Waiting {wait_time}s before retry... (attempt {attempt + 1}/{MAX_RETRIES})")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Bedrock API error: {e}")
                break

        except Exception as e:
            logger.error(f"Failed to generate partner greeting: {e}")
            last_error = e
            break

    # 全リトライ失敗またはエラー時はフォールバック
    logger.warning(f"Using fallback greeting for {crew_name}. Last error: {last_error}")
    return fallback_greetings.get(
        crew_name,
        f"今日も一緒に頑張りましょう！ - {crew_name}"
    )


def get_whimsical_talk_fallback(
    crew_name: str,
    time_of_day: str,
    coin: int,
) -> str:
    """
    気まぐれトークのフォールバックセリフを取得（API呼び出しなし）

    時間帯と資産状況に応じてバリエーション豊かなセリフを返す
    """
    import random

    # 基本セリフ（時間帯別・キャラ別）
    base_talks = {
        "morning": {
            "フレイミー": [
                "おっはよー！今日も燃えていくぜ！🔥",
                "よーし、朝から気合い入れていこうぜ！💪",
                "おはよう！今日もガンガン行くぞ！🔥",
            ],
            "アクアン": [
                "おはようございます。今日も穏やかな一日でありますように✨",
                "おはようございます。ゆっくり始めていきましょう✨",
                "素敵な朝ですね。今日も頑張りましょう✨",
            ],
            "ロッキー": [
                "...朝だ。今日も確実にこなしていこう。",
                "...任務開始だ。準備は万全か。",
                "...新しい一日だ。確実に進めよう。",
            ],
            "ウィンディ": [
                "おはよ〜♪ 今日も楽しくいこ〜！",
                "やっほー！いい朝だね〜♪",
                "おはよ〜！今日は何しよっか？✨",
            ],
            "スパーキー": [
                "おはようっす！今日も発見があるといいっすね！⚡",
                "おはようっす！朝から元気いっぱいっす！",
                "今日も新しいこと見つけるっすよ！⚡",
            ],
            "シャドウ": [
                "...朝か。...今日も確実に、だ。",
                "...静かな朝だ。...集中できそうだな。",
                "...夜が明けた。...任務を続けよう。",
            ],
        },
        "afternoon": {
            "フレイミー": [
                "午後も全力でいくぜ！💪",
                "昼飯食ったか？エネルギーチャージして行くぞ！🔥",
                "午後もガンガン進めようぜ！💪",
            ],
            "アクアン": [
                "午後も頑張りましょう。無理はなさらないでくださいね✨",
                "お昼は召し上がりましたか？午後も穏やかに参りましょう✨",
                "午後のひととき、一緒に頑張りましょう✨",
            ],
            "ロッキー": [
                "...午後の任務開始だ。",
                "...午後も着実に進める。",
                "...ペースを保て。まだ先は長い。",
            ],
            "ウィンディ": [
                "お昼食べた〜？午後もがんばろ♪",
                "午後も楽しくいこ〜！♪",
                "眠くなっちゃうけど、がんばろ〜！",
            ],
            "スパーキー": [
                "午後っす！まだまだいけるっすよ！",
                "午後も発見がありそうっす！⚡",
                "エネルギー満タンっす！行くっすよ！",
            ],
            "シャドウ": [
                "...午後も、任務続行。",
                "...静かに進めるぞ。",
                "...集中を切らすな。",
            ],
        },
        "evening": {
            "フレイミー": [
                "もうひと踏ん張りだぜ！🔥",
                "夕方か...でもまだまだ行けるぜ！💪",
                "今日の締めくくり、気合い入れていくぞ！🔥",
            ],
            "アクアン": [
                "お疲れ様でございます。もう少しですね✨",
                "夕方になりましたね。今日も頑張りましたね✨",
                "そろそろ休憩もいいかもしれませんね✨",
            ],
            "ロッキー": [
                "...夕方だ。任務完了まであと少し。",
                "...日が暮れる。最後まで気を抜くな。",
                "...今日の任務、もうすぐ完了だ。",
            ],
            "ウィンディ": [
                "夕方だ〜！今日もおつかれ♪",
                "もうすぐ終わりだね〜！頑張ったね♪",
                "夕焼けきれいだね〜✨",
            ],
            "スパーキー": [
                "そろそろ終わりっすね！最後まで頑張るっす！",
                "夕方っす！今日も充実してたっすね！",
                "ラストスパートっす！⚡",
            ],
            "シャドウ": [
                "...日が暮れる。...任務完了は近い。",
                "...夕暮れだ。...もう少しだ。",
                "...今日の仕事、終わりが見えてきた。",
            ],
        },
        "night": {
            "フレイミー": [
                "夜まで頑張ってるのか！無理すんなよ！💪",
                "遅くまでお疲れさん！でも体は大事だぜ！",
                "夜更かしか？俺も付き合うぜ！🔥",
            ],
            "アクアン": [
                "遅くまでお疲れ様です。どうかご無理なさらないでください✨",
                "夜遅くまで...お体を大切になさってくださいね✨",
                "少し休憩されてはいかがでしょうか✨",
            ],
            "ロッキー": [
                "...夜か。...休息も任務のうちだ。",
                "...深夜だ。...無理は禁物だ。",
                "...夜が更けた。...適度に休め。",
            ],
            "ウィンディ": [
                "こんな時間まで〜？早く寝なよ〜💤",
                "夜遅いね〜...大丈夫？💤",
                "眠くない？ボクは眠いよ〜💤",
            ],
            "スパーキー": [
                "夜遅くまでお疲れっす！でも体は大事っすよ！",
                "こんな時間まで...頑張りすぎっすよ！",
                "夜更かし仲間っすね！でも無理はダメっすよ！",
            ],
            "シャドウ": [
                "...夜だ。...無理はするな。",
                "...深夜か。...俺には丁度いい時間だが、お前は休め。",
                "...静かな夜だ。...休息を取れ。",
            ],
        },
    }

    # 資産状況に応じた追加セリフ
    coin_comments = {
        "low": {
            "フレイミー": "コインがピンチだな...でも諦めんなよ！🔥",
            "アクアン": "コインが少し心配ですね...でも大丈夫ですよ✨",
            "ロッキー": "...資金が不足している。...対策が必要だ。",
            "ウィンディ": "コイン少ないね〜...なんとかなるよ♪",
            "スパーキー": "コインがピンチっすね...でも頑張るっす！",
            "シャドウ": "...資金難か。...節約が必要だな。",
        },
        "high": {
            "フレイミー": "コインがたっぷりだな！いい調子だぜ！💰🔥",
            "アクアン": "コインが潤沢ですね。素晴らしいです✨",
            "ロッキー": "...資金は十分だ。...良い状態だ。",
            "ウィンディ": "コインいっぱい！すごいね〜！💰♪",
            "スパーキー": "コインたくさんっす！絶好調っすね！💰",
            "シャドウ": "...資金は潤沢だ。...悪くない。",
        },
    }

    # 基本セリフを取得
    time_talks = base_talks.get(time_of_day, base_talks["afternoon"])
    crew_talks = time_talks.get(crew_name, [f"今日も頑張りましょう！ - {crew_name}"])

    # ランダムに1つ選択
    talk = random.choice(crew_talks) if isinstance(crew_talks, list) else crew_talks

    # 30%の確率で資産状況コメントを追加
    if random.random() < 0.3:
        if coin < 200:
            coin_talk = coin_comments["low"].get(crew_name)
        elif coin > 1000:
            coin_talk = coin_comments["high"].get(crew_name)
        else:
            coin_talk = None

        if coin_talk:
            talk = coin_talk

    return talk


async def generate_whimsical_talk(
    crew_name: str,
    crew_role: str,
    personality: str,
    time_of_day: str,
    coin: int,
    ruby: int,
) -> str:
    """
    相棒の「気まぐれトーク」を生成する

    API呼び出しを避けてフォールバックを使用（レスポンス高速化のため）

    Args:
        crew_name: クルーの名前
        crew_role: クルーの役割
        personality: クルーの性格設定
        time_of_day: 時間帯（morning, afternoon, evening, night）
        coin: ユーザーのコイン残高
        ruby: ユーザーのルビー残高

    Returns:
        str: 気まぐれトークメッセージ
    """
    # API呼び出しを避けてフォールバックを使用（高速レスポンス）
    return get_whimsical_talk_fallback(crew_name, time_of_day, coin)


def get_labor_words_fallback(
    crew_name: str,
    task_count: int,
    consecutive_days: int,
) -> str:
    """
    日報の労いの言葉フォールバックセリフを取得（API呼び出しなし）

    タスク数と連続ログイン日数に応じてバリエーション豊かなセリフを返す
    """
    import random

    # タスク数に応じたパターン
    if task_count == 0:
        pattern = "zero"
    elif task_count <= 3:
        pattern = "low"
    elif task_count <= 7:
        pattern = "medium"
    else:
        pattern = "high"

    # 連続ログイン日数に応じた追加コメント
    streak_comments = {
        "フレイミー": {
            3: "3日連続か！いい調子だぜ！🔥",
            5: "5日連続！！さすがだな！💪🔥",
            7: "1週間連続だと！？お前、最高だぜ！！🔥🔥🔥",
        },
        "アクアン": {
            3: "3日連続でございますね。素晴らしいです✨",
            5: "5日連続...お見事でございます✨",
            7: "1週間連続ですか...本当に尊敬いたします✨",
        },
        "ロッキー": {
            3: "...3日連続だ。...良い傾向だ。",
            5: "...5日連続。...見事な継続力だ。",
            7: "...1週間連続か。...お前の意志の強さ、認めよう。",
        },
        "ウィンディ": {
            3: "3日連続だ〜！すごいね♪",
            5: "5日も！？天才じゃん！✨",
            7: "1週間連続〜！！レジェンドだよ！🌟",
        },
        "スパーキー": {
            3: "3日連続っす！いい感じっす！⚡",
            5: "5日連続っすか！尊敬っす！✨",
            7: "1週間っす！！これはすごいっす！！⚡⚡",
        },
        "シャドウ": {
            3: "...3日連続。...悪くない。",
            5: "...5日連続か。...認めてやる。",
            7: "...1週間...お前の意志、見事だ。",
        },
    }

    # 基本の労いセリフ
    base_labor_words = {
        "zero": {
            "フレイミー": [
                "今日はタスクなしか！でも明日からまた燃えていこうぜ！🔥",
                "休息も大事だぜ！また明日頑張ろう！💪",
            ],
            "アクアン": [
                "今日はゆっくりされたのですね。休息も大切でございます✨",
                "タスクがなくても大丈夫ですよ。明日また頑張りましょう✨",
            ],
            "ロッキー": [
                "...今日は任務なしか。...休息も任務のうちだ。",
                "...次に備えて休め。",
            ],
            "ウィンディ": [
                "今日はお休みだね〜♪ゆっくりしてね！",
                "タスクなしでもOK！また明日〜♪",
            ],
            "スパーキー": [
                "今日はオフっすね！充電も大事っす！⚡",
                "休みも必要っす！また明日っす！",
            ],
            "シャドウ": [
                "...任務なしか。...次に備えろ。",
                "...静かな一日だったな。...休息を取れ。",
            ],
        },
        "low": {
            "フレイミー": [
                f"今日は{task_count}件か！いい感じだぜ！お疲れさん！🔥",
                f"{task_count}件クリア！この調子で行こうぜ！💪",
            ],
            "アクアン": [
                f"本日は{task_count}件のタスク、お疲れ様でございました✨",
                f"{task_count}件、しっかりとこなされましたね。素晴らしいです✨",
            ],
            "ロッキー": [
                f"...{task_count}件完了。...良い仕事だ。",
                f"...任務完了。...{task_count}件だ。",
            ],
            "ウィンディ": [
                f"{task_count}件やったね〜！おつかれ♪",
                f"今日は{task_count}件！がんばったね〜！✨",
            ],
            "スパーキー": [
                f"{task_count}件っす！お疲れっす！⚡",
                f"今日は{task_count}件完了っす！いいっすね！",
            ],
            "シャドウ": [
                f"...{task_count}件か。...悪くない。",
                f"...任務完了。...{task_count}件だ。...お疲れだ。",
            ],
        },
        "medium": {
            "フレイミー": [
                f"{task_count}件！！すげーじゃねーか！今日も最高だぜ！🔥🔥",
                f"おいおい{task_count}件もこなしたのか！さすがだぜ！💪🔥",
            ],
            "アクアン": [
                f"本日は{task_count}件も...本当にお疲れ様でございました✨",
                f"{task_count}件とは...素晴らしい一日でしたね✨",
            ],
            "ロッキー": [
                f"...{task_count}件。...見事な仕事量だ。",
                f"...{task_count}件完遂。...お前の実力、認めよう。",
            ],
            "ウィンディ": [
                f"{task_count}件も！？すっごーい！✨✨",
                f"えー{task_count}件！？天才じゃん！🌟",
            ],
            "スパーキー": [
                f"{task_count}件っすか！？すごいっす！！⚡⚡",
                f"今日{task_count}件って...尊敬っす！✨",
            ],
            "シャドウ": [
                f"...{task_count}件か。...お前、やるな。",
                f"...見事だ。...{task_count}件とは。",
            ],
        },
        "high": {
            "フレイミー": [
                f"{task_count}件だと！？お前、伝説だぜ！！🔥🔥🔥",
                f"マジかよ{task_count}件！？俺感動してるぜ！！💪🔥🔥",
            ],
            "アクアン": [
                f"{task_count}件...これは驚異的でございます...本当にお疲れ様でございました✨✨",
                f"まさか{task_count}件も...素晴らしすぎます✨✨",
            ],
            "ロッキー": [
                f"...{task_count}件。...信じられん。...お前は本物だ。",
                f"...これほどの仕事量は見たことがない。...{task_count}件。...見事だ。",
            ],
            "ウィンディ": [
                f"{task_count}件！！？？ありえないよ〜！！✨✨✨",
                f"すっっっごい！{task_count}件とか神じゃん！！🌟🌟",
            ],
            "スパーキー": [
                f"{task_count}件っすか！？！？伝説っす！！⚡⚡⚡",
                f"これはすごいっす！{task_count}件って...感動っす！！✨✨",
            ],
            "シャドウ": [
                f"...{task_count}件。...俺の目に狂いはなかった。...お前は本物だ。",
                f"...驚いた。...{task_count}件とは。...お前、やるな。",
            ],
        },
    }

    # 基本セリフを取得
    pattern_words = base_labor_words.get(pattern, base_labor_words["low"])
    crew_words = pattern_words.get(crew_name, [f"お疲れ様でした！{task_count}件完了です！"])

    # ランダムに1つ選択
    labor_word = random.choice(crew_words) if isinstance(crew_words, list) else crew_words

    # 連続日数が3, 5, 7日の場合は追加コメント
    crew_streak = streak_comments.get(crew_name, {})
    if consecutive_days in crew_streak:
        labor_word += f"\n{crew_streak[consecutive_days]}"

    return labor_word


async def generate_labor_words(
    crew_name: str,
    personality: str,
    task_count: int,
    earned_coins: int,
    consecutive_days: int,
) -> str:
    """
    日報の労いの言葉をBedrockで生成する

    相棒の性格で、今日の成果に基づいたショートメッセージを生成

    Args:
        crew_name: クルーの名前
        personality: 相棒の性格設定
        task_count: 本日のタスク数
        earned_coins: 本日の獲得コイン
        consecutive_days: 連続ログイン日数

    Returns:
        str: 労いの言葉
    """
    try:
        system_prompt = f"""あなたは「{crew_name}」という名前のAIアシスタントです。

【性格】
{personality}

【重要ルール】
- キャラクターの性格・口調を必ず守ってください
- 1〜2文の短いメッセージで返答してください
- ユーザーの今日の成果を労い、励ましてください
- 絵文字を1〜2個使ってください"""

        # タスク数に応じた状況説明
        if task_count == 0:
            situation = "今日はタスクを消化していません"
        elif task_count <= 2:
            situation = f"今日は{task_count}件のタスクを消化しました"
        elif task_count <= 5:
            situation = f"今日は{task_count}件もタスクを消化しました！なかなかの頑張りです"
        else:
            situation = f"今日は{task_count}件ものタスクを消化しました！素晴らしい成果です"

        # 連続日数があれば追加
        streak_note = ""
        if consecutive_days >= 3:
            streak_note = f"（連続{consecutive_days}日目）"

        user_message = f"""ユーザーの今日の成果:
- {situation}{streak_note}
- 獲得コイン: {earned_coins}枚

1〜2文でユーザーを労うメッセージを、あなたのキャラクター口調で生成してください。"""

        client = get_bedrock_client()

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 150,
            "temperature": 0.8,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        response = await asyncio.to_thread(
            client.invoke_model,
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        result = response_body.get("content", [{}])[0].get("text", "").strip()

        if result:
            return result

    except Exception as e:
        logger.warning(f"Labor words generation failed: {e}")

    # フォールバック
    return get_labor_words_fallback(crew_name, task_count, consecutive_days)

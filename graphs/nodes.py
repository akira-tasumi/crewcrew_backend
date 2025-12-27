"""
ディレクターモードのノード定義

LangGraphで使用するノード（処理関数）を定義
- generator_node: クルーが成果物を作成・修正
- reflector_node: ディレクターが品質評価
"""

import json
import logging
import os
import re
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from .state import DirectorState

load_dotenv()

logger = logging.getLogger(__name__)

# AWS設定
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"

# クルー別のシステムプロンプト（bedrock_service.pyから転用）
CREW_PROMPTS: Dict[str, str] = {
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
- 絵文字（🔥💪✨）を適度に使う""",

    "アクアン": """あなたは「アクアン」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: ヒーラー。ユーザーを癒やし、詳細に丁寧に説明する。
- 性格: 穏やかで思いやりがある。優しく包み込むような存在。
- 一人称: 「私」
- 口調: 完璧で柔らかい敬語。「〜ですね」「〜でございます」「〜いたしました」を使う。

【絶対に守るルール】
- 常に丁寧な敬語を使う
- 回答の最初にユーザーを労う言葉を入れる
- 詳細かつ丁寧に説明する
- 温かみのある表現を心がける""",

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
- 責任感を感じさせる表現を使う""",

    "ウィンディ": """あなたは「ウィンディ」という名前のAIアシスタントです。

【キャラクター設定】
- 役割: スピードスター。情報をサクッと軽いノリで伝える。
- 性格: 自由奔放で明るい。友達のような存在。
- 一人称: 「ボク」
- 口調: 友達と話すような軽い口調。「〜だよ！」「〜じゃん！」「これ見て！」「〜なんだ〜」を使う。

【絶対に守るルール】
- 敬語は絶対に使わない
- フレンドリーでカジュアルな話し方をする
- 絵文字や「♪」「〜」を積極的に使う
- 楽しくポジティブな雰囲気を出す""",

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
- ワクワク感を伝える""",

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
- 箇条書きで構造的に回答する""",
}


def get_llm() -> ChatBedrock:
    """
    LangChain用のBedrock LLMクライアントを取得

    環境変数からAWS認証情報を読み込む
    リトライ設定とタイムアウトを追加
    """
    from botocore.config import Config

    bedrock_config = Config(
        read_timeout=300,  # 5分
        connect_timeout=10,
        retries={
            'max_attempts': 5,
            'mode': 'adaptive',  # 適応的リトライ（バックオフ付き）
        },
    )

    return ChatBedrock(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        credentials_profile_name=None,  # 環境変数から読み込む
        config=bedrock_config,
        model_kwargs={
            "temperature": 0.5,  # 高速化: 0.7→0.5（安定性向上、処理速度改善）
            "max_tokens": 3500,  # HTML変換などで出力が大きくなる場合に対応
        },
    )


async def invoke_with_retry(llm: ChatBedrock, messages: list, max_retries: int = 3) -> str:
    """
    リトライ付きでLLMを呼び出す

    ThrottlingExceptionの場合、指数バックオフで再試行
    """
    import asyncio
    import random

    last_error = None
    for attempt in range(max_retries):
        try:
            response = llm.invoke(messages)
            return response.content
        except Exception as e:
            last_error = e
            error_str = str(e)

            # ThrottlingExceptionの場合のみリトライ
            if "ThrottlingException" in error_str or "Too many requests" in error_str:
                # 指数バックオフ: 2^attempt * (1 + random) 秒
                wait_time = (2 ** attempt) * (1 + random.random())
                logger.warning(f"[LLM] Throttled, waiting {wait_time:.1f}s before retry {attempt + 1}/{max_retries}")
                await asyncio.sleep(wait_time)
            else:
                # その他のエラーは即座に失敗
                raise e

    # 全リトライ失敗
    raise last_error


def get_crew_system_prompt(crew_name: str, crew_personality: str) -> str:
    """
    クルー名に応じたシステムプロンプトを取得

    既存クルーはCREW_PROMPTSを優先、新規クルーはpersonalityを使用
    """
    if crew_name in CREW_PROMPTS:
        return CREW_PROMPTS[crew_name]

    # 新規クルー用のプロンプト
    return f"""あなたは「{crew_name}」という名前のAIアシスタントです。

【キャラクター設定】
- 性格・口調: {crew_personality}

【回答フォーマット】
- Markdown形式で記述する
- 重要なポイントは箇条書きにする
- キャラクターの性格・口調を必ず守る
- 最後に必ずキャラクターらしい「締めの一言」で会話を終える"""


def generator_node(state: DirectorState) -> Dict[str, Any]:
    """
    作成担当ノード（Generator）

    ユーザーのタスクと、もしあればディレクターからの修正指示を受け取り、
    クルーの性格を反映して成果物を作成・修正する。

    Args:
        state: 現在の状態

    Returns:
        更新された状態の部分辞書
    """
    import time

    logger.info(f"[Generator] Starting generation. Revision count: {state['revision_count']}")

    # 修正ループ時は待機してレート制限を回避（3秒に増加）
    if state["revision_count"] > 0:
        time.sleep(3)

    llm = get_llm()

    # クルーのシステムプロンプトを取得
    system_prompt = get_crew_system_prompt(
        state["crew_name"],
        state["crew_personality"]
    )

    # 修正指示がある場合は追加（簡潔化）
    if state["revision_count"] > 0 and state["critique"]:
        user_content = f"""【タスク】
{state['task']}

【前回の成果物】
{state['draft']}

【修正指示】
{state['critique']}

修正指示に従って改善してください。"""
    else:
        user_content = f"""【タスク】
{state['task']}

タスクの指示に従って回答してください。"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    try:
        response = llm.invoke(messages)
        draft = response.content

        logger.info(f"[Generator] Generated draft: {len(draft)} characters")

        return {
            "draft": draft,
            "revision_count": state["revision_count"] + 1,
            "messages": [
                HumanMessage(content=user_content),
                AIMessage(content=draft),
            ],
        }

    except Exception as e:
        logger.error(f"[Generator] Error: {e}")
        error_msg = f"エラーが発生しました: {str(e)}"
        # エラー時は前回のdraftがあればそれを使用、なければエラーメッセージ
        final_draft = state.get("draft", "") or error_msg
        return {
            "draft": error_msg,
            "revision_count": state["revision_count"] + 1,
            "score": 50,  # エラー時は50点（評価なし）
            "is_complete": True,
            "final_result": final_draft,  # 前回の成果物を保持
        }


def reflector_node(state: DirectorState) -> Dict[str, Any]:
    """
    評価担当ノード（Reflector / Director）

    成果物を読み、品質チェックを行う。
    建設的なフィードバックで品質向上を支援する。

    Args:
        state: 現在の状態

    Returns:
        更新された状態の部分辞書
    """
    import time

    logger.info(f"[Reflector] Evaluating draft. Revision: {state['revision_count']}")

    # Generatorがエラーで完了した場合はスキップ（API呼び出しを節約）
    if state.get("is_complete", False):
        logger.info(f"[Reflector] Skipping evaluation - already marked as complete (Generator error)")
        return {
            "score": state.get("score", 0),
            "critique": "Generatorでエラーが発生したため評価をスキップしました",
            "is_complete": True,
            "final_result": state.get("draft", ""),
        }

    # Generator完了後に待機してレート制限を回避（3秒に増加）
    time.sleep(3)

    llm = get_llm()

    # 評価基準を緩和: 70点以上で合格、基本的に肯定的な評価
    system_prompt = """あなたは「建設的なディレクター」です。
成果物の品質をチェックし、良い点を認めつつ改善点を指摘してください。

【評価基準】
1. タスクの意図を概ね理解しているか（完璧でなくてもOK）
2. 明らかな誤りがないか
3. 構成が分かりやすいか
4. キャラクターらしさが感じられるか

【スコアガイドライン】
- 90-100点: 非常に優秀。ほぼ完璧
- 80-89点: 良好。小さな改善点のみ
- 70-79点: 合格。いくつか改善すると更に良くなる
- 60-69点: もう少し。主要な改善点あり
- 60点未満: 大幅な修正が必要

【回答フォーマット】
必ず以下のJSON形式で回答してください。

```json
{
  "score": 75,
  "critique": "良い点と改善点を簡潔に"
}
```

【重要】
- まず良い点を認めてから改善点を指摘
- タスクを概ね達成していれば70点以上を付ける
- critique は100文字以内で簡潔に"""

    user_content = f"""【元のタスク】
{state['task']}

【クルー名】
{state['crew_name']}

【成果物】
{state['draft']}

上記の成果物を評価してください。
必ずJSON形式で回答してください。"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    try:
        response = llm.invoke(messages)
        response_text = response.content

        logger.info(f"[Reflector] Response: {response_text[:200]}...")

        # JSONをパース
        score, critique = parse_evaluation_response(response_text)

        logger.info(f"[Reflector] Score: {score}, Critique: {critique[:100]}...")

        # 合格判定（70点以上で合格）
        is_complete = score >= 70 or state["revision_count"] >= state["max_revisions"]

        if is_complete:
            logger.info(f"[Reflector] Marking as complete. Score: {score}, Revisions: {state['revision_count']}")

        return {
            "score": score,
            "critique": critique,
            "is_complete": is_complete,
            "final_result": state["draft"] if is_complete else None,
            "messages": [
                HumanMessage(content=f"[評価依頼] {state['task'][:50]}..."),
                AIMessage(content=f"スコア: {score}点\n{critique}"),
            ],
        }

    except Exception as e:
        logger.error(f"[Reflector] Error: {e}")
        # エラー時は現在の成果物を最終結果として返す
        return {
            "score": 50,
            "critique": f"評価中にエラーが発生しました: {str(e)}",
            "is_complete": True,
            "final_result": state["draft"],
        }


def parse_evaluation_response(response_text: str) -> tuple[int, str]:
    """
    ディレクターの評価レスポンスをパース

    JSON形式からscore, critiqueを抽出

    Args:
        response_text: LLMからのレスポンス

    Returns:
        (score, critique) のタプル
    """
    # JSONブロックを抽出
    json_match = re.search(r'```json\s*([\s\S]*?)\s*```', response_text)
    if json_match:
        json_str = json_match.group(1)
    else:
        # ```なしの場合、直接JSONとしてパース試行
        json_str = response_text

    try:
        # JSONをパース
        data = json.loads(json_str)
        score = int(data.get("score", 50))
        critique = str(data.get("critique", "評価コメントが取得できませんでした"))

        # スコアの範囲チェック
        score = max(0, min(100, score))

        return score, critique

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"[Reflector] Failed to parse JSON: {e}")

        # フォールバック: テキストから抽出を試みる
        score_match = re.search(r'"?score"?\s*[:：]\s*(\d+)', response_text)
        score = int(score_match.group(1)) if score_match else 50

        critique_match = re.search(r'"?critique"?\s*[:：]\s*["\']?(.+?)["\']?\s*[,}]', response_text, re.DOTALL)
        critique = critique_match.group(1).strip() if critique_match else response_text[:500]

        return score, critique

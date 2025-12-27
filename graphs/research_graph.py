"""
Deep Research グラフ定義

LangGraphを使用した自律リサーチ機能
- researcher_node: 検索キーワード生成・検索実行・情報収集
- writer_node: 収集した情報から最終回答を作成
"""

import json
import logging
import os
from typing import Dict, Any, List, TypedDict

from dotenv import load_dotenv
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from botocore.config import Config

load_dotenv()

logger = logging.getLogger(__name__)

# AWS設定
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"


def get_tavily_api_key() -> str:
    """Tavily APIキーを取得（都度読み込み）"""
    load_dotenv()  # 再読み込み
    return os.getenv("TAVILY_API_KEY", "")

# 最大検索ループ回数
MAX_LOOPS = 3


# =============================================================================
# State定義
# =============================================================================

class ResearchState(TypedDict):
    """リサーチの状態を管理"""
    question: str  # ユーザーの元の質問
    search_queries: List[str]  # 過去に試した検索クエリ
    gathered_info: List[Dict[str, str]]  # 検索で得られた情報 [{content, url, title}]
    loop_count: int  # ループ回数
    is_sufficient: bool  # 情報が十分集まったかどうか
    final_answer: str  # 最終回答


def create_initial_state(question: str) -> ResearchState:
    """初期状態を作成"""
    return {
        "question": question,
        "search_queries": [],
        "gathered_info": [],
        "loop_count": 0,
        "is_sufficient": False,
        "final_answer": "",
    }


# =============================================================================
# LLMクライアント
# =============================================================================

def get_llm() -> ChatBedrock:
    """LangChain用のBedrock LLMクライアントを取得"""
    bedrock_config = Config(
        read_timeout=300,
        connect_timeout=10,
        retries={
            'max_attempts': 5,
            'mode': 'adaptive',
        },
    )

    return ChatBedrock(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        credentials_profile_name=None,
        config=bedrock_config,
        model_kwargs={
            "temperature": 0.3,  # リサーチは正確性重視
            "max_tokens": 2000,
        },
    )


# =============================================================================
# Tavily検索
# =============================================================================

def search_with_tavily(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Tavily APIで検索を実行

    Args:
        query: 検索クエリ
        max_results: 最大結果数

    Returns:
        検索結果のリスト [{content, url, title}]
    """
    api_key = get_tavily_api_key()
    if not api_key:
        logger.error("[Research] TAVILY_API_KEY is not set")
        return []

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_answer=False,
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "content": item.get("content", ""),
                "url": item.get("url", ""),
                "title": item.get("title", ""),
            })

        logger.info(f"[Research] Tavily search for '{query}': {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"[Research] Tavily search error: {e}")
        return []


# =============================================================================
# Nodes
# =============================================================================

def researcher_node(state: ResearchState) -> Dict[str, Any]:
    """
    検索・判断担当ノード

    1. 現在の情報を分析
    2. 次の検索クエリを決定、または情報が十分か判断
    3. Tavilyで検索実行
    """
    import time

    logger.info(f"[Researcher] Loop {state['loop_count'] + 1}/{MAX_LOOPS}")

    # レート制限回避（2回目以降は15秒待機）
    if state["loop_count"] > 0:
        logger.info("[Researcher] Waiting 15 seconds to avoid rate limit...")
        time.sleep(15)

    llm = get_llm()

    # 既存の情報をまとめる
    existing_info = ""
    if state["gathered_info"]:
        info_list = []
        for i, info in enumerate(state["gathered_info"], 1):
            info_list.append(f"{i}. {info['title']}: {info['content'][:200]}...")
        existing_info = "\n".join(info_list)

    past_queries = ", ".join(state["search_queries"]) if state["search_queries"] else "なし"

    system_prompt = """あなたは徹底的なリサーチャーです。
ユーザーの質問に答えるために、多角的な視点から情報を収集してください。

【あなたの役割】
1. 質問に回答するために必要な情報を分析する
2. まだ足りない情報があれば、新しい検索クエリを考える
3. 十分な情報が集まったら、そのことを報告する

【重要なポイント】
- 過去に使った検索クエリとは異なる角度から検索する
- 事実確認のために複数の情報源を探す
- 最新の情報を優先する

【回答フォーマット】
必ず以下のJSON形式で回答してください。

```json
{
  "is_sufficient": false,
  "reasoning": "なぜ追加の検索が必要か、または十分な理由",
  "next_query": "次に検索するクエリ（is_sufficientがfalseの場合のみ）"
}
```"""

    user_content = f"""【ユーザーの質問】
{state['question']}

【過去の検索クエリ】
{past_queries}

【これまでに収集した情報】
{existing_info if existing_info else "まだ情報を収集していません"}

上記を踏まえて、追加の検索が必要かどうか判断してください。
必要であれば次の検索クエリを提案してください。"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    try:
        response = llm.invoke(messages)
        response_text = response.content

        logger.info(f"[Researcher] LLM response: {response_text[:300]}...")

        # JSONをパース
        import re
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response_text

        data = json.loads(json_str)
        is_sufficient = data.get("is_sufficient", False)
        next_query = data.get("next_query", "")

        # 情報が十分な場合
        if is_sufficient:
            logger.info("[Researcher] Information is sufficient")
            return {
                "is_sufficient": True,
                "loop_count": state["loop_count"] + 1,
            }

        # 追加検索が必要な場合
        if next_query:
            logger.info(f"[Researcher] Searching for: {next_query}")
            search_results = search_with_tavily(next_query)

            new_queries = state["search_queries"] + [next_query]
            new_info = state["gathered_info"] + search_results

            return {
                "search_queries": new_queries,
                "gathered_info": new_info,
                "loop_count": state["loop_count"] + 1,
                "is_sufficient": False,
            }

        # クエリがない場合は十分とみなす
        return {
            "is_sufficient": True,
            "loop_count": state["loop_count"] + 1,
        }

    except Exception as e:
        logger.error(f"[Researcher] Error: {e}")
        # エラー時は現在の収集済み情報を保持して終了
        return {
            "is_sufficient": True,
            "loop_count": state["loop_count"] + 1,
            "search_queries": state.get("search_queries", []),
            "gathered_info": state.get("gathered_info", []),
        }


def writer_node(state: ResearchState) -> Dict[str, Any]:
    """
    執筆担当ノード

    収集した情報を元に、ユーザーの質問に対する最終回答を作成
    """
    import time

    logger.info(f"[Writer] Creating final answer from {len(state['gathered_info'])} sources")

    # レート制限回避
    time.sleep(3)

    llm = get_llm()

    # 収集した情報をフォーマット
    sources_text = ""
    if state["gathered_info"]:
        sources_list = []
        for i, info in enumerate(state["gathered_info"], 1):
            sources_list.append(f"""【情報{i}】
タイトル: {info['title']}
URL: {info['url']}
内容: {info['content']}
""")
        sources_text = "\n".join(sources_list)
    else:
        sources_text = "情報が収集できませんでした。"

    system_prompt = """あなたは優秀なリサーチライターです。
収集された情報を元に、ユーザーの質問に対する包括的で正確な回答を作成してください。

【回答のルール】
1. 収集された情報を元に、事実に基づいた回答を作成する
2. 情報源（URL）を明記する
3. 複数の情報源から得られた情報を統合する
4. 不明な点は「情報が見つかりませんでした」と正直に伝える
5. Markdown形式で読みやすく構成する

【回答構成】
- 概要（質問への簡潔な回答）
- 詳細（収集した情報を元にした詳しい説明）
- 出典（参照したURLのリスト）"""

    user_content = f"""【ユーザーの質問】
{state['question']}

【収集した情報】
{sources_text}

上記の情報を元に、質問に対する回答を作成してください。"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    try:
        response = llm.invoke(messages)
        final_answer = response.content

        logger.info(f"[Writer] Generated answer: {len(final_answer)} characters")

        return {
            "final_answer": final_answer,
        }

    except Exception as e:
        logger.error(f"[Writer] Error: {e}")
        return {
            "final_answer": f"回答の生成中にエラーが発生しました: {str(e)}",
        }


# =============================================================================
# Workflow
# =============================================================================

def should_continue(state: ResearchState) -> str:
    """
    条件分岐: 検索を続けるか、執筆に進むか判定
    """
    if state.get("is_sufficient", False):
        logger.info("[Workflow] Sufficient info -> writer")
        return "writer"

    if state.get("loop_count", 0) >= MAX_LOOPS:
        logger.info(f"[Workflow] Max loops ({MAX_LOOPS}) reached -> writer")
        return "writer"

    logger.info("[Workflow] Need more info -> researcher")
    return "researcher"


def build_research_graph() -> StateGraph:
    """
    リサーチグラフを構築

    フロー:
    START -> researcher -> (条件分岐)
                            ├─> researcher (ループ)
                            └─> writer -> END
    """
    workflow = StateGraph(ResearchState)

    # ノードを追加
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("writer", writer_node)

    # エントリーポイント
    workflow.set_entry_point("researcher")

    # 条件分岐
    workflow.add_conditional_edges(
        "researcher",
        should_continue,
        {
            "researcher": "researcher",
            "writer": "writer",
        }
    )

    # writer -> END
    workflow.add_edge("writer", END)

    return workflow


# コンパイル済みグラフ
research_app = build_research_graph().compile()


# =============================================================================
# 実行関数
# =============================================================================

async def run_deep_research(question: str) -> Dict[str, Any]:
    """
    Deep Researchを実行

    Args:
        question: ユーザーの質問

    Returns:
        {
            "success": bool,
            "answer": str,
            "search_queries": List[str],
            "sources": List[Dict],
            "loop_count": int,
            "error": str | None,
        }
    """
    logger.info(f"[Deep Research] Starting: {question[:50]}...")

    try:
        initial_state = create_initial_state(question)

        # 状態を累積して保持
        accumulated_state = dict(initial_state)
        async for state in research_app.astream(initial_state):
            for node_name, node_state in state.items():
                logger.debug(f"[Deep Research] Node '{node_name}' completed")
                # 各ノードの出力で状態を更新
                accumulated_state.update(node_state)

        if not accumulated_state.get("final_answer"):
            raise ValueError("Research produced no output")

        # 結果を整形
        result = {
            "success": True,
            "answer": accumulated_state.get("final_answer", ""),
            "search_queries": accumulated_state.get("search_queries", []),
            "sources": [
                {"title": info["title"], "url": info["url"]}
                for info in accumulated_state.get("gathered_info", [])
            ],
            "loop_count": accumulated_state.get("loop_count", 0),
            "error": None,
        }

        logger.info(
            f"[Deep Research] Completed: {len(result['sources'])} sources, "
            f"{result['loop_count']} loops"
        )

        return result

    except Exception as e:
        logger.error(f"[Deep Research] Error: {e}")
        return {
            "success": False,
            "answer": "",
            "search_queries": [],
            "sources": [],
            "loop_count": 0,
            "error": str(e),
        }


async def run_deep_research_stream(question: str):
    """
    Deep Researchをストリーミング実行

    各ステップの進捗をリアルタイムで送信

    Yields:
        SSEイベント用の辞書
    """
    logger.info(f"[Deep Research Stream] Starting: {question[:50]}...")

    try:
        yield {
            "type": "research_start",
            "question": question[:100] + "..." if len(question) > 100 else question,
            "max_loops": MAX_LOOPS,
        }

        initial_state = create_initial_state(question)
        final_state = None

        async for state in research_app.astream(initial_state):
            for node_name, node_state in state.items():
                if node_name == "researcher":
                    loop_count = node_state.get("loop_count", 0)
                    queries = node_state.get("search_queries", [])
                    latest_query = queries[-1] if queries else ""
                    info_count = len(node_state.get("gathered_info", []))

                    yield {
                        "type": "search_complete",
                        "loop_count": loop_count,
                        "latest_query": latest_query,
                        "total_sources": info_count,
                        "is_sufficient": node_state.get("is_sufficient", False),
                    }

                elif node_name == "writer":
                    yield {
                        "type": "writing",
                        "message": "収集した情報から回答を作成中...",
                    }

                final_state = node_state

        if final_state is None:
            raise ValueError("Research produced no output")

        yield {
            "type": "research_complete",
            "success": True,
            "answer": final_state.get("final_answer", ""),
            "search_queries": final_state.get("search_queries", []),
            "sources": [
                {"title": info["title"], "url": info["url"]}
                for info in final_state.get("gathered_info", [])
            ],
            "loop_count": final_state.get("loop_count", 0),
        }

    except Exception as e:
        logger.error(f"[Deep Research Stream] Error: {e}")
        yield {
            "type": "research_error",
            "success": False,
            "error": str(e),
        }

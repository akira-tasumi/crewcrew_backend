"""
ディレクターモードのワークフロー定義

LangGraphを使用して自己修正ループを構築
- generator -> reflector -> (条件分岐) -> generator or END
"""

import logging
from typing import Dict, Any, Optional

from langgraph.graph import StateGraph, END

from .state import DirectorState, create_initial_state
from .nodes import generator_node, reflector_node

logger = logging.getLogger(__name__)


def should_continue(state: DirectorState) -> str:
    """
    条件分岐: 継続するか終了するかを判定

    終了条件:
    - スコアが80点以上（合格）
    - 修正回数が最大回数に達した
    - is_completeフラグがTrue

    Args:
        state: 現在の状態

    Returns:
        "generator" (継続) または "end" (終了)
    """
    if state.get("is_complete", False):
        logger.info(f"[Workflow] Ending: is_complete=True, score={state.get('score', 0)}")
        return "end"

    if state.get("score", 0) >= 70:
        logger.info(f"[Workflow] Ending: score={state['score']} >= 70 (passed)")
        return "end"

    if state.get("revision_count", 0) >= state.get("max_revisions", 3):
        logger.info(f"[Workflow] Ending: revision_count={state['revision_count']} >= max_revisions")
        return "end"

    logger.info(f"[Workflow] Continuing: score={state.get('score', 0)}, revision={state.get('revision_count', 0)}")
    return "generator"


def build_director_graph() -> StateGraph:
    """
    ディレクターモードのグラフを構築

    フロー:
    START -> generator -> reflector -> (条件分岐)
                                        ├─> generator (ループ)
                                        └─> END

    Returns:
        コンパイル済みのStateGraph
    """
    # グラフを作成
    workflow = StateGraph(DirectorState)

    # ノードを追加
    workflow.add_node("generator", generator_node)
    workflow.add_node("reflector", reflector_node)

    # エッジを追加
    workflow.set_entry_point("generator")  # 開始点
    workflow.add_edge("generator", "reflector")  # generator -> reflector

    # 条件分岐を追加
    workflow.add_conditional_edges(
        "reflector",
        should_continue,
        {
            "generator": "generator",  # ループ
            "end": END,  # 終了
        }
    )

    return workflow


# コンパイル済みのグラフをエクスポート
app = build_director_graph().compile()


async def run_director_workflow(
    task: str,
    crew_name: str,
    crew_personality: str,
    max_revisions: int = 3,
) -> Dict[str, Any]:
    """
    ディレクターモードのワークフローを実行

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        max_revisions: 最大修正回数（デフォルト3）

    Returns:
        実行結果を含む辞書:
        {
            "success": bool,
            "final_result": str,  # 最終成果物
            "score": int,  # 最終スコア
            "critique": str,  # 最終評価コメント
            "revision_count": int,  # 修正回数
            "error": str | None,
        }
    """
    logger.info(f"[Director] Starting workflow: task={task[:50]}..., crew={crew_name}")

    try:
        # 初期状態を作成
        initial_state = create_initial_state(
            task=task,
            crew_name=crew_name,
            crew_personality=crew_personality,
            max_revisions=max_revisions,
        )

        # グラフを実行
        final_state = None
        async for state in app.astream(initial_state):
            # 最新の状態を保持
            for node_name, node_state in state.items():
                logger.debug(f"[Director] Node '{node_name}' completed")
                final_state = node_state

        if final_state is None:
            raise ValueError("Workflow produced no output")

        # 結果を整形
        result = {
            "success": True,
            "final_result": final_state.get("final_result") or final_state.get("draft", ""),
            "score": final_state.get("score", 0),
            "critique": final_state.get("critique", ""),
            "revision_count": final_state.get("revision_count", 0),
            "crew_name": crew_name,
            "error": None,
        }

        logger.info(
            f"[Director] Workflow completed: score={result['score']}, "
            f"revisions={result['revision_count']}"
        )

        return result

    except Exception as e:
        logger.error(f"[Director] Workflow error: {e}")
        return {
            "success": False,
            "final_result": None,
            "score": 0,
            "critique": "",
            "revision_count": 0,
            "crew_name": crew_name,
            "error": str(e),
        }


def run_director_workflow_sync(
    task: str,
    crew_name: str,
    crew_personality: str,
    max_revisions: int = 3,
) -> Dict[str, Any]:
    """
    ディレクターモードのワークフローを同期的に実行（テスト用）

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        max_revisions: 最大修正回数

    Returns:
        実行結果を含む辞書
    """
    import asyncio
    return asyncio.run(run_director_workflow(task, crew_name, crew_personality, max_revisions))


async def run_director_workflow_stream(
    task: str,
    crew_name: str,
    crew_personality: str,
    crew_image: str = "",
    max_revisions: int = 3,
):
    """
    ディレクターモードのワークフローをSSEストリーミングで実行

    各ノードの実行状況をリアルタイムで送信する。
    フロントエンドでクルーが協働している感を演出するためのイベント:
    - workflow_start: ワークフロー開始
    - generation_start: クルーが成果物作成を開始
    - generation_complete: クルーが成果物を作成完了
    - reflection_start: ディレクターが評価を開始
    - reflection_complete: ディレクターが評価完了（スコア・フィードバック付き）
    - revision_start: 修正ループ開始
    - workflow_complete: ワークフロー完了（最終結果付き）
    - workflow_error: エラー発生

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        crew_image: クルーの画像URL
        max_revisions: 最大修正回数

    Yields:
        SSEイベント用の辞書
    """
    import json

    logger.info(f"[Director Stream] Starting workflow: task={task[:50]}..., crew={crew_name}")

    try:
        # ワークフロー開始イベント
        yield {
            "type": "workflow_start",
            "crew_name": crew_name,
            "crew_image": crew_image,
            "max_revisions": max_revisions,
            "task": task[:100] + "..." if len(task) > 100 else task,
        }

        # 初期状態を作成
        initial_state = create_initial_state(
            task=task,
            crew_name=crew_name,
            crew_personality=crew_personality,
            max_revisions=max_revisions,
        )

        # グラフを実行（ストリーミング）
        final_state = None
        current_revision = 0

        async for state in app.astream(initial_state):
            for node_name, node_state in state.items():
                logger.info(f"[Director Stream] Node '{node_name}' completed, state keys: {list(node_state.keys())}")

                if node_name == "generator":
                    # クルーが成果物を作成完了
                    current_revision = node_state.get("revision_count", 0)

                    if current_revision == 1:
                        # 初回生成
                        yield {
                            "type": "generation_start",
                            "crew_name": crew_name,
                            "crew_image": crew_image,
                            "revision_count": current_revision,
                            "message": f"{crew_name}が成果物を作成中...",
                        }

                    yield {
                        "type": "generation_complete",
                        "crew_name": crew_name,
                        "crew_image": crew_image,
                        "revision_count": current_revision,
                        "draft_preview": node_state.get("draft", "")[:200] + "..." if len(node_state.get("draft", "")) > 200 else node_state.get("draft", ""),
                        "message": f"{crew_name}が成果物を作成しました（{current_revision}回目）",
                    }

                elif node_name == "reflector":
                    # ディレクターが評価完了
                    score = node_state.get("score", 0)
                    critique = node_state.get("critique", "")
                    is_complete = node_state.get("is_complete", False)

                    yield {
                        "type": "reflection_complete",
                        "score": score,
                        "critique": critique,
                        "is_complete": is_complete,
                        "revision_count": current_revision,
                        "message": f"ディレクターの評価: {score}点",
                    }

                    # 修正が必要な場合
                    if not is_complete and score < 80 and current_revision < max_revisions:
                        yield {
                            "type": "revision_start",
                            "crew_name": crew_name,
                            "crew_image": crew_image,
                            "score": score,
                            "critique": critique,
                            "revision_count": current_revision,
                            "message": f"スコア{score}点のため、{crew_name}が修正を開始します...",
                        }

                # 最新の状態を保持
                final_state = node_state

        if final_state is None:
            raise ValueError("Workflow produced no output")

        # ワークフロー完了イベント
        yield {
            "type": "workflow_complete",
            "success": True,
            "final_result": final_state.get("final_result") or final_state.get("draft", ""),
            "score": final_state.get("score", 0),
            "critique": final_state.get("critique", ""),
            "revision_count": final_state.get("revision_count", 0),
            "crew_name": crew_name,
            "crew_image": crew_image,
            "message": f"完了！最終スコア: {final_state.get('score', 0)}点（{final_state.get('revision_count', 0)}回の修正）",
        }

        logger.info(
            f"[Director Stream] Workflow completed: score={final_state.get('score', 0)}, "
            f"revisions={final_state.get('revision_count', 0)}"
        )

    except Exception as e:
        logger.error(f"[Director Stream] Workflow error: {e}")
        yield {
            "type": "workflow_error",
            "success": False,
            "error": str(e),
            "crew_name": crew_name,
            "message": f"エラーが発生しました: {str(e)}",
        }


async def run_generator_only_stream(
    task: str,
    crew_name: str,
    crew_personality: str,
    crew_image: str = "",
):
    """
    Generatorのみを実行するシンプルなストリーミング実行（v2用）

    Reflectorを使わず、Generatorの出力をそのまま返す。
    これによりAPIコール数を大幅に削減。

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        crew_image: クルーの画像URL

    Yields:
        SSEイベント用の辞書
    """
    from .nodes import generator_node
    from .state import create_initial_state

    logger.info(f"[Generator Only] Starting: task={task[:50]}..., crew={crew_name}")

    try:
        # ワークフロー開始イベント
        yield {
            "type": "workflow_start",
            "crew_name": crew_name,
            "crew_image": crew_image,
            "max_revisions": 1,
            "task": task[:100] + "..." if len(task) > 100 else task,
        }

        # 初期状態を作成
        initial_state = create_initial_state(
            task=task,
            crew_name=crew_name,
            crew_personality=crew_personality,
            max_revisions=1,
        )

        # クルーが成果物作成開始
        yield {
            "type": "generation_start",
            "crew_name": crew_name,
            "crew_image": crew_image,
            "revision_count": 1,
            "message": f"{crew_name}が成果物を作成中...",
        }

        # Generatorのみ実行（同期関数なのでスレッドプールで実行）
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, generator_node, initial_state)

        draft = result.get("draft", "")
        revision_count = result.get("revision_count", 1)

        # 成果物作成完了
        yield {
            "type": "generation_complete",
            "crew_name": crew_name,
            "crew_image": crew_image,
            "revision_count": revision_count,
            "draft_preview": draft[:200] + "..." if len(draft) > 200 else draft,
            "message": f"{crew_name}が成果物を作成しました",
        }

        # ワークフロー完了イベント（Reflectorなしなのでスコアは100固定）
        yield {
            "type": "workflow_complete",
            "success": True,
            "final_result": draft,
            "score": 100,  # Reflectorなしなので自動合格
            "critique": "（評価スキップ）",
            "revision_count": 1,
            "crew_name": crew_name,
            "crew_image": crew_image,
            "message": f"完了！{crew_name}が成果物を作成しました",
        }

        logger.info(f"[Generator Only] Completed: crew={crew_name}")

    except Exception as e:
        logger.error(f"[Generator Only] Error: {e}")
        yield {
            "type": "workflow_error",
            "success": False,
            "error": str(e),
            "crew_name": crew_name,
            "message": f"エラーが発生しました: {str(e)}",
        }

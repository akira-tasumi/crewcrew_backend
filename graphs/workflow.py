"""
ディレクターモードのワークフロー定義

LangGraphを使用して自己修正ループを構築
- generator -> reflector -> (条件分岐) -> generator or END

Human-in-the-loop機能:
- 外部出力前にhuman_review_nodeでinterrupt
- 承認後にoutput_creation_nodeで出力作成
"""

import logging
import json
from typing import Dict, Any, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import DirectorState, create_initial_state, OutputType
from .nodes import generator_node, reflector_node, human_review_node, output_creation_node

logger = logging.getLogger(__name__)

# インメモリチェックポインター（本番環境ではSqliteなどに置き換え可能）
checkpointer = MemorySaver()


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


def should_go_to_review(state: DirectorState) -> str:
    """
    条件分岐: レビューノードに進むか終了するかを判定

    Args:
        state: 現在の状態

    Returns:
        "human_review" (承認フロー) または "end" (終了)
    """
    # 承認フローが有効で、外部出力が必要な場合
    if state.get("requires_approval", False) and state.get("output_type", "none") != "none":
        logger.info(f"[Workflow] Going to human review. output_type={state.get('output_type')}")
        return "human_review"

    logger.info("[Workflow] No approval required, ending workflow")
    return "end"


def should_create_output(state: DirectorState) -> str:
    """
    条件分岐: 出力作成に進むか終了するかを判定

    Args:
        state: 現在の状態

    Returns:
        "output_creation" (出力作成) または "end" (終了)
    """
    approval_status = state.get("approval_status", "none")

    if approval_status == "approved":
        logger.info("[Workflow] Approved, proceeding to output creation")
        return "output_creation"
    elif approval_status == "rejected":
        logger.info("[Workflow] Rejected, ending workflow")
        return "end"
    elif approval_status == "pending":
        # 承認待ち状態では中断される（interruptで停止）
        logger.info("[Workflow] Pending approval, workflow will be interrupted")
        return "end"  # interruptで停止するので実際には到達しない

    return "end"


def build_director_graph() -> StateGraph:
    """
    ディレクターモードのグラフを構築

    フロー:
    START -> generator -> reflector -> (条件分岐)
                                        ├─> generator (ループ)
                                        └─> human_review -> (条件分岐)
                                                             ├─> output_creation -> END
                                                             └─> END

    Returns:
        コンパイル済みのStateGraph
    """
    # グラフを作成
    workflow = StateGraph(DirectorState)

    # ノードを追加
    workflow.add_node("generator", generator_node)
    workflow.add_node("reflector", reflector_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("output_creation", output_creation_node)

    # エッジを追加
    workflow.set_entry_point("generator")  # 開始点
    workflow.add_edge("generator", "reflector")  # generator -> reflector

    # 条件分岐を追加（reflector後）
    workflow.add_conditional_edges(
        "reflector",
        should_continue,
        {
            "generator": "generator",  # ループ
            "end": "human_review",  # 終了 → レビューへ（または直接END）
        }
    )

    # human_review後の条件分岐
    workflow.add_conditional_edges(
        "human_review",
        should_create_output,
        {
            "output_creation": "output_creation",
            "end": END,
        }
    )

    # output_creation後は終了
    workflow.add_edge("output_creation", END)

    return workflow


def build_director_graph_with_interrupt() -> StateGraph:
    """
    Human-in-the-loop用のグラフを構築（interrupt_before付き）

    human_reviewノードの前でinterruptし、承認を待つ。

    Returns:
        コンパイル済みのStateGraph（checkpointer付き）
    """
    workflow = build_director_graph()

    # human_reviewノードの前でinterrupt
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review"]
    )


# コンパイル済みのグラフをエクスポート（従来互換）
app = build_director_graph().compile()

# Human-in-the-loop用グラフ
app_with_approval = build_director_graph_with_interrupt()


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


# =============================================================================
# Human-in-the-loop ワークフロー関数
# =============================================================================

async def run_workflow_with_approval(
    task: str,
    crew_name: str,
    crew_personality: str,
    crew_image: str = "",
    output_type: OutputType = "slides",
    max_revisions: int = 3,
) -> Dict[str, Any]:
    """
    承認フロー付きでワークフローを実行

    human_reviewノードの前でinterruptし、承認待ち状態を返す。
    フロントエンドは返されたthread_idを使って承認/却下/修正を送信する。

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        crew_image: クルーの画像URL
        output_type: 出力タイプ（slides/sheets/slack/email）
        max_revisions: 最大修正回数

    Returns:
        実行結果を含む辞書:
        {
            "success": bool,
            "status": "awaiting_approval" | "completed" | "error",
            "thread_id": str,  # 再開用
            "pending_output": str,  # 承認待ちの成果物
            "score": int,
            "crew_name": str,
            "error": str | None,
        }
    """
    import uuid

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(f"[ApprovalWorkflow] Starting: task={task[:50]}..., crew={crew_name}, thread_id={thread_id}")

    try:
        # 初期状態を作成
        initial_state = create_initial_state(
            task=task,
            crew_name=crew_name,
            crew_personality=crew_personality,
            max_revisions=max_revisions,
            requires_approval=True,
            output_type=output_type,
            thread_id=thread_id,
        )

        # グラフを実行（interruptで停止する）
        final_state = None
        async for state in app_with_approval.astream(initial_state, config):
            for node_name, node_state in state.items():
                logger.info(f"[ApprovalWorkflow] Node '{node_name}' completed")
                # __interrupt__ ノードはタプルを返すのでスキップ
                if node_name == "__interrupt__" or not isinstance(node_state, dict):
                    continue
                final_state = node_state

        if final_state is None:
            raise ValueError("Workflow produced no output")

        # checkpointerから正確な状態を取得して、interruptされたかどうかを確認
        checkpoint_state = app_with_approval.get_state(config)
        is_interrupted = checkpoint_state.next and "human_review" in checkpoint_state.next

        # 承認待ち状態かどうかを確認
        approval_status = final_state.get("approval_status", "none")
        is_complete = final_state.get("is_complete", False)

        if is_interrupted or approval_status == "pending":
            # 承認待ち状態で中断された
            logger.info(f"[ApprovalWorkflow] Interrupted for approval. thread_id={thread_id}")
            return {
                "success": True,
                "status": "awaiting_approval",
                "thread_id": thread_id,
                "pending_output": final_state.get("final_result") or final_state.get("draft", ""),
                "score": final_state.get("score", 0),
                "critique": final_state.get("critique", ""),
                "revision_count": final_state.get("revision_count", 0),
                "crew_name": crew_name,
                "crew_image": crew_image,
                "output_type": output_type,
                "error": None,
            }
        else:
            # 承認不要で完了した
            logger.info(f"[ApprovalWorkflow] Completed without approval. thread_id={thread_id}")
            return {
                "success": True,
                "status": "completed",
                "thread_id": thread_id,
                "final_result": final_state.get("final_result") or final_state.get("draft", ""),
                "score": final_state.get("score", 0),
                "critique": final_state.get("critique", ""),
                "revision_count": final_state.get("revision_count", 0),
                "crew_name": crew_name,
                "crew_image": crew_image,
                "output_type": output_type,
                "error": None,
            }

    except Exception as e:
        logger.error(f"[ApprovalWorkflow] Error: {e}")
        return {
            "success": False,
            "status": "error",
            "thread_id": thread_id,
            "pending_output": None,
            "score": 0,
            "crew_name": crew_name,
            "error": str(e),
        }


async def resume_workflow_with_approval(
    thread_id: str,
    approval_status: str,
    human_feedback: Optional[str] = None,
    modified_output: Optional[str] = None,
) -> Dict[str, Any]:
    """
    承認/却下/修正を受けてワークフローを再開

    Args:
        thread_id: 中断されたワークフローのスレッドID
        approval_status: "approved" | "rejected" | "modified"
        human_feedback: 修正指示やコメント（optional）
        modified_output: 修正後の成果物（modifiedの場合）

    Returns:
        実行結果を含む辞書
    """
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(f"[ResumeWorkflow] Resuming: thread_id={thread_id}, status={approval_status}")

    try:
        # 現在の状態を取得
        current_state = app_with_approval.get_state(config)

        if current_state is None or current_state.values is None:
            raise ValueError(f"No checkpoint found for thread_id: {thread_id}")

        # 状態を更新
        update_values = {
            "approval_status": approval_status,
        }

        if human_feedback:
            update_values["human_feedback"] = human_feedback

        if modified_output:
            update_values["pending_output"] = modified_output

        # 状態を更新してグラフを再開
        app_with_approval.update_state(config, update_values)

        # ワークフローを再開
        final_state = None
        async for state in app_with_approval.astream(None, config):
            for node_name, node_state in state.items():
                logger.info(f"[ResumeWorkflow] Node '{node_name}' completed")
                # __interrupt__ ノードはタプルを返すのでスキップ
                if node_name == "__interrupt__" or not isinstance(node_state, dict):
                    continue
                final_state = node_state

        if final_state is None:
            # 状態が更新されなかった場合は現在の状態を使用
            final_state = current_state.values

        is_complete = final_state.get("is_complete", False)
        final_approval_status = final_state.get("approval_status", "none")

        logger.info(f"[ResumeWorkflow] Completed: is_complete={is_complete}, status={final_approval_status}")

        return {
            "success": True,
            "status": "completed" if is_complete else "awaiting_approval",
            "thread_id": thread_id,
            "final_result": final_state.get("final_result") or final_state.get("pending_output") or final_state.get("draft", ""),
            "score": final_state.get("score", 0),
            "approval_status": final_approval_status,
            "output_type": final_state.get("output_type", "none"),
            "error": None,
        }

    except Exception as e:
        logger.error(f"[ResumeWorkflow] Error: {e}")
        return {
            "success": False,
            "status": "error",
            "thread_id": thread_id,
            "final_result": None,
            "error": str(e),
        }


def get_workflow_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    ワークフローの現在の状態を取得

    Args:
        thread_id: ワークフローのスレッドID

    Returns:
        現在の状態を含む辞書、またはNone
    """
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = app_with_approval.get_state(config)
        if state is None or state.values is None:
            return None

        return dict(state.values)
    except Exception as e:
        logger.error(f"[GetState] Error: {e}")
        return None

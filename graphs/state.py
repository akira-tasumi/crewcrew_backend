"""
ディレクターモードの状態定義

LangGraphで使用する状態（State）をTypedDictで定義
"""

from typing import Annotated, List, Optional, Literal
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# 承認ステータスの型定義
ApprovalStatus = Literal["none", "pending", "approved", "rejected", "modified"]

# 出力タイプの型定義
OutputType = Literal["slides", "sheets", "slack", "email", "none"]


class DirectorState(TypedDict):
    """
    ディレクターモードのグラフ状態

    Attributes:
        task: ユーザーからの指示内容
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        draft: クルーが作成した成果物（作文・修正版）
        critique: ディレクターからのフィードバック
        score: 品質スコア（0-100点）
        revision_count: 現在の修正回数
        max_revisions: 最大修正回数（デフォルト3）
        messages: 会話履歴（LangChain形式）
        final_result: 最終成果物
        is_complete: 完了フラグ

        # Human-in-the-loop用フィールド
        requires_approval: 外部出力前に承認が必要かどうか
        approval_status: 承認ステータス (none/pending/approved/rejected/modified)
        pending_output: 承認待ちの成果物
        output_type: 出力タイプ (slides/sheets/slack/email/none)
        human_feedback: 人間からの修正指示やコメント
        thread_id: LangGraph再開用のスレッドID
        approval_request_id: DBに保存された承認リクエストID
    """
    task: str
    crew_name: str
    crew_personality: str
    draft: str
    critique: str
    score: int
    revision_count: int
    max_revisions: int
    messages: Annotated[List[BaseMessage], add_messages]
    final_result: Optional[str]
    is_complete: bool

    # Human-in-the-loop用
    requires_approval: bool
    approval_status: ApprovalStatus
    pending_output: Optional[str]
    output_type: OutputType
    human_feedback: Optional[str]
    thread_id: Optional[str]
    approval_request_id: Optional[int]


def create_initial_state(
    task: str,
    crew_name: str,
    crew_personality: str,
    max_revisions: int = 3,
    requires_approval: bool = False,
    output_type: OutputType = "none",
    thread_id: Optional[str] = None,
) -> DirectorState:
    """
    初期状態を生成

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        max_revisions: 最大修正回数
        requires_approval: 承認フローを有効にするか
        output_type: 出力タイプ（slides/sheets/slack/email/none）
        thread_id: スレッドID（再開時に指定）

    Returns:
        DirectorState: 初期化された状態
    """
    import uuid

    return DirectorState(
        task=task,
        crew_name=crew_name,
        crew_personality=crew_personality,
        draft="",
        critique="",
        score=0,
        revision_count=0,
        max_revisions=max_revisions,
        messages=[],
        final_result=None,
        is_complete=False,
        # Human-in-the-loop用
        requires_approval=requires_approval,
        approval_status="none",
        pending_output=None,
        output_type=output_type,
        human_feedback=None,
        thread_id=thread_id or str(uuid.uuid4()),
        approval_request_id=None,
    )

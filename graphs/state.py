"""
ディレクターモードの状態定義

LangGraphで使用する状態（State）をTypedDictで定義
"""

from typing import Annotated, List, Optional
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


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


def create_initial_state(
    task: str,
    crew_name: str,
    crew_personality: str,
    max_revisions: int = 3,
) -> DirectorState:
    """
    初期状態を生成

    Args:
        task: ユーザーからの指示
        crew_name: 担当クルーの名前
        crew_personality: クルーの性格設定
        max_revisions: 最大修正回数

    Returns:
        DirectorState: 初期化された状態
    """
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
    )

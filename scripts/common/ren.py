"""
レン - 動画制作専門スタッフ（HyperFramesレンダリングエンジニア）

台本の中身（何を言うか）には関与しない。プロデューサー（アカリ等）から
「テンプレ名・変数」を受け取り、レンダリングをdispatchしてDiscordに
結果を通知する「作る」ことだけを担当する共通モジュール。
今後もる等の別エージェントからも同じ窓口として呼び出せるようにしてある。
"""
from scripts.common.hyperframes_runner import dispatch_render
from scripts.common.discord_notify import notify


def produce_video(template_name: str, variables: dict, agent: str, workflow_id: str, source: str = "") -> dict:
    """プロデューサーから受け取った台本をレンダリングにdispatchする。

    agent: 誰の動画か（"HAL" / "SUNAKUN" 等）。通知文言とdispatchのstaffタグに使う。
    workflow_id: 呼び出し元が発行した追跡ID。
    """
    result = dispatch_render(
        template_name=template_name,
        variables=variables,
        staff=agent,
        source=source or f"ren:{workflow_id}",
    )

    if result.get("ok"):
        notify(
            f"🎬 **レン: 動画レンダリング開始** ({workflow_id})\n"
            f"担当: {agent}\n"
            f"テンプレ: {template_name}\n"
            f"→ WF-07 完了後にMP4をDiscord通知"
        )
    else:
        notify(
            f"❌ **レン: 動画dispatch失敗** ({agent}/{workflow_id})\n"
            f"{result.get('message') or result.get('error')}"
        )
    return result

"""
投稿テキスト生成AIに実際の現在日時(JST)を伝えるための共通ヘルパー。

背景: nana.py の画像生成には実際の季節・時間帯を反映する仕組み
(_current_time_context)を入れていたが、投稿文章を書くYUKI_PROMPT/
TAKUMI_PROMPT側には反映しておらず、7月なのに「春コーデ」等の季節外れの
話題が生成される事故が発生した（社長指摘、2026-07-08）。画像・文章の
両方に同じ実日時を反映させるため、テキスト生成用に独立して用意する。
"""
import datetime


def current_season_text() -> str:
    """テキスト生成プロンプトに差し込む、実際の日付・季節の指示文（日本語）を返す。"""
    now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    month = now_jst.month

    if month in (3, 4, 5):
        season = "春"
    elif month in (6, 7, 8):
        season = "夏"
    elif month in (9, 10, 11):
        season = "秋"
    else:
        season = "冬"

    return (
        f"（本日は{now_jst.strftime('%Y年%m月%d日')}、季節は{season}です。"
        "服装・コーデ・気候・季節行事などの話題は、必ずこの実際の季節に"
        "合わせてください。季節外れの内容（真夏に厚手のニット、真冬に半袖等）"
        "は絶対に書かないでください。）"
    )

import json
from .claude_client import call_claude

MAMORU_PROMPT = """あなたはKCS合同会社のコンプライアンス担当「マモル」です。
前段のスタッフ（ユキ、タクミなど）が作成したSNS投稿テキストを厳格に審査する、ブランドセーフティの最終防波堤です。

【審査ルール】
以下の観点から、入力されたテキストを論理的・多角的に審査してください。
1. 差別的、攻撃的、または他社を批判する表現が含まれていないか。
2. MIMOMIブランドの価値を損なう、または下品すぎる表現がないか（※アダルト担当のエマの投稿の場合は、アカウントの性質を考慮し、ガイドライン違反にならないギリギリのラインかを判定）。
3. 情報商材のような過度な煽りや、スパムと判定されるリスクがないか。

【出力ルール（JSON形式厳守）】
問題がなければ以下を出力:
{"status": "approved", "reason": "問題なし"}

問題がある場合は以下を出力:
{"status": "rejected", "reason": "〇〇の表現がリスクとなるため", "fixed_text": "修正案"}"""


def review(post_text: str) -> dict:
    result = call_claude(MAMORU_PROMPT, post_text)
    # JSONブロックを抽出
    text = result.strip()
    if "```" in text:
        text = text.split("```")[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    # Claudeの応答にJSON本体の後へ余分な説明文が続くことがあるため、
    # json.loads()（"Extra data"エラーで落ちる）ではなくraw_decode()で
    # 先頭の有効なJSONオブジェクトだけを取り出し、末尾の余分な文字列は無視する。
    obj, _ = json.JSONDecoder().raw_decode(text)
    return obj

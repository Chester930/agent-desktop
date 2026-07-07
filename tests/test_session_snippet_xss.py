"""T44: session 搜尋回傳的 snippet 會經前端 [innerHTML] 直接渲染（略過
markdown pipe 的 DOMPurify），但 FTS5 snippet()／LIKE fallback 組出來的
snippet 原本都是 search_text 或搜尋關鍵字的原始子字串，未跳脫就直接拼接
<mark> 標籤 —— 對話內容或使用者自己輸入的搜尋關鍵字只要含有 HTML 字元，
就會被當成真的 HTML 渲染。"""
import json

import pytest

pytestmark = pytest.mark.asyncio


def _write_session_file(tmp_claude_home, project_slug: str, session_id: str, user_text: str):
    proj_dir = tmp_claude_home / "projects" / project_slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    session_file = proj_dir / f"{session_id}.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"content": user_text}, "cwd": "/tmp/proj"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}),
    ]
    session_file.write_text("\n".join(lines), encoding="utf-8")
    return session_file


class TestSessionSnippetEscaping:
    async def test_fts5_snippet_path_escapes_html_in_content(self, client, tmp_claude_home):
        import main
        main.CLAUDE_HOME = tmp_claude_home
        _write_session_file(
            tmp_claude_home, "proj1", "sess-xss-1",
            "searchterm<script>xss</script>",
        )

        resp = await client.get("/api/sessions?q=searchterm")
        assert resp.status == 200
        body = await resp.json()
        matches = [s for s in body["sessions"] if s["id"] == "sess-xss-1"]
        assert matches, f"expected session not found in {body['sessions']}"
        snippet = matches[0]["snippet"]

        # FTS5's snippet() window is narrow (trigram tokenizer -> ~12 trigrams),
        # so the full closing tag may get truncated by the "…" ellipsis — what
        # matters is that no raw "<" ever survives unescaped.
        assert "<script>" not in snippet
        assert "&lt;scr" in snippet
        # the intentional highlight marker must still survive as real HTML
        assert "<mark>" in snippet and "</mark>" in snippet

    async def test_like_fallback_path_escapes_html_in_query_and_content(self, client, tmp_claude_home):
        """短查詢（<3 字元）走 LIKE fallback，且會把查詢字串本身包進 <mark> —
        這裡驗證『使用者自己在搜尋框打的字』也會被跳脫（反射型 XSS 的來源）。"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        _write_session_file(
            tmp_claude_home, "proj2", "sess-xss-2",
            "ab <img src=x onerror=alert(1)>",
        )

        resp = await client.get("/api/sessions?q=ab")
        assert resp.status == 200
        body = await resp.json()
        matches = [s for s in body["sessions"] if s["id"] == "sess-xss-2"]
        assert matches, f"expected session not found in {body['sessions']}"
        snippet = matches[0]["snippet"]

        assert "<img" not in snippet
        assert "&lt;img" in snippet
        assert "<mark>ab</mark>" in snippet

    async def test_no_query_snippet_still_escaped(self, client, tmp_claude_home):
        import main
        main.CLAUDE_HOME = tmp_claude_home
        _write_session_file(
            tmp_claude_home, "proj3", "sess-xss-3",
            "<b>bold html</b> in the very first message",
        )

        resp = await client.get("/api/sessions")
        assert resp.status == 200
        body = await resp.json()
        matches = [s for s in body["sessions"] if s["id"] == "sess-xss-3"]
        assert matches, f"expected session not found in {body['sessions']}"
        snippet = matches[0]["snippet"]

        assert "<b>" not in snippet
        assert "&lt;b&gt;" in snippet

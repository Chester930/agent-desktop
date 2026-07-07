"""T30: handle_restore 用 zf.read() 把整個 zip 項目解壓進記憶體，原本沒有
任何大小檢查。上傳的 zip 本身雖受 client_max_size（~20MB）限制，但高度可
壓縮的內容（zip bomb）解壓後可能遠超過這個大小。"""
import io
import zipfile

import pytest

pytestmark = pytest.mark.asyncio


def _make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def _post_restore(client, zip_bytes: bytes):
    return await client.post(
        "/api/restore",
        data={"file": io.BytesIO(zip_bytes)},
    )


class TestRestoreZipBomb:
    async def test_normal_small_backup_succeeds(self, client, tmp_claude_home):
        zip_bytes = _make_zip({
            "soul.md": "hello soul",
            "schedules.json": "[]",
            "memory/note.md": "a memory note",
        })
        resp = await _post_restore(client, zip_bytes)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    async def test_single_entry_over_per_entry_cap_rejected(self, client, tmp_claude_home):
        # highly compressible: compresses to a few KB but file_size metadata is ~25MB
        huge_text = "A" * (25 * 1024 * 1024)
        zip_bytes = _make_zip({"memory/bomb.md": huge_text})
        resp = await _post_restore(client, zip_bytes)
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    async def test_many_entries_over_total_cap_rejected(self, client, tmp_claude_home):
        # each individually under the 20MB per-entry cap, but 4 x 15MB > 50MB total cap
        chunk = "B" * (15 * 1024 * 1024)
        zip_bytes = _make_zip({
            "memory/a.md": chunk,
            "memory/b.md": chunk,
            "memory/c.md": chunk,
            "memory/d.md": chunk,
        })
        resp = await _post_restore(client, zip_bytes)
        assert resp.status == 400

    async def test_rejected_restore_does_not_write_any_files(self, client, tmp_claude_home):
        import main
        mem_dir = tmp_claude_home / "memory"
        main.CLAUDE_HOME = tmp_claude_home

        huge_text = "A" * (25 * 1024 * 1024)
        zip_bytes = _make_zip({"memory/bomb.md": huge_text, "soul.md": "should not land"})
        resp = await _post_restore(client, zip_bytes)
        assert resp.status == 400
        assert not (mem_dir / "bomb.md").exists()

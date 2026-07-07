"""T33: agency_agents_importer.py 的 div_key 來自釘死版本的上游 GitHub repo
（divisions.json），會被拿去拼 team_id/team_file 檔名。防禦深度：即使上游
repo 或這支 script 之後被改成信任程度較低的來源，一個惡意的 key（例如
"../../../etc"）也不該造成路徑穿越寫入。"""
import agency_agents_importer as importer


def test_is_safe_id_rejects_path_traversal():
    assert importer._is_safe_id("../../../etc") is False
    assert importer._is_safe_id("a/b") is False
    assert importer._is_safe_id("a\\b") is False
    assert importer._is_safe_id("") is False


def test_is_safe_id_allows_normal_division_key():
    assert importer._is_safe_id("engineering") is True
    assert importer._is_safe_id("marketing-ops") is True

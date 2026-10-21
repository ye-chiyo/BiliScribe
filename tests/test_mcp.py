# -*- coding: utf-8 -*-
import asyncio
import json
from pathlib import Path

import pytest

mcp_sdk = pytest.importorskip("mcp")
from biliscribe import mcp_server


def test_mcp_registers_expected_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "get_capabilities", "extract_subtitles", "transcribe_local_audio"
    }


def test_capabilities_are_available_over_mcp():
    response = asyncio.run(mcp_server.mcp.call_tool("get_capabilities", {}))
    payload = json.loads(response[0].text)
    assert payload["bilibili_audio_download"] is False
    assert "credentials_present" in payload
    assert payload["login_status"] == "not_checked"
    assert "logged_in" not in payload
    assert "cuda_available" in payload
    assert payload["max_audio_mb"] > 0


def test_export_uses_a_new_name_instead_of_overwriting(tmp_path):
    first = mcp_server._write_export(tmp_path, "meeting", "txt", "first", "utf-8")
    second = mcp_server._write_export(tmp_path, "meeting", "txt", "second", "utf-8")

    assert first.name == "meeting.asr.txt"
    assert second.name == "meeting.asr (2).txt"
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"


def test_audio_allowlist_checks_resolved_file(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    denied = tmp_path / "elsewhere"
    allowed.mkdir()
    denied.mkdir()
    audio = allowed / "voice.mp3"
    audio.write_bytes(b"test")
    denied_audio = denied / "voice.mp3"
    denied_audio.write_bytes(b"test")
    monkeypatch.setenv("BILISCRIBE_MCP_AUDIO_DIRS", str(allowed))
    assert mcp_server._audio_path(str(audio)) == audio.resolve()
    with pytest.raises(ValueError, match="不在允许目录"):
        mcp_server._audio_path(str(denied_audio))

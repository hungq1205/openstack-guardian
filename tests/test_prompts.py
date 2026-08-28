"""Tests for the assemble_log prompt -- pure, algorithmic
evidence gathering (fetch recent logs, check the latest line against the
failure-pattern knowledge base) with no procedural instructions to the model;
that lives in the investigate-incident agent skill instead. Built and tested
standalone here on a bare Server; wired onto the real cmp-admin server at
cutover (see test_cmp_admin_mcp.py once that lands).
"""

from __future__ import annotations

import pytest
from mcp.server.lowlevel import Server

from mcp_servers.prompts import register_prompts


def _prompt_server() -> Server:
    server: Server = Server("prompts-under-test")
    register_prompts(server)
    return server


@pytest.mark.asyncio
async def test_list_prompts_declares_assemble_log() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        result = await session.list_prompts()

    assert [p.name for p in result.prompts] == ["assemble_log"]
    prompt = result.prompts[0]
    assert prompt.arguments is not None
    arguments_by_name = {a.name: a for a in prompt.arguments}
    assert arguments_by_name.keys() == {"server_id", "suspected_cause", "investigation_hint"}
    assert arguments_by_name["server_id"].required is True
    assert arguments_by_name["suspected_cause"].required is False
    assert arguments_by_name["investigation_hint"].required is False


@pytest.mark.asyncio
async def test_get_prompt_fills_in_server_id_only() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        result = await session.get_prompt("assemble_log", {"server_id": "srv-123"})

    assert len(result.messages) == 1
    text = result.messages[0].content.text
    assert "srv-123" in text
    assert "Administrator's suspected cause" not in text
    assert "Investigation hint:" not in text


@pytest.mark.asyncio
async def test_get_prompt_fills_in_optional_arguments_when_given() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        result = await session.get_prompt(
            "assemble_log",
            {
                "server_id": "srv-456",
                "suspected_cause": "volume state drift",
                "investigation_hint": "started failing around 14:00 UTC",
            },
        )

    text = result.messages[0].content.text
    assert "srv-456" in text
    assert "volume state drift" in text
    assert "started failing around 14:00 UTC" in text


@pytest.mark.asyncio
async def test_get_prompt_requires_server_id() -> None:
    from mcp import McpError
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        with pytest.raises(McpError):
            await session.get_prompt("assemble_log", {})


@pytest.mark.asyncio
async def test_get_prompt_rejects_unknown_prompt_name() -> None:
    from mcp import McpError
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        with pytest.raises(McpError):
            await session.get_prompt("not_a_real_prompt", {})


@pytest.mark.asyncio
async def test_prompt_text_mentions_the_tools_it_ran_and_hands_off_to_the_skill() -> None:
    """This prompt only gathers evidence -- pins that its text names the two
    lookups it performed algorithmically (search_logs, search_failure_patterns)
    and explicitly defers everything else (interpretation, planning,
    approval, execution, escalation, reporting) rather than spelling out that
    procedure itself."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        result = await session.get_prompt("assemble_log", {"server_id": "srv-789"})

    text = result.messages[0].content.text
    assert "search_logs" in text
    assert "search_failure_patterns" in text
    assert "incident-response skill" in text.lower()


@pytest.mark.asyncio
async def test_prompt_text_warns_that_retrieved_content_is_untrusted() -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_prompt_server()) as session:
        result = await session.get_prompt("assemble_log", {"server_id": "srv-789"})

    text = result.messages[0].content.text
    assert "untrusted data" in text.lower()


@pytest.mark.asyncio
async def test_prompt_reports_a_knowledge_base_match_algorithmically() -> None:
    """When the most recent fetched log line matches a curated failure
    pattern, the match (cause, instruction, source_reference) is inlined
    into the prompt text directly -- the model is never asked to call
    search_failure_patterns itself to discover this."""
    import mcp_servers.prompts.assemble_log as module

    matching_line = (
        "ERROR celery.server_creator server_creator_execution "
        "11111111-1111-1111-1111-111111111111 Error generating server "
        "22222222-2222-2222-2222-222222222222: Unable to establish connection to "
        "https://10.0.0.1:8774/v2.1/servers: ('Connection aborted.', "
        "RemoteDisconnected('Remote end closed connection without response')) "
        "@timestamp:Jan 1, 2026 @ 00:00:00.000."
    )

    def _fake_search_logs(server_id: str) -> dict:
        return {"logs": [{"timestamp": "2026-01-01T00:00:00", "message": matching_line}]}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(module, "_search_logs", _fake_search_logs)
        result = module.render({"server_id": "srv-789"})

    text = result.messages[0].content.text
    assert "connection_aborted_transient" in text
    assert matching_line in text


@pytest.mark.asyncio
async def test_prompt_reports_no_match_when_nothing_in_the_kb_fits() -> None:
    import mcp_servers.prompts.assemble_log as module

    def _fake_search_logs(server_id: str) -> dict:
        return {"logs": [{"timestamp": "2026-01-01T00:00:00", "message": "totally unrelated line"}]}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(module, "_search_logs", _fake_search_logs)
        result = module.render({"server_id": "srv-789"})

    text = result.messages[0].content.text
    assert "No knowledge-base pattern matched" in text

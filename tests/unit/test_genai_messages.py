"""gen_ai.*.messages follow the OTel GenAI parts schema (semantic-conventions-genai
model/gen-ai/gen-ai-{input,output}-messages.json, gen-ai-system-instructions.json)."""

import json

from hermes_otel.genai_messages import (
    genai_messages_json,
    output_message_json,
    single_message_json,
    system_instructions_json,
    to_genai_messages,
)


def _loads(value):
    assert value is not None
    return json.loads(value)


def test_text_messages_become_text_parts():
    out = _loads(
        genai_messages_json(
            [
                {"role": "system", "content": "be Noir"},
                {"role": "user", "content": "hi"},
            ]
        )
    )
    assert out == [
        {"role": "system", "parts": [{"type": "text", "content": "be Noir"}]},
        {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
    ]


def test_developer_role_maps_to_system():
    out = _loads(genai_messages_json([{"role": "developer", "content": "x"}]))
    assert out[0]["role"] == "system"


def test_assistant_tool_calls_and_reasoning():
    out = _loads(
        genai_messages_json(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "need a tool",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": '{"q": "weather"}'},
                        }
                    ],
                }
            ]
        )
    )
    assert out == [
        {
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "need a tool"},
                {
                    "type": "tool_call",
                    "name": "web_search",
                    "id": "call_1",
                    "arguments": {"q": "weather"},
                },
            ],
        }
    ]


def test_unparseable_tool_arguments_stay_a_string():
    out = _loads(
        output_message_json(None, [{"id": "c", "function": {"name": "f", "arguments": "not json"}}])
    )
    assert out[0]["parts"][0]["arguments"] == "not json"


def test_tool_result_becomes_tool_call_response():
    out = _loads(
        genai_messages_json([{"role": "tool", "tool_call_id": "call_1", "content": "sunny"}])
    )
    assert out == [
        {
            "role": "tool",
            "parts": [
                {"type": "tool_call_response", "response": "sunny", "id": "call_1"},
            ],
        }
    ]


def test_content_part_lists():
    out = _loads(
        genai_messages_json(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                        {"type": "custom_part", "x": 1},
                    ],
                }
            ]
        )
    )
    assert out[0]["parts"] == [
        {"type": "text", "content": "look"},
        {"type": "uri", "modality": "image", "uri": "https://example.test/a.png"},
        {"type": "custom_part", "x": 1},
    ]


def test_empty_messages_are_dropped_and_empty_lists_give_none():
    assert to_genai_messages(None) is None
    assert to_genai_messages([]) is None
    assert to_genai_messages([{"role": "assistant", "content": ""}]) is None


def test_sdk_objects_are_read_like_dicts():
    class Message:
        role = "assistant"
        content = "from an object"
        tool_calls = None
        name = None
        reasoning_content = None
        reasoning = None

    out = _loads(genai_messages_json([Message()]))
    assert out == [{"role": "assistant", "parts": [{"type": "text", "content": "from an object"}]}]


def test_single_and_output_messages():
    assert _loads(single_message_json("user", "hi")) == [
        {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
    ]
    assert single_message_json("user", "") is None
    assert _loads(output_message_json("done", None, "stop")) == [
        {
            "role": "assistant",
            "parts": [{"type": "text", "content": "done"}],
            "finish_reason": "stop",
        },
    ]
    assert output_message_json(None, None) is None


def test_system_instructions_are_text_parts():
    assert _loads(system_instructions_json("You are Noir")) == [
        {"type": "text", "content": "You are Noir"}
    ]
    assert system_instructions_json("") is None


def test_edge_shapes_in_content_part_lists():
    out = _loads(
        genai_messages_json(
            [
                {
                    "role": "user",
                    "content": [
                        "bare string item",
                        {"type": "input_text", "text": "responses-api text"},
                        {"type": "image_url", "image_url": "https://example.test/b.png"},
                        {"type": "image_url", "image_url": {}},
                        {"type": "thinking", "thinking": "hmm"},
                        {"type": "reasoning"},
                        {"no_type": True},
                    ],
                }
            ]
        )
    )
    assert out[0]["parts"] == [
        {"type": "text", "content": "bare string item"},
        {"type": "text", "content": "responses-api text"},
        {"type": "uri", "modality": "image", "uri": "https://example.test/b.png"},
        {"type": "reasoning", "content": "hmm"},
        {"type": "text", "content": "{'no_type': True}"},
    ]


def test_non_string_scalar_content_becomes_text():
    out = _loads(genai_messages_json([{"role": "user", "content": 42}]))
    assert out[0]["parts"] == [{"type": "text", "content": "42"}]


def test_tool_call_variants():
    out = _loads(
        output_message_json(
            None,
            [
                {"function": {"arguments": "{}"}},  # no name: skipped
                {"name": "flat", "arguments": {"already": "parsed"}},  # flat shape, dict arguments
                {"function": {"name": "bare"}},  # no id, no arguments
            ],
        )
    )
    assert out[0]["parts"] == [
        {"type": "tool_call", "name": "flat", "arguments": {"already": "parsed"}},
        {"type": "tool_call", "name": "bare"},
    ]
    assert output_message_json(None, "not a list") is None


def test_tool_result_with_part_list_and_name_handling():
    out = _loads(
        genai_messages_json(
            [
                {
                    "role": "tool",
                    "tool_call_id": "c",
                    "name": "ls",
                    "content": [
                        {"type": "text", "text": "a"},
                        {"type": "text", "text": "b"},
                    ],
                },
                {"role": "assistant", "name": "noir", "content": "hi"},
            ]
        )
    )
    assert out[0] == {
        "role": "tool",
        "parts": [{"type": "tool_call_response", "response": "a b", "id": "c"}],
    }
    assert out[1]["name"] == "noir"


def test_non_list_input_becomes_one_user_message():
    assert _loads(genai_messages_json("just text")) == [
        {"role": "user", "parts": [{"type": "text", "content": "just text"}]},
    ]
    assert genai_messages_json(None) is None


def test_unserialisable_payloads_give_none():
    circular = {}
    circular["self"] = circular
    call = {"function": {"name": "f", "arguments": circular}}
    assert genai_messages_json([{"role": "assistant", "tool_calls": [call]}]) is None
    assert output_message_json(None, [call]) is None


def test_anthropic_tool_use_and_tool_result_blocks():
    """api_mode anthropic_messages: tool calls and results are content blocks."""
    out = to_genai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Listing."},
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "terminal",
                        "input": {"command": "ls"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": [{"type": "text", "text": "a.txt"}],
                    }
                ],
            },
        ]
    )
    assert out == [
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "content": "Listing."},
                {
                    "type": "tool_call",
                    "name": "terminal",
                    "id": "tu1",
                    "arguments": {"command": "ls"},
                },
            ],
        },
        {
            "role": "user",
            "parts": [{"type": "tool_call_response", "response": "a.txt", "id": "tu1"}],
        },
    ]


def test_responses_api_input_items():
    """api_mode codex_responses: request_messages is the Responses ``input``
    list, where tool rounds are items without a role."""
    out = to_genai_messages(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "use ls"}]},
            {"type": "reasoning", "summary": [], "encrypted_content": "opaque"},
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "terminal",
                "arguments": '{"command": "ls"}',
            },
            {"type": "function_call", "call_id": "c2", "name": ""},
            {"type": "function_call_output", "call_id": "c1", "output": "a.txt"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "One file."}],
            },
        ]
    )
    assert out == [
        {"role": "user", "parts": [{"type": "text", "content": "list files"}]},
        {"role": "assistant", "parts": [{"type": "reasoning", "content": "use ls"}]},
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "name": "terminal",
                    "id": "c1",
                    "arguments": {"command": "ls"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_call_response", "response": "a.txt", "id": "c1"}],
        },
        {"role": "assistant", "parts": [{"type": "text", "content": "One file."}]},
    ]

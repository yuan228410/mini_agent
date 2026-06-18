"""LLM provider router.

Keeps provider selection outside individual provider adapters so OpenAI and
Anthropic code paths do not mutate each other's HTTP session headers or miss
provider-specific arguments.
"""


def chat(messages, tools=None, ctx=None):
    from .base import get_api_mode

    if get_api_mode(ctx) == "anthropic":
        from .anthropic import chat as anthropic_chat

        return anthropic_chat(messages, tools, ctx=ctx)

    from .openai import chat as openai_chat

    return openai_chat(messages, tools, ctx=ctx)


def chat_stream(messages, tools=None, ctx=None, abort_event=None):
    from .base import get_api_mode

    if get_api_mode(ctx) == "anthropic":
        from .anthropic import chat_stream as anthropic_stream

        yield from anthropic_stream(messages, tools, ctx=ctx, abort_event=abort_event)
        return

    from .openai import chat_stream as openai_stream

    yield from openai_stream(messages, tools, ctx=ctx, abort_event=abort_event)

from compactor import Compactor
from config import PROJECT_DIR
from context import ContextBuilder
from llm import chat
from memory import MemoryStore
from skills import SkillLoader
from tools import handle_tool_calls, register

SKILL_LOADER = SkillLoader(PROJECT_DIR / "skills")


def main():
    register(SKILL_LOADER)
    store = MemoryStore(PROJECT_DIR / "memory_data")
    compactor = Compactor(store)
    ctx = ContextBuilder(PROJECT_DIR)

    system_prompt = ctx.build(memory_store=store, skill_loader=SKILL_LOADER)
    messages = [{"role": "system", "content": system_prompt}]

    unarchived = store.load_unarchived()
    if unarchived:
        messages.extend(unarchived)
        print(f"[恢复] {len(unarchived)} 条历史消息")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break

        messages.append({"role": "user", "content": user_input})
        store.append("user", user_input)

        while True:
            msg = chat(messages)
            if not msg or "tool_calls" not in msg:
                break
            handle_tool_calls(msg, messages)

        if msg:
            print("Assistant:", msg["content"])
            messages.append({"role": "assistant", "content": msg["content"]})
            store.append("assistant", msg["content"])

        if compactor.should_compact(messages):
            messages = compactor.compact(chat, messages)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
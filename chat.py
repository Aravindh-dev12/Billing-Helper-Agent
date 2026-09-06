import sys
import argparse
from pathlib import Path
from agent.sandbox_client import SandboxClient
from agent.orchestrator import BillingAgent

def human_approval_prompt(prompt_text: str) -> bool:
    try:
        ans = input(prompt_text).strip().lower()
        return ans in ["y", "yes", "true", "1"]
    except (EOFError, KeyboardInterrupt):
        return False

def main():
    parser = argparse.ArgumentParser(description="Ferrowave Billing Helper CLI")
    subparsers = parser.add_subparsers(dest="command")

    chat_parser = subparsers.add_parser("chat", help="Start chat session with customer")
    chat_parser.add_argument("--email", type=str, required=True, help="Customer email address")
    chat_parser.add_argument("--sandbox", type=str, default="http://127.0.0.1:8787", help="Billing sandbox URL")
    chat_parser.add_argument("--trace", action="store_true", help="Print tool calls, arguments, and results")

    args = parser.parse_args()

    if args.command != "chat":
        # Fallback support if user runs without explicit 'chat' subcommand: python chat.py --email ...
        if "--email" in sys.argv:
            # Re-parse treating arguments as chat arguments
            remaining = [arg for arg in sys.argv[1:] if arg != "chat"]
            chat_parser_fallback = argparse.ArgumentParser()
            chat_parser_fallback.add_argument("--email", type=str, required=True)
            chat_parser_fallback.add_argument("--sandbox", type=str, default="http://127.0.0.1:8787")
            chat_parser_fallback.add_argument("--trace", action="store_true")
            args = chat_parser_fallback.parse_args(remaining)
        else:
            parser.print_help()
            sys.exit(1)

    sandbox = SandboxClient(base_url=args.sandbox, trace=args.trace)
    agent = BillingAgent(sandbox, trace=args.trace)

    print("=" * 65)
    print(" FERROWAVE PULSE BILLING HELPER CLI")
    print(f" Customer: {args.email} | Sandbox: {args.sandbox} | Trace: {args.trace}")
    print(" Type 'exit' or 'quit' to end session.")
    print("=" * 65)

    initial_reply = agent.start_conversation(args.email)
    print(f"\nAgent: {initial_reply}\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("\nEnding session...")
                break

            response = agent.handle_message(user_input, human_approval_callback=human_approval_prompt)
            print(f"\nAgent: {response}\n")
        except (KeyboardInterrupt, EOFError):
            print("\nSession interrupted.")
            break

    transcripts_dir = Path(__file__).resolve().parent / "transcripts"
    path = agent.save_transcript(transcripts_dir)
    print(f"Transcript saved to: {path}")

if __name__ == "__main__":
    main()

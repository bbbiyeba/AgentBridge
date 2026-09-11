import argparse
import sys

from .config import load_config
from .mailboard import Mailboard
from .orchestrator import Orchestrator


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    mailboard = Mailboard(config.settings.mailboard_file)
    if args.task:
        mailboard.set_task(args.task)
    if not mailboard.task:
        print(
            'No task set. Pass --task "..." or set one via the web UI first.',
            file=sys.stderr,
        )
        sys.exit(1)

    orchestrator = Orchestrator(config, mailboard)
    results = orchestrator.run(max_turns=args.turns, start_agent=args.agent)
    for entry in results:
        print(f"[{entry['id']}] {entry['agent']}: {entry['message']}")
        for f in entry["files"]:
            print(f"    {f['action']}: {f['path']}")


def cmd_web(args: argparse.Namespace) -> None:
    from .web.app import create_app

    config = load_config(args.config)
    app = create_app(config)
    app.run(host=args.host, port=args.port, debug=args.debug)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentbridge")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run turns from the terminal")
    run_p.add_argument("--task", help="Set/replace the task before running")
    run_p.add_argument(
        "--agent", help="Agent to start with (default: mailboard's next_agent, else the first configured agent)"
    )
    run_p.add_argument("--turns", type=int, default=None, help="Max turns to run (default: settings.max_turns)")
    run_p.set_defaults(func=cmd_run)

    web_p = sub.add_parser("web", help="Start the local web UI")
    web_p.add_argument("--host", default="127.0.0.1")
    web_p.add_argument("--port", type=int, default=5050)
    web_p.add_argument("--debug", action="store_true")
    web_p.set_defaults(func=cmd_web)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

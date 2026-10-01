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


def cmd_worker(args: argparse.Namespace) -> None:
    from .worker import WorkerError, run_one_turn, watch

    try:
        if args.once:
            entry = run_one_turn(args.server, args.agent)
            print(f"[{entry['id']}] ran turn: {entry['message']}")
            for f in entry["files"]:
                print(f"    {f['action']}: {f['path']}")
        else:
            watch(args.server, args.agent, args.poll_interval)
    except WorkerError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_integrations(args: argparse.Namespace) -> None:
    from .integrations import IntegrationError, all_integrations

    integrations = all_integrations()
    if args.env_template:
        # A ready-to-fill .env / Vercel env list generated from each
        # integration's declared settings, so it can't drift from the code.
        for integ in integrations:
            print(f"# --- {integ.title} ---")
            for s in integ.settings:
                req = "required" if s.required else f"optional, default {s.default!r}" if s.default else "optional"
                print(f"# {s.description} ({req})")
                print(f"{s.env}=")
            print()
        return

    failed = False
    for integ in integrations:
        missing = integ.missing()
        state = "configured" if not missing else "NOT configured"
        print(f"{integ.title} [{integ.name}]: {state}")
        for s in integ.settings:
            value = integ.get(s.env)
            if value:
                shown = "(set)" if s.secret else value
            else:
                shown = "MISSING" if s.required else "(unset, optional)"
            print(f"    {s.env:<30} {shown}")
        if args.check and not missing:
            try:
                print(f"    live check: OK - {integ.check()}")
            except IntegrationError as e:
                failed = True
                print(f"    live check: FAILED - {e.message}")
                if e.detail:
                    print(f"      detail: {e.detail}")
        print()
    if failed:
        sys.exit(1)


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

    worker_p = sub.add_parser(
        "worker",
        help="Run turns for one agent locally, using YOUR local API key — it never touches the hosted server",
    )
    worker_p.add_argument("--server", required=True, help="Base URL of the hosted AgentBridge server, e.g. https://your-host")
    worker_p.add_argument("--agent", required=True, help="Name of the agent (as configured on the server) this worker acts as")
    worker_p.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between checks for whose turn it is (default: 5)")
    worker_p.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one turn immediately, regardless of whose turn it currently is, then exit",
    )
    worker_p.set_defaults(func=cmd_worker)

    integ_p = sub.add_parser(
        "integrations", help="Show which third-party integrations are configured (reads env vars)"
    )
    integ_p.add_argument("--check", action="store_true", help="Also make a live API call to verify each one")
    integ_p.add_argument(
        "--env-template", action="store_true", help="Print every integration env var as a fill-in template"
    )
    integ_p.set_defaults(func=cmd_integrations)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

"""CLI for the DHLS artifact toolkit."""

import argparse
from dhls.core.generate import build_flow, build_report, build_ai_flow
from dhls.core.validate import check_repository


def main(argv=None):
    parser = argparse.ArgumentParser(description="DHLS research artifact toolkit")
    parser.add_argument("command", choices=["check", "report", "flow", "ai-flow", "all"])
    args = parser.parse_args(argv)
    if args.command in ("report", "all"):
        build_report()
    if args.command in ("flow", "all"):
        build_flow()
    if args.command in ("ai-flow", "all"):
        build_ai_flow()
    if args.command in ("check", "all"):
        errors = check_repository()
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print("OK: artifacts, fonts, local links, interview and AI target flowcharts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

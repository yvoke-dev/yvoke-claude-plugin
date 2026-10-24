"""Lintel CLI dispatcher and command entry point."""

import argparse
from pathlib import Path
import shlex
import sys
from typing import Optional

# Prevent bytecode generation in plugin directory
sys.dont_write_bytecode = True

# Ensure plugin directory is on sys.path before importing siblings
_plugin_dir = str(Path(__file__).resolve().parent.parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# Import siblings
from lintel import (
    amend,
    audit,
    caller,
    config,
    config_amend,
    decide,
    deliver,
    diff,
    doctor,
    drift,
    events,
    findings,
    flow,
    gate,
    git,
    installer,
    lock,
    paths,
    personas,
    reviews,
    runs,
    restore,
    self_check,
    shell,
    skills,
    state,
    task_control,
    task_start,
)
from lintel.caller import (
    HUMAN_ONLY_COMMANDS,
    CallerRefusalError,
    check_caller,
    is_human_only_command,
)
from lintel.task_start import task_start as do_task_start, task_status as do_task_status


def is_human_only(args: argparse.Namespace, repo_root: Optional[Path] = None) -> bool:
    """Determine whether the parsed command requires human confirmation dialog per §3.5."""
    subcmd = getattr(args, "subcommand", None) or ""
    return is_human_only_command(subcmd, args=args, repo_root=repo_root)


def build_parser() -> argparse.ArgumentParser:
    """Construct the full CLI argument parser for all §3.5 subcommands."""
    parser = argparse.ArgumentParser(
        prog="lintel",
        description="Lintel 2: QA -> DEV workflow orchestrator",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        title="subcommands",
        metavar="<subcommand>",
        help="Subcommand to execute",
    )

    # 1. install <project> (human)
    p_install = subparsers.add_parser("install", help="Vendor plugin and initialize project (human-only)")
    p_install.add_argument("--global", dest="global_install", action="store_true", help="Install plugin globally")
    p_install.add_argument("--dest", help="Global destination directory")
    p_install.add_argument("project", nargs="?", default=".", help="Path to project directory")

    # 2. init [--scripts] (human)
    p_init = subparsers.add_parser("init", help="Initialize project configuration and templates (human-only)")
    p_init.add_argument(
        "--scripts",
        action="store_true",
        help="Copy missing script templates without overwriting",
    )

    # 3. upgrade (human)
    subparsers.add_parser("upgrade", help="Upgrade vendored plugin (human-only)")

    # 4. uninstall (human)
    subparsers.add_parser("uninstall", help="Uninstall vendored plugin (human-only)")

    # 5. doctor [--role r] [--path p]
    p_doctor = subparsers.add_parser("doctor", help="Check installation, configuration and environment")
    p_doctor.add_argument("--role", help="Inspect skills and steering for specified role")
    p_doctor.add_argument("--path", help="Show path class and test group for given path")

    # 6. task ...
    p_task = subparsers.add_parser("task", help="Task lifecycle management commands")
    task_subparsers = p_task.add_subparsers(
        dest="task_command",
        title="task subcommands",
        metavar="<task-command>",
        help="Task subcommand",
    )

    # task start "<title>"
    p_task_start = task_subparsers.add_parser("start", help="Start a new task with given title")
    p_task_start.add_argument("title", help="Task title")

    # task status
    task_subparsers.add_parser("status", help="Print task progress, position and next action")

    # task resume
    task_subparsers.add_parser("resume", help="Resume task in a new orchestrator conversation")

    # task finish [--pr] (human)
    p_task_finish = task_subparsers.add_parser("finish", help="Complete task and commit task record (human-only)")
    p_task_finish.add_argument("--pr", action="store_true", help="Open a pull/merge request")

    # task abort
    task_subparsers.add_parser("abort", help="Abort current task and restore branch")

    # task unstick [--force]
    p_task_unstick = task_subparsers.add_parser("unstick", help="Abandon current run and re-invoke role")
    p_task_unstick.add_argument(
        "--force",
        action="store_true",
        help="Force abandonment of a live run (human-only)",
    )

    # task wait [--timeout s]
    p_task_wait = task_subparsers.add_parser("wait", help="Wait for active run state change")
    p_task_wait.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Wait timeout in seconds (default: 60)",
    )

    # 7. invoke <role> --phase N [--from-report] [--pair <writer>]
    p_invoke = subparsers.add_parser("invoke", help="Invoke a persona run")
    p_invoke.add_argument("role", help="Persona role to invoke")
    p_invoke.add_argument("--phase", help="Stage label or phase number")
    p_invoke.add_argument(
        "--from-report",
        action="store_true",
        help="Copy blocked writer report section into brief",
    )
    p_invoke.add_argument("--pair", help="Writer of the stalled pair (for arbiter)")

    # 8. answer <role> <question-id> "<text>"
    p_answer = subparsers.add_parser("answer", help="Record answer to a needs-input question")
    p_answer.add_argument("role", help="Asking role")
    p_answer.add_argument("question_id", help="Question identifier")
    p_answer.add_argument("text", help="Verbatim answer text")

    # 9. enter <role> <nonce>
    p_enter = subparsers.add_parser("enter", help="Persona entry point activating run")
    p_enter.add_argument("role", help="Persona role")
    p_enter.add_argument("nonce", help="One-time run nonce")

    # 10. done --verdict v [--cause c]
    p_done = subparsers.add_parser("done", help="Record persona completion verdict")
    p_done.add_argument("--verdict", help="Verdict (e.g. complete, needs-input, blocked, approve, changes, no-change)")
    p_done.add_argument("--cause", help="Cause code when verdict is blocked")

    # 11. diff [--base | --delivered N] [--full]
    p_diff = subparsers.add_parser("diff", help="Show code diffs excluding tests")
    diff_group = p_diff.add_mutually_exclusive_group()
    diff_group.add_argument("--base", action="store_true", help="Diff against task base SHA")
    diff_group.add_argument("--delivered", help="Diff against what phase N delivered")
    p_diff.add_argument("--full", action="store_true", help="Show full diffs (human-only)")

    # 12. approve requirements | plan --phase N | final | docs [--reject "<why>" [--pair po|architect]] (human)
    p_approve = subparsers.add_parser("approve", help="Human approvals for documents and phases (human-only)")
    p_approve.add_argument(
        "target",
        choices=["requirements", "plan", "final", "docs"],
        help="Approval target",
    )
    p_approve.add_argument("--phase", help="Phase number for plan approval")
    p_approve.add_argument("--reject", nargs="?", const="", help="Rejection rationale / feedback")
    p_approve.add_argument(
        "--pair",
        choices=["po", "architect"],
        help="Target pair for docs rejection",
    )

    # 13. restore-tests
    subparsers.add_parser(
        "restore-tests",
        help="Restore tests, locked files, and approved documents to recorded head",
    )

    # 14. check --phase N [--start] [--full]
    p_check = subparsers.add_parser("check", help="Run gate checks in worktree without committing")
    p_check.add_argument("--phase", help="Phase number")
    p_check.add_argument("--start", action="store_true", help="Detach and run gate check in background")
    p_check.add_argument("--full", action="store_true", help="Show full report including test paths (human-only)")

    # 15. decide --phase <stage> [--pair <writer>] (human)
    p_decide = subparsers.add_parser("decide", help="Resolve stall or block escalation (human-only)")
    p_decide.add_argument("--phase", help="Stage or phase label")
    p_decide.add_argument("--pair", help="Writer of the pair")
    p_decide.add_argument("--guidance", help="Guidance text for writer")
    p_decide.add_argument("--accept", action="store_true", help="Accept writer current lane content")
    p_decide.add_argument("--retry", action="store_true", help="Retry writer run")
    p_decide.add_argument("--reset", action="store_true", help="Reset DEV pair")
    p_decide.add_argument("--to", choices=["final", "docs"], help="Transition to final review or docs update")
    p_decide.add_argument("--skip-docs", action="store_true", help="Skip documentation update")
    p_decide.add_argument("--abort", action="store_true", help="Abort task from decide")

    # 16. lock --phase N [--allow-green <group>]... [--reject "<why>"] (human)
    p_lock = subparsers.add_parser("lock", help="Lock test suite for phase (human-only)")
    p_lock.add_argument("--phase", help="Phase number")
    p_lock.add_argument(
        "--allow-green",
        action="append",
        default=[],
        help="Allow listed test group to pass against stubs",
    )
    p_lock.add_argument("--reject", nargs="?", const="", help="Reject lock and send QA back")

    # 17. amend --phase N [--reject "<why>"] / amend --config [--release <path>]... (human)
    p_amend = subparsers.add_parser("amend", help="Amend phase tests or configuration (human-only)")
    p_amend.add_argument("--phase", help="Phase number")
    p_amend.add_argument("--reject", help="Reject amend")
    p_amend.add_argument("--config", action="store_true", help="Amend config, scripts, personas, or steering")
    p_amend.add_argument(
        "--release",
        action="append",
        default=[],
        help="Release locked path moved out of tests",
    )

    # 18. drift --phase N [--ack | --reject "<why>"] [--full]
    p_drift = subparsers.add_parser("drift", help="Report or acknowledge build and configuration drift")
    p_drift.add_argument("--phase", help="Phase number")
    drift_action = p_drift.add_mutually_exclusive_group()
    drift_action.add_argument("--ack", action="store_true", help="Acknowledge drift files (human-only)")
    drift_action.add_argument("--reject", help="Reject drift with human finding (human-only)")
    p_drift.add_argument("--full", action="store_true", help="Show full diffs (human-only)")

    # 19. audit --phase N [--ack | --reject "<why>"] [--pair po|architect] [--full]
    p_audit = subparsers.add_parser("audit", help="Report or acknowledge boundary crossings")
    p_audit.add_argument("--phase", help="Phase number")
    audit_action = p_audit.add_mutually_exclusive_group()
    audit_action.add_argument("--ack", action="store_true", help="Acknowledge audit crossings (human-only)")
    audit_action.add_argument("--reject", help="Reject audit crossings (human-only)")
    p_audit.add_argument("--pair", choices=["po", "architect"], help="Target pair for rejection in docs update")
    p_audit.add_argument("--full", action="store_true", help="Show full report including test paths (human-only)")

    # 20. deliver --phase N [--full]
    p_deliver = subparsers.add_parser("deliver", help="Gate check and delivery commit for phase")
    p_deliver.add_argument("--phase", help="Phase number")
    p_deliver.add_argument("--full", action="store_true", help="Show full report including test paths (human-only)")

    # 21. reset --phase N (human)
    p_reset = subparsers.add_parser("reset", help="Reset phase implementation to recorded head (human-only)")
    p_reset.add_argument("--phase", help="Phase number")
    # 22. stats [--task <id>] [--json]
    p_stats = subparsers.add_parser("stats", help="Show statistics over committed task records")
    p_stats.add_argument("--task", help="Filter by specific task ID")
    p_stats.add_argument("--json", action="store_true", help="Output JSON instead of plain text")

    return parser


def main(argv=None, repo_root: Optional[Path] = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    if argv is None and len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args(argv)
    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    # Plugin integrity check per PLAN §3.6 and docs/state-machine.md T-STG-10
    if not self_check.is_exempt_from_integrity_check(args):
        root = Path(repo_root).resolve() if repo_root else self_check.find_repo_root()
        active_info = self_check.find_active_task_head(root)
        if active_info:
            recorded_head, _ = active_info
            differences = self_check.check_plugin_integrity(root, recorded_head)
            if differences:
                diff_list = "\n".join(f"  - {d}" for d in differences)
                sys.stderr.write(
                    f"Refused: vendored plugin directory (.agents/plugins/lintel/) differs from recorded head '{recorded_head}' (T-STG-10):\n"
                    f"{diff_list}\n"
                    f"Revert differing files or run 'lintel task abort'.\n"
                )
                sys.exit(1)

    # Caller check for human-only commands per §3.5 and M1a-11
    raw_args = sys.argv[1:] if argv is None else argv
    command_str = shlex.join(raw_args)
    check_caller(command_str, args, repo_root=repo_root)

    root = Path(repo_root).resolve() if repo_root else self_check.find_repo_root()

    if args.subcommand == "install":
        installer.install(
            Path(args.project),
            global_install=getattr(args, "global_install", False),
            dest=getattr(args, "dest", None)
        )
        sys.exit(0)

    if args.subcommand == "upgrade":
        try:
            installer.upgrade(root)
            sys.exit(0)
        except installer.InstallerRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(1)

    if args.subcommand == "uninstall":
        try:
            installer.uninstall(root)
            sys.exit(0)
        except installer.InstallerRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(1)

    if args.subcommand == "init":
        installer.init(root, only_missing_scripts=args.scripts)
        sys.exit(0)

    if args.subcommand == "doctor":
        report = doctor.doctor(root, role=args.role, path_arg=args.path)
        sys.stdout.write(report.format() + "\n")
        sys.exit(0 if report.ok else 1)

    if args.subcommand == "task":
        if not getattr(args, "task_command", None):
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction) and "task" in action.choices:
                    action.choices["task"].print_help()
                    sys.exit(0)
            parser.print_help()
            sys.exit(0)

        if args.task_command == "start":
            do_task_start(root, args.title)
            sys.exit(0)
        if args.task_command == "status":
            code = do_task_status(root)
            sys.exit(code)
        if args.task_command == "resume":
            try:
                task_control.task_resume(root)
                sys.exit(0)
            except (task_control.TaskControlRefusalError, flow.FlowRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))
        if args.task_command == "wait":
            try:
                task_control.task_wait(root, timeout=args.timeout)
                sys.exit(0)
            except (task_control.TaskControlRefusalError, flow.FlowRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))
        if args.task_command == "unstick":
            try:
                task_control.task_unstick(
                    root,
                    force=args.force,
                    skip_caller_check=True,
                )
                sys.exit(0)
            except (task_control.TaskControlRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))
        if args.task_command == "abort":
            try:
                task_control.task_abort(root)
                sys.exit(0)
            except (task_control.TaskControlRefusalError, flow.FlowRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))
        if args.task_command == "finish":
            try:
                task_control.task_finish(
                    root,
                    pr=args.pr,
                    skip_caller_check=True,
                )
                sys.exit(0)
            except (task_control.TaskControlRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))

    if args.subcommand == "invoke":
        try:
            runs.invoke(
                repo_root=root,
                role=args.role,
                phase=args.phase,
                from_report=args.from_report,
                pair=args.pair,
            )
            sys.exit(0)
        except runs.RunRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(e.code)

    if args.subcommand == "enter":
        try:
            runs.enter(
                repo_root=root,
                role=args.role,
                nonce=args.nonce,
            )
            sys.exit(0)
        except runs.RunRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(e.code)

    if args.subcommand == "done":
        try:
            runs.done(
                repo_root=root,
                verdict=args.verdict,
                cause=args.cause,
            )
            sys.exit(0)
        except runs.RunRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(e.code)

    if args.subcommand == "answer":
        try:
            runs.answer(
                repo_root=root,
                role=args.role,
                question_id=args.question_id,
                text=args.text,
            )
            sys.exit(0)
        except runs.RunRefusalError as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(e.code)

    if args.subcommand == "approve":
        try:
            if args.target == "requirements":
                reviews.approve_requirements(
                    repo_root=root,
                    reject=args.reject,
                    pair=args.pair,
                )
                sys.exit(0)
            elif args.target == "plan":
                reviews.approve_plan(
                    repo_root=root,
                    phase=args.phase,
                    reject=args.reject,
                    pair=args.pair,
                )
                sys.exit(0)
            elif args.target == "final":
                reviews.approve_final(
                    repo_root=root,
                    reject=args.reject,
                    phase=args.phase,
                    pair=args.pair,
                )
                sys.exit(0)
            elif args.target == "docs":
                reviews.approve_docs(
                    repo_root=root,
                    reject=args.reject,
                    pair=args.pair,
                    phase=args.phase,
                )
                sys.exit(0)
        except (reviews.ReviewRefusalError, flow.FlowRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "lock":
        try:
            lock.lock(
                repo_root=root,
                phase=args.phase,
                allow_green=args.allow_green,
                reject=args.reject,
            )
            sys.exit(0)
        except (lock.LockRefusalError, flow.FlowRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "restore-tests":
        try:
            restore.restore_tests(
                repo_root=root,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (restore.RestoreRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "reset":
        try:
            restore.reset(
                repo_root=root,
                phase=args.phase,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (restore.RestoreRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "amend":
        if args.config or args.release:
            try:
                config_amend.amend_config(
                    repo_root=root,
                    release=args.release,
                    phase=args.phase,
                    skip_caller_check=True,
                )
                sys.exit(0)
            except (config_amend.ConfigAmendRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))
        else:
            try:
                amend.amend(
                    repo_root=root,
                    phase=args.phase,
                    reject=args.reject,
                    skip_caller_check=True,
                )
                sys.exit(0)
            except (amend.AmendRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
                sys.stderr.write(str(e) + "\n")
                sys.exit(getattr(e, "code", 1))

    if args.subcommand == "drift":
        try:
            drift.drift(
                repo_root=root,
                phase=args.phase,
                ack=args.ack,
                reject=args.reject,
                full=args.full,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (drift.DriftRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "check":
        try:
            deliver.check(
                repo_root=root,
                phase=args.phase,
                start=args.start,
                full=args.full,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (deliver.DeliverRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "deliver":
        try:
            deliver.deliver(
                repo_root=root,
                phase=args.phase,
                full=args.full,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (deliver.DeliverRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "decide":
        try:
            decide.decide(
                repo_root=root,
                phase=args.phase,
                pair=args.pair,
                guidance=args.guidance,
                accept=args.accept,
                retry=args.retry,
                reset=args.reset,
                to=args.to,
                skip_docs=getattr(args, "skip_docs", False),
                abort=args.abort,
                skip_caller_check=True,
            )
            sys.exit(0)
        except (decide.DecideRefusalError, flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "diff":
        try:
            diff.diff(
                repo_root=root,
                base=args.base,
                delivered=args.delivered,
                full=args.full,
            )
            sys.exit(0)
        except (diff.DiffRefusalError, flow.FlowRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "audit":
        import lintel.audit as audit
        try:
            audit.audit(
                repo_root=root,
                phase=args.phase,
                ack=args.ack,
                reject=args.reject,
                pair=args.pair,
                full=args.full,
                skip_caller_check=False,
            )
            sys.exit(0)
        except (flow.FlowRefusalError, caller.CallerRefusalError) as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(getattr(e, "code", 1))

    if args.subcommand == "stats":
        import lintel.stats as stats
        try:
            stats.run_stats(
                repo_root=root,
                task_id=args.task,
                as_json=args.json,
            )
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(str(e) + "\n")
            sys.exit(1)

    sys.exit("not implemented")


if __name__ == "__main__":
    main()

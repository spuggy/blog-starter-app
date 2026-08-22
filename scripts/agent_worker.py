#!/usr/bin/env python3
"""Prototype GitHub issue queue worker for coding agents.

The worker treats GitHub issue labels as a small state machine:

- agent:ready -> agent:planning
- agent:approved -> agent:building

It defaults to dry-run mode. Pass --execute to mutate GitHub and launch the
configured agent command.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


API_ROOT = "https://api.github.com"


@dataclass(frozen=True)
class Labels:
    ready: str
    planning: str
    plan_ready: str
    needs_answers: str
    approved: str
    building: str
    reviewing: str
    blocked: str


@dataclass(frozen=True)
class Config:
    repo: str
    token_env: str
    workspace: str
    agent_cmd: str
    agent_timeout_seconds: int
    base_branch: str
    branch_prefix: str
    git_remote: str
    check_commands: tuple[str, ...]
    poll_seconds: int
    once: bool
    execute: bool
    labels: Labels


class GitHub:
    def __init__(self, repo: str, token: str, dry_run: bool) -> None:
        self.repo = repo
        self.token = token
        self.dry_run = dry_run

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str | int] | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = f"{API_ROOT}/repos/{self.repo}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read().decode("utf-8")
                return json.loads(response_body) if response_body else None
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub {method} {path} failed: {exc.code} {details}") from exc

    def list_open_issues_with_label(self, label: str) -> list[dict[str, Any]]:
        issues = self.request(
            "GET",
            "/issues",
            query={
                "state": "open",
                "labels": label,
                "per_page": 25,
                "sort": "created",
                "direction": "asc",
            },
        )
        return [issue for issue in issues if "pull_request" not in issue]

    def list_open_issues(self, limit: int = 5) -> list[dict[str, Any]]:
        issues = self.request(
            "GET",
            "/issues",
            query={
                "state": "open",
                "per_page": limit,
                "sort": "created",
                "direction": "desc",
            },
        )
        return [issue for issue in issues if "pull_request" not in issue]

    def get_issue(self, number: int) -> dict[str, Any]:
        return self.request("GET", f"/issues/{number}")

    def add_labels(self, number: int, labels: list[str]) -> None:
        if self.dry_run:
            print(f"DRY RUN: add labels to #{number}: {labels}")
            return
        self.request("POST", f"/issues/{number}/labels", body={"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        if self.dry_run:
            print(f"DRY RUN: remove label from #{number}: {label}")
            return
        encoded = urllib.parse.quote(label, safe="")
        self.request("DELETE", f"/issues/{number}/labels/{encoded}", allow_404=True)

    def comment(self, number: int, body: str) -> None:
        if self.dry_run:
            print(f"DRY RUN: comment on #{number}:\n{body}\n")
            return
        self.request("POST", f"/issues/{number}/comments", body={"body": body})

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool,
    ) -> dict[str, Any] | None:
        if self.dry_run:
            print(f"DRY RUN: create draft PR {head} -> {base}: {title}")
            print(f"DRY RUN: PR body follows\n{body}\n")
            return None
        return self.request(
            "POST",
            "/pulls",
            body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
                "maintainer_can_modify": True,
            },
        )


def label_names(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels", [])}


def slugify(value: str, max_length: int = 48) -> str:
    value = value.lower()
    value = re.sub(r"^\[agent\]:\s*", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-+", "-", value)
    return (value[:max_length].strip("-") or "issue")


def first_issue_for_label(github: GitHub, label: str) -> dict[str, Any] | None:
    issues = github.list_open_issues_with_label(label)
    return issues[0] if issues else None


def run_local_command(args: list[str], cwd: str, *, timeout: int | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True, timeout=timeout)


def run_shell_command(command: str, cwd: str, *, timeout: int | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True, shell=True, timeout=timeout)


def git(config: Config, *args: str) -> None:
    command = ["git", *args]
    if config.execute:
        print(f"Running: {' '.join(shlex.quote(part) for part in command)}")
        run_local_command(command, config.workspace)
    else:
        print(f"DRY RUN: would run {' '.join(shlex.quote(part) for part in command)}")


def git_output(config: Config, *args: str) -> str:
    command = ["git", *args]
    completed = subprocess.run(
        command,
        cwd=config.workspace,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def ensure_clean_worktree(config: Config) -> None:
    status = git_output(config, "status", "--porcelain")
    if status:
        raise RuntimeError(
            "Workspace has uncommitted changes before build starts. "
            "Commit, stash, or remove them before the worker claims build work.\n"
            f"{status}"
        )


def create_build_branch(config: Config, issue: dict[str, Any]) -> str:
    branch = f"{config.branch_prefix}/{issue['number']}-{slugify(issue['title'])}"
    git(config, "fetch", config.git_remote, config.base_branch)
    git(config, "switch", config.base_branch)
    git(config, "pull", "--ff-only", config.git_remote, config.base_branch)
    git(config, "switch", "-c", branch)
    return branch


def changed_files(config: Config) -> list[str]:
    status = git_output(config, "status", "--porcelain")
    files: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        files.append(line[3:])
    return files


def commit_and_push(config: Config, issue: dict[str, Any], branch: str) -> str | None:
    files = changed_files(config)
    if not files:
        return None

    commit_message = f"Implement issue #{issue['number']}: {issue['title']}"
    git(config, "add", "-A")
    git(config, "commit", "-m", commit_message)
    git(config, "push", "-u", config.git_remote, branch)
    if config.execute:
        return git_output(config, "rev-parse", "HEAD")
    return "dry-run-sha"


def run_worker_checks(config: Config) -> None:
    for command in config.check_commands:
        if config.execute:
            print(f"Running check: {command}")
            run_shell_command(command, config.workspace)
        else:
            print(f"DRY RUN: would run check: {command}")


def pr_body(config: Config, issue: dict[str, Any], commit_sha: str | None) -> str:
    checks = "\n".join(f"- [x] `{command}`" for command in config.check_commands)
    if not checks:
        checks = "- [ ] Agent-reported checks only"

    commit_line = f"\n\nCommit: `{commit_sha}`" if commit_sha else ""
    return "\n".join(
        [
            "# Summary",
            "",
            f"- Implements GitHub issue #{issue['number']}.",
            "- Coding agent edited files only; the worker handled branch, commit, push, and PR creation.",
            "",
            "# Linked Issue",
            "",
            f"Closes #{issue['number']}",
            "",
            "# Verification",
            "",
            checks,
            "",
            "# Review Notes",
            "",
            "- Automated review comments addressed: none yet.",
            "- Human review comments addressed: none yet.",
            "- Known limitations or follow-up work: see issue discussion if applicable.",
            commit_line,
        ]
    )


def run_agent(config: Config, prompt: str) -> None:
    command = shlex.split(config.agent_cmd)

    if config.execute:
        print(f"Starting agent: {command}")
        subprocess.run(
            [*command, prompt],
            cwd=config.workspace,
            check=True,
            timeout=config.agent_timeout_seconds,
        )
    else:
        print(f"DRY RUN: would start agent in {config.workspace}: {command}")
        print("DRY RUN: prompt follows")
        print(prompt)


def mark_agent_failure(
    github: GitHub,
    config: Config,
    issue_number: int,
    run_id: str,
    active_label: str,
    message: str,
) -> None:
    github.add_labels(issue_number, [config.labels.blocked])
    github.remove_label(issue_number, active_label)
    github.comment(
        issue_number,
        "\n".join(
            [
                "Agent worker run failed.",
                "",
                f"Run: `{run_id}`",
                f"Status: `{config.labels.blocked}`",
                "",
                message,
            ]
        ),
    )


def claim_for_planning(github: GitHub, config: Config, issue: dict[str, Any], run_id: str) -> bool:
    number = issue["number"]
    labels = label_names(issue)
    if config.labels.ready not in labels:
        print(f"Skipping #{number}: no longer has {config.labels.ready}")
        return False

    github.add_labels(number, [config.labels.planning])
    github.remove_label(number, config.labels.ready)
    github.comment(
        number,
        "\n".join(
            [
                "Claimed by agent worker.",
                "",
                f"Run: `{run_id}`",
                "Status: preparing plan.",
                "",
                "I will inspect the issue and repository, then post a plan before making code changes.",
            ]
        ),
    )

    if config.execute:
        refreshed = github.get_issue(number)
        refreshed_labels = label_names(refreshed)
        if config.labels.planning not in refreshed_labels or config.labels.ready in refreshed_labels:
            print(f"Claim verification failed for #{number}; not starting agent.")
            return False

    return True


def claim_for_build(github: GitHub, config: Config, issue: dict[str, Any], run_id: str) -> bool:
    number = issue["number"]
    labels = label_names(issue)
    if config.labels.approved not in labels:
        print(f"Skipping #{number}: no longer has {config.labels.approved}")
        return False

    github.add_labels(number, [config.labels.building])
    github.remove_label(number, config.labels.approved)
    github.comment(
        number,
        "\n".join(
            [
                "Approved plan picked up by agent worker.",
                "",
                f"Run: `{run_id}`",
                "Status: building.",
                "",
                "I will implement the approved plan, run checks, and prepare a draft PR.",
            ]
        ),
    )

    if config.execute:
        refreshed = github.get_issue(number)
        refreshed_labels = label_names(refreshed)
        if config.labels.building not in refreshed_labels or config.labels.approved in refreshed_labels:
            print(f"Build claim verification failed for #{number}; not starting agent.")
            return False

    return True


def planning_prompt(config: Config, issue: dict[str, Any], run_id: str) -> str:
    return f"""Pick up GitHub issue #{issue["number"]} in {config.repo}.

Run id: {run_id}

First produce a plan only. Do not implement until a human approves the plan.

Required behavior:
- Read the issue and relevant repository files.
- If requirements are unclear, post specific questions on the issue.
- If you can plan the work, post the plan on the issue.
- Do not edit files, commit, push, or open a PR during this planning run.

When complete:
- If a plan was posted, move labels from {config.labels.planning} to {config.labels.plan_ready}.
- If answers are needed, move labels from {config.labels.planning} to {config.labels.needs_answers}.
"""


def build_prompt(config: Config, issue: dict[str, Any], run_id: str) -> str:
    return f"""Issue #{issue["number"]} in {config.repo} has an approved plan.

Run id: {run_id}

Implement the approved plan.

Required behavior:
- Keep changes scoped to the issue and approved plan.
- Edit files only.
- Do not create branches.
- Do not commit changes.
- Do not push changes.
- Do not open a pull request.
- Run the requested local checks if your sandbox permits it.
- Fix failures and rerun checks where possible.
- Leave the final file changes in the working tree for the Python worker to commit and publish.

When complete:
- Add {config.labels.blocked} if a true blocker prevents progress.
"""


def publish_build_result(github: GitHub, config: Config, issue: dict[str, Any], branch: str) -> None:
    run_worker_checks(config)
    commit_sha = commit_and_push(config, issue, branch)

    if not commit_sha:
        raise RuntimeError("Agent completed without leaving any file changes to commit.")

    title = f"Implement issue #{issue['number']}: {issue['title']}"
    pull_request = github.create_pull_request(
        title=title,
        body=pr_body(config, issue, commit_sha),
        head=branch,
        base=config.base_branch,
        draft=True,
    )
    pr_url = pull_request["html_url"] if pull_request else "(dry run)"
    github.comment(
        issue["number"],
        "\n".join(
            [
                "Build run completed.",
                "",
                f"Branch: `{branch}`",
                f"Commit: `{commit_sha}`",
                f"Draft PR: {pr_url}",
            ]
        ),
    )
    github.add_labels(issue["number"], [config.labels.reviewing])
    github.remove_label(issue["number"], config.labels.building)


def handle_one_issue(github: GitHub, config: Config) -> bool:
    approved_issue = first_issue_for_label(github, config.labels.approved)
    if approved_issue:
        run_id = f"agent-{uuid.uuid4().hex[:8]}"
        print(f"Found approved issue #{approved_issue['number']}: {approved_issue['title']}")
        try:
            ensure_clean_worktree(config)
        except RuntimeError as exc:
            mark_agent_failure(
                github,
                config,
                approved_issue["number"],
                run_id,
                config.labels.approved,
                str(exc),
            )
            return True

        if claim_for_build(github, config, approved_issue, run_id):
            try:
                branch = create_build_branch(config, approved_issue)
                run_agent(config, build_prompt(config, approved_issue, run_id))
                publish_build_result(github, config, approved_issue, branch)
            except subprocess.TimeoutExpired:
                mark_agent_failure(
                    github,
                    config,
                    approved_issue["number"],
                    run_id,
                    config.labels.building,
                    f"The agent command timed out after {config.agent_timeout_seconds} seconds.",
                )
            except subprocess.CalledProcessError as exc:
                mark_agent_failure(
                    github,
                    config,
                    approved_issue["number"],
                    run_id,
                    config.labels.building,
                    f"The agent command exited with status {exc.returncode}.",
                )
            except RuntimeError as exc:
                mark_agent_failure(
                    github,
                    config,
                    approved_issue["number"],
                    run_id,
                    config.labels.building,
                    str(exc),
                )
        return True

    ready_issue = first_issue_for_label(github, config.labels.ready)
    if ready_issue:
        run_id = f"agent-{uuid.uuid4().hex[:8]}"
        print(f"Found ready issue #{ready_issue['number']}: {ready_issue['title']}")
        if claim_for_planning(github, config, ready_issue, run_id):
            try:
                run_agent(config, planning_prompt(config, ready_issue, run_id))
            except subprocess.TimeoutExpired:
                mark_agent_failure(
                    github,
                    config,
                    ready_issue["number"],
                    run_id,
                    config.labels.planning,
                    f"The agent command timed out after {config.agent_timeout_seconds} seconds.",
                )
            except subprocess.CalledProcessError as exc:
                mark_agent_failure(
                    github,
                    config,
                    ready_issue["number"],
                    run_id,
                    config.labels.planning,
                    f"The agent command exited with status {exc.returncode}.",
                )
        return True

    print("No ready or approved issues found.")
    print("Recent open issues:")
    for issue in github.list_open_issues():
        labels = ", ".join(sorted(label_names(issue))) or "no labels"
        print(f"- #{issue['number']} {issue['title']} [{labels}]")
    return False


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description="Prototype GitHub issue worker for coding agents.")
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form.")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable containing a GitHub token.")
    parser.add_argument("--workspace", default=os.getcwd(), help="Local repository checkout where the agent should run.")
    parser.add_argument("--agent-cmd", default="codex exec", help="Non-interactive agent command to launch, for example 'codex exec' or 'claude --print'.")
    parser.add_argument("--agent-timeout-seconds", type=int, default=3600, help="Maximum runtime for one agent process.")
    parser.add_argument("--base-branch", default="main", help="Base branch used for build branches and PRs.")
    parser.add_argument("--branch-prefix", default="agent", help="Prefix for worker-created build branches.")
    parser.add_argument("--git-remote", default="origin", help="Git remote used for fetch, pull, and push.")
    parser.add_argument("--check-command", action="append", default=[], help="Worker-owned verification command to run before commit. Repeatable.")
    parser.add_argument("--poll-seconds", type=int, default=60, help="Delay between polling loops.")
    parser.add_argument("--once", action="store_true", help="Check once and exit.")
    parser.add_argument("--execute", action="store_true", help="Mutate GitHub and start the configured agent.")
    parser.add_argument("--ready-label", default="agent:ready")
    parser.add_argument("--planning-label", default="agent:planning")
    parser.add_argument("--plan-ready-label", default="agent:plan-ready")
    parser.add_argument("--needs-answers-label", default="agent:needs-answers")
    parser.add_argument("--approved-label", default="agent:approved")
    parser.add_argument("--building-label", default="agent:building")
    parser.add_argument("--reviewing-label", default="agent:reviewing")
    parser.add_argument("--blocked-label", default="agent:blocked")

    args = parser.parse_args(argv)
    return Config(
        repo=args.repo,
        token_env=args.token_env,
        workspace=args.workspace,
        agent_cmd=args.agent_cmd,
        agent_timeout_seconds=args.agent_timeout_seconds,
        base_branch=args.base_branch,
        branch_prefix=args.branch_prefix,
        git_remote=args.git_remote,
        check_commands=tuple(args.check_command),
        poll_seconds=args.poll_seconds,
        once=args.once,
        execute=args.execute,
        labels=Labels(
            ready=args.ready_label,
            planning=args.planning_label,
            plan_ready=args.plan_ready_label,
            needs_answers=args.needs_answers_label,
            approved=args.approved_label,
            building=args.building_label,
            reviewing=args.reviewing_label,
            blocked=args.blocked_label,
        ),
    )


def main(argv: list[str]) -> int:
    config = parse_args(argv)
    token = os.environ.get(config.token_env)
    if not token:
        print(f"Missing GitHub token env var: {config.token_env}", file=sys.stderr)
        return 2

    github = GitHub(repo=config.repo, token=token, dry_run=not config.execute)

    while True:
        handle_one_issue(github, config)
        if config.once:
            return 0
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

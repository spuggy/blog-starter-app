# Agentic Development Loop

This workflow turns GitHub issues into reviewed pull requests through a controlled agent loop. The key rule is that agents can draft plans and code, but humans approve scope before build work starts and humans own final merge decisions.

## Goals

- Start development from a GitHub issue with enough detail for an agent to plan.
- Put agent work behind an explicit plan approval step.
- Require local verification before a pull request is opened.
- Use GitHub Actions as the repeatable merge gate.
- Let automated reviewers and humans leave comments that the agent can address.
- Keep every step visible in GitHub so the history is auditable.

## Roles

| Role | Responsibility |
| --- | --- |
| Issue author | Defines the problem, expected behavior, acceptance criteria, and constraints. |
| Triage human | Confirms the issue is ready for agent work and applies queue labels. |
| Agent | Produces a plan, asks blocking questions, implements approved work, runs checks, opens a PR, and addresses review comments. |
| CI | Runs the repository guard checks on every PR update. |
| Automated reviewer | Reviews the PR for likely bugs, maintainability risks, and test gaps. |
| Human reviewer | Reviews product fit, implementation choices, edge cases, and approves or requests changes. |
| Maintainer | Merges after required approvals and checks pass. |

## GitHub Labels

Use labels as the queue state machine:

| Label | Meaning |
| --- | --- |
| `agent:ready` | Issue has enough context for an agent to pick up. |
| `agent:planning` | Agent is preparing a plan and questions. |
| `agent:needs-answers` | Agent is blocked on human clarification. |
| `agent:plan-ready` | Plan is posted and waiting for approval. |
| `agent:approved` | Human has approved the plan and build can start. |
| `agent:building` | Agent is implementing the approved plan. |
| `agent:reviewing` | PR is open and the agent is responding to review feedback. |
| `agent:blocked` | Agent cannot proceed without a decision or external fix. |
| `agent:done` | Work has been merged or intentionally closed. |

## Issue Contract

An agent-ready issue should include:

- Problem statement: what is wrong or missing.
- Desired behavior: what users or maintainers should observe after the change.
- Acceptance criteria: testable conditions that define done.
- Out of scope: related work the agent should not do.
- Constraints: design, architecture, compatibility, performance, security, or accessibility requirements.
- Verification expectations: local commands, unit tests, Playwright checks, or manual QA notes.
- References: screenshots, logs, links, related issues, or prior PRs.

If any of these are missing and the answer changes implementation scope, the agent should ask questions before planning.

## Loop

1. Human creates or updates a GitHub issue using the agent task template.
2. Triage human applies `agent:ready`.
3. Agent claims the issue by replacing `agent:ready` with `agent:planning`.
4. Agent reads the issue, repository conventions, and relevant files.
5. Agent posts a plan comment with:
   - short problem restatement
   - proposed implementation steps
   - files or areas likely to change
   - verification commands
   - risks and open questions
6. If questions are blocking, agent applies `agent:needs-answers` and waits.
7. Human answers questions and approves the plan by commenting `Approved to build` or applying `agent:approved`.
8. Agent creates a branch named `agent/<issue-number>-short-title`.
9. Agent implements only the approved scope.
10. Agent runs local checks, fixes failures, and repeats until local verification passes or a true blocker is found.
11. Agent opens a draft PR linked to the issue.
12. GitHub Actions runs required checks.
13. Automated reviewer reviews the PR.
14. Agent addresses actionable automated review comments and CI failures, then reruns checks.
15. Human reviewer reviews the PR.
16. Agent addresses human review comments and reruns checks.
17. Human reviewer approves.
18. Maintainer merges the PR.
19. Issue is closed and labeled `agent:done`.

## Agent Operating Rules

- Do not start implementation until a human has approved the plan.
- Keep changes scoped to the approved issue.
- Ask questions when requirements are ambiguous enough to affect architecture, behavior, data model, security, or user experience.
- Prefer existing project patterns over new abstractions.
- Run the fastest relevant checks during development, then the full required check set before PR.
- If checks cannot be run locally, state why in the PR and rely on CI only as a fallback.
- Do not resolve human or automated review comments silently; reply with what changed or why no code change was made.
- Do not merge your own PR.

## Recommended Check Levels

For this repository today, the only defined package scripts are:

```bash
npm run build
```

As the project grows, add these scripts so the loop can become stricter:

```bash
npm run lint
npm run typecheck
npm test
npm run test:e2e
```

Suggested merge gate:

- Required now: `npm run build`
- Add next: lint and typecheck
- Add when behavior coverage matters: unit tests
- Add when user flows matter: Playwright smoke tests

## PR Expectations

Every agent PR should include:

- Linked issue.
- Plan approval reference.
- Summary of changed behavior.
- Tests and checks run locally.
- Screenshots or recordings for UI changes.
- Known limitations or follow-up work.
- Review comment resolution notes when applicable.

## Automation Options

You can run this loop manually at first. Add automation only where it removes real coordination cost.

Low-complexity setup:

- Humans label issues with `agent:ready`.
- Agent is started manually from the issue.
- GitHub Actions runs checks on PRs.
- CodeRabbit or another reviewer comments on PRs.
- Agent is manually prompted to address PR comments.

More automated setup:

- A GitHub Action watches for `agent:ready`.
- The action posts or dispatches an agent job.
- The agent posts its plan back to the issue.
- A human approval label or comment unlocks the build step.
- The agent opens a draft PR and updates it until CI and reviews pass.

Keep the approval checkpoint explicit even in the automated setup.

---
name: Linear Integration
description: Utilities and guidelines for interacting with Linear in this project.
---

# Linear Integration Skill

This skill provides context and helpers for managing tasks in Linear for the OCR Dashboard project.

## Configuration

- **Team Name**: tomaasz
- **Team ID**: b1b11ec2-933e-4115-b272-641ec466940a

## Usage

### Creating Issues

Use the `mcp_linear_create_issue` tool.

- Always assign to team `b1b11ec2-933e-4115-b272-641ec466940a`.
- **Assignee**:
  - Fetch the current user's email from GitHub using `github-mcp-server:get_me`.
  - Find the corresponding Linear user ID using `linear-mcp-server:list_users` with a filter for that email.
  - Set `assigneeId` to the found user ID.
- Use descriptive titles.
- Add relevant labels if available.

### Listing Issues

Use `mcp_linear_list_issues` to see current work.

- Filter by `teamId` to ensure you see relevant issues.

## Workflows

- See `.agent/workflows/create-issue.md` for the simplified issue creation workflow.

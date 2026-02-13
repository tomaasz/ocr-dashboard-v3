---
description: Create a new issue in Linear
---

# Create Linear Issue

This workflow simplifies creating a new issue in the 'tomaasz' team on Linear9.

1.  **Identify the Issue**: Determine the title and description for the issue.
2.  **Get User Information**:
    - Call `github-mcp-server:get_me` to get the current user's email.
    - Call `linear-mcp-server:list_users` (filtering by the email from step 2a) to get the Linear user ID.
3.  **Create Issue**: Use the `mcp_linear_create_issue` tool.
    - **Team ID**: `b1b11ec2-933e-4115-b272-641ec466940a`
    - **Title**: [Issue Title]
    - **Description**: [Issue Description]
    - **Assignee**: [User ID from step 2b]
    - **Priority**: 0 (No priority by default, unless specified)

4.  **Confirm**: Output the URL of the created issue.

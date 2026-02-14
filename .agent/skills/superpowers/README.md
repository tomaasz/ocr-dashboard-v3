# Superpowers Skills - Installed

**Installation Date:** 2026-02-14  
**Source:** https://github.com/obra/superpowers

## What is Superpowers?

Superpowers is a complete software development workflow framework for coding agents, built on composable "skills" that guide development best practices.

## Installed Skills

### 🎯 Core Workflow

- **brainstorming** - Socratic design refinement before coding
- **writing-plans** - Detailed implementation plans with 2-5 min tasks
- **executing-plans** - Batch execution with checkpoints
- **subagent-driven-development** - Fast iteration with two-stage review

### 🧪 Testing & Quality

- **test-driven-development** - RED-GREEN-REFACTOR cycle enforcement
- **requesting-code-review** - Pre-review checklist
- **receiving-code-review** - Responding to feedback
- **verification-before-completion** - Ensure fixes actually work

### 🐛 Debugging

- **systematic-debugging** - 4-phase root cause process
- **verification-before-completion** - Verify the fix works

### 🔧 Development Tools

- **using-git-worktrees** - Parallel development branches
- **finishing-a-development-branch** - Merge/PR decision workflow
- **dispatching-parallel-agents** - Concurrent subagent workflows

### 📚 Meta

- **using-superpowers** - Introduction to the skills system
- **writing-skills** - Create new skills following best practices

## How Skills Work

Skills are automatically discovered by Antigravity from the `.agent/skills/` directory. Each skill:

- Has a `SKILL.md` file with YAML frontmatter
- Includes a `description` that triggers automatic activation
- Contains detailed instructions for the agent to follow

## Usage

Skills activate automatically when relevant. The agent will:

1. Check for relevant skills before any task
2. Invoke the skill if there's even a 1% chance it applies
3. Follow the skill instructions exactly

You don't need to manually invoke skills - they're part of the agent's workflow.

## Updating

To update Superpowers skills:

```bash
cd /tmp
git clone --depth 1 https://github.com/obra/superpowers.git
rm -rf /home/tomaasz/ocr-dashboard-v3/.agent/skills/superpowers
cp -r /tmp/superpowers/skills /home/tomaasz/ocr-dashboard-v3/.agent/skills/superpowers
rm -rf /tmp/superpowers
```

## Documentation

- **Main Repository:** https://github.com/obra/superpowers
- **Release Notes:** https://github.com/obra/superpowers/blob/main/RELEASE-NOTES.md
- **Issues:** https://github.com/obra/superpowers/issues

# Available Skills

Skills are loaded from `~/.claude/skills/`. Each provides specialized knowledge for specific domains.

## Project-Relevant Skills

### Langfuse (`~/.claude/skills/langfuse/skills/`)

**When to invoke**: Any Langfuse-related task — tracing setup, debugging missing traces, querying traces/prompts/datasets, prompt management, evaluation setup.

**Key files**:
- `SKILL.md` — Main Langfuse skill
- `agents.md` — Agent instructions for skill maintenance

**Use for**:
- Debugging why traces don't appear in UI
- Setting up Langfuse prompt management
- Querying traces via API
- Migrating between Langfuse SDK versions
- Configuring evaluations and scoring

**Notes**: Our project uses Langfuse v4.8.1 (OTEL-based). The skill may reference v2 patterns — always prefer v4 APIs. See [[langfuse-v4-tracing]] for project-specific gotchas.

### DeepSeek (`~/.claude/skills/deepseek/`)

**When to invoke**: DeepSeek API usage, DeepSeek model selection, deepseek-reasoner specific behavior, comparison between DeepSeek and other providers.

**Key files**:
- `awesome-deepseek-agent/README.md` — Curated list of DeepSeek tools and agents
- `awesome-deepseek-agent/docs/` — Guides for various DeepSeek integrations

**Use for**:
- Understanding DeepSeek API quirks (e.g., reasoner rejects temperature)
- Best practices for prompt engineering with DeepSeek
- Comparing deepseek-chat vs deepseek-reasoner for specific tasks

**Notes**: Our project's AI abstraction in `core/ai/` already handles DeepSeek-specific quirks (temperature stripping, `<think>` block removal). See [[ai-provider-abstraction]].

### Anthropic/Claude (`~/.claude/skills/anthropic/skills/`)

**When to invoke**: Claude API usage, Anthropic SDK, prompt caching, thinking/compaction, tool use patterns, model migration (4.5→4.6→4.7).

**Key files**:
- `skills/skills/web-artifacts-builder/SKILL.md` — Building complex HTML artifacts with React + Tailwind
- `skills/spec/agent-skills-spec.md` — Agent Skills specification
- `skills/template/SKILL.md` — Template for creating new skills

**Use for**:
- Optimizing Claude API calls (caching, thinking budget)
- Migrating between Claude model versions
- Building HTML artifacts for POC demos
- Understanding the Agent Skills standard

**Notes**: The `web-artifacts-builder` skill is useful for enhancing POC output quality when the coder agent generates HTML.

## Using Skills

Skills auto-trigger based on their frontmatter `description`. Key words like "Langfuse", "DeepSeek", "Claude API" will invoke the appropriate skill.

To explicitly invoke a skill:
```
/langfuse  (for Langfuse tasks)
/deepseek  (for DeepSeek tasks)
/claude-api (for Claude API tasks)
```

## Creating New Skills

Use the template at `~/.claude/skills/anthropic/skills/template/SKILL.md`:

```markdown
---
name: my-skill-name
description: What the skill does and when Claude should use it.
---

# Instructions
...
```

Skills go in `.claude/skills/<skill-name>/SKILL.md` for project-level, or `~/.claude/skills/<skill-name>/` for global.

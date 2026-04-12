---
title: zero-strike Project Context
description: Core project context for OpenAgentsControl agents
tags: [project, context]
---

# zero-strike

## Overview
This project uses OpenAgentsControl (developer profile) with global installation at `~/.config/opencode/`.

## AI Providers
- **Primary**: Anthropic (Claude) — `anthropic/claude-sonnet-4-6`
- **Secondary**: OpenAI (GPT)
- API keys are set via environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

## Project Structure
- `opencode.json` — opencode provider and model configuration
- `.env` — API key environment variables (not committed)
- `.opencode/context/project/` — project-specific agent context

## Development Notes
- OpenAgentsControl installed globally at `~/.config/opencode/`
- Use `opencode --agent OpenAgent` for general tasks
- Use `opencode --agent OpenCoder` for production coding tasks
- Agents propose a plan before executing — always approve before changes are made

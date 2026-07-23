# Nayruuu Agent Skills

Reusable, vendor-neutral skills for AI agents and deterministic tools that can
also run without an agent.

## Available skills

### `reclaim-storage`

Safely audit disk usage and prepare exact cleanup plans on macOS, Linux, and
Windows. It includes a standard-library Python CLI for read-only `audit`,
non-executable `plan`, and post-action `verify` workflows. The CLI never deletes
data.

Install only this skill:

```bash
npx skills add Nayruuu/agent-skills --skill reclaim-storage
```

Install every skill in this repository:

```bash
npx skills add Nayruuu/agent-skills --skill '*'
```

Each skill is self-contained under `skills/<skill-name>/` and includes its own
`SKILL.md`, references, scripts, and tests where needed.

## License

MIT. See [LICENSE](LICENSE).

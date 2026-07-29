"""The task tree — one vertical slice per business capability (doc 02 §1).

```
tasks/
  _shared/                      schema fragments reused across skills
  <task>/
    task.yml                    id, title, description, enabled
    skills/<skill>/             ONE router-visible capability
      schema.py                 class Args(BaseModel); SKILL_META
      skill.py                  run(ctx, args) -> SkillResult
      tools/                    deterministic helpers; never call an LLM
      subskills/                internal steps, invoked in code by the skill
      tests/
```

Only ``skills/*`` are exposed to the router (decision D3); subskills, tools
and subtools are called by the skill in a fixed order, in code. A skill's id
is ``"<task>.<skill>"`` and comes from the directory names —
:class:`~poseidon.core.skills.registry.SkillRegistry` walks this tree, so
adding a skill is adding a directory, and there is nothing to register.

This package deliberately imports nothing: discovery imports task modules
lazily, and a task that is disabled must never be imported at all.
"""

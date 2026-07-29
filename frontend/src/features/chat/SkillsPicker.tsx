import { useState } from "react";

interface Skill {
  id: string;
  name: string;
  description: string;
  /** A runnable example question -- present on the curated fallback list;
   * GET /api/skills carries no such field (see ApiSkill below), so a
   * skill sourced from there has none. */
  example?: string;
}

/** The wire shape of GET /api/skills (poseidon.api.live_chat.list_skills):
 * registry-backed, no curated example prompt. */
interface ApiSkill {
  id: string;
  label: string;
  description: string;
}

/** The registered skills, surfaced for discovery only — the backend router still decides.
 * Also this component's fallback: what renders when GET /api/skills fails
 * (mock mode has no such route; live mode's own fetch can still fail offline). */
const FALLBACK_SKILLS: Skill[] = [
  {
    id: "metric_query",
    name: "Metric query",
    description: "Ask the warehouse for a number.",
    example: "Top GP customers for Port of Singapore in April 2026",
  },
  {
    id: "web_research",
    name: "Web research",
    description: "Search the open web for market and company news.",
    example: "Any recent news on Northstar Lines?",
  },
  {
    id: "existing_customer_brief",
    name: "Existing customer brief",
    description: "Account review for a customer you already trade with.",
    example: "Run the existing-customer brief for …",
  },
  {
    id: "new_prospect_brief",
    name: "New prospect brief",
    description: "Research a company you do not trade with yet.",
    example: "Research prospect …",
  },
];

export interface SkillsPickerProps {
  /** Receives the chosen skill's example prompt (or, for a registry-backed
   * skill with no curated example, its label) as a composer starter. */
  onPick: (example: string) => void;
}

/** Composer affordance: a popover of skills with an example prompt each.
 * Fetches the real, registered skill list from GET /api/skills each time it
 * opens, falling back to the curated static list above on any failure
 * (offline, a network error, or mock mode's own 404 — that route is
 * live-chat-only, see live_chat.py's module docstring) so the picker is
 * never empty. */
export function SkillsPicker({ onPick }: SkillsPickerProps) {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<Skill[]>(FALLBACK_SKILLS);

  async function loadSkills(): Promise<void> {
    try {
      const response = await fetch("/api/skills");
      if (!response.ok) throw new Error(`request failed: ${response.status}`);
      const body = (await response.json()) as ApiSkill[];
      setSkills(
        body.map((skill) => ({ id: skill.id, name: skill.label, description: skill.description })),
      );
    } catch {
      setSkills(FALLBACK_SKILLS);
    }
  }

  return (
    <div
      className="skills"
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <button
        type="button"
        className="skills-button"
        aria-expanded={open}
        onClick={() => {
          const willOpen = !open;
          setOpen(willOpen);
          if (willOpen) void loadSkills();
        }}
      >
        Skills
      </button>
      {open ? (
        // Plain buttons in tab order rather than an ARIA menu: a `role="menu"`
        // would promise arrow-key navigation this popover does not implement.
        <div className="skills-popover" role="group" aria-label="Skills">
          {skills.map((skill) => (
            <button
              key={skill.id}
              type="button"
              className="skill-item"
              onClick={() => {
                onPick(skill.example ?? skill.name);
                setOpen(false);
              }}
            >
              <span className="skill-name">{skill.name}</span>
              <span className="skill-desc">{skill.description}</span>
              {skill.example ? <span className="skill-example">“{skill.example}”</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

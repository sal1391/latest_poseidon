import { useState } from "react";

interface Skill {
  id: string;
  name: string;
  description: string;
  example: string;
}

/** The registered skills, surfaced for discovery only — the backend router still decides. */
const SKILLS: Skill[] = [
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
  /** Receives the chosen skill's example prompt as a composer starter. */
  onPick: (example: string) => void;
}

/** Composer affordance: a popover of preset skills with an example prompt each. */
export function SkillsPicker({ onPick }: SkillsPickerProps) {
  const [open, setOpen] = useState(false);

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
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        Skills
      </button>
      {open ? (
        // Plain buttons in tab order rather than an ARIA menu: a `role="menu"`
        // would promise arrow-key navigation this popover does not implement.
        <div className="skills-popover" role="group" aria-label="Skills">
          {SKILLS.map((skill) => (
            <button
              key={skill.id}
              type="button"
              className="skill-item"
              onClick={() => {
                onPick(skill.example);
                setOpen(false);
              }}
            >
              <span className="skill-name">{skill.name}</span>
              <span className="skill-desc">{skill.description}</span>
              <span className="skill-example">“{skill.example}”</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

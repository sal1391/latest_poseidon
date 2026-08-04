import { useCallback, useEffect, useRef, useState } from "react";
import { listSkills } from "../../api/client";

interface Skill {
  id: string;
  name: string;
  description: string;
  /** A runnable example question -- present on the curated fallback list;
   * GET /api/skills carries no such field (see api/types.ts's SkillSummary),
   * so a skill sourced from there has none. */
  example?: string;
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

// Final-review wave item 12 (M4): a lookup from BARE skill name (the
// segment after a dotted id's last part -- see live_chat.py's own
// `skill_label`/`bareSkillName` derivation) to its curated example, built
// from the curated list above. GET /api/skills returns namespaced ids
// (e.g. "data_qa.metric_query"), so a registry-backed skill needs its bare
// name computed before it can find its match here -- without this map,
// EVERY registry-backed skill fell back to inserting its bare label instead
// of a runnable example, even when the exact same skill's curated example
// already existed one line above.
const EXAMPLES_BY_BARE_NAME: Record<string, string> = Object.fromEntries(
  FALLBACK_SKILLS.filter(
    (skill): skill is Skill & { example: string } => skill.example !== undefined,
  ).map((skill) => [skill.id, skill.example]),
);

function bareSkillName(id: string): string {
  const dot = id.lastIndexOf(".");
  return dot === -1 ? id : id.slice(dot + 1);
}

// Review fix round 1, Important #1: whether `target` (or an ancestor, up to
// but excluding `document.body`) is itself a normally-focusable element --
// i.e. something a real mousedown's own native default action would hand
// focus to (a `<button>`, the composer's `<input>`, another sidebar row,
// ...). `HTMLElement.tabIndex` reports 0+ for every element the browser
// treats as focusable-by-default (form controls, links with `href`, or
// anything carrying an explicit `tabindex`), and -1 for everything else
// (a plain `<div>`, page chrome) -- checked up the ancestor chain rather
// than on `target` alone since a click often lands on an inner child (an
// icon, a text node's containing span) of the real focusable element, not
// that element itself.
function isOrContainsFocusable(target: EventTarget | null): boolean {
  let el = target instanceof Element ? target : null;
  while (el && el !== document.body) {
    if (el instanceof HTMLElement && el.tabIndex >= 0) return true;
    el = el.parentElement;
  }
  return false;
}

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
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Phase 12 Task 4 (a11y carry-list, verbatim): closing the popover WITHOUT
  // completing a pick (outside click, Escape) returns focus to the trigger
  // that opened it, so a keyboard/screen-reader user backing out never loses
  // their place. A completed PICK is deliberately different: `onPick` below
  // moves focus into the composer itself (ChatScreen's own `insert`), which
  // is the more useful place to land once a skill was actually chosen -- so
  // that path sets `open` false directly, never through this function.
  const closeAndReturnFocus = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    // Listens on `mousedown`, and calls `preventDefault()` for an outside
    // target -- deliberately NOT racing to re-focus the trigger AFTER the
    // fact. Clicking a non-focusable outside target (e.g. plain page
    // chrome) carries the browser's OWN default action for that gesture --
    // moving focus away from whatever currently holds it -- and this is not
    // merely "done synchronously before listeners finish", so a `.focus()`
    // call made from ANY listener on this same event, or even queued from
    // one via a microtask or a `setTimeout(0)` macrotask, still loses the
    // race and gets clobbered right back to nothing focused. This was
    // live-verified against a real Chromium build via Playwright, not just
    // this suite's jsdom (which never reproduced any of it): an
    // instrumented `HTMLElement.prototype.focus` proved a synchronous call
    // from inside a listener DOES land (the trigger was still active at the
    // moment it ran) and is undone regardless. `preventDefault()` on the
    // mousedown itself is the one thing that works -- it cancels the
    // browser's blur-on-mousedown-to-non-focusable-target step outright, so
    // the trigger is simply never blurred in the first place, and the
    // explicit `.focus()` in `closeAndReturnFocus` above is then just a
    // (harmless, and for Escape's keyboard-only path, load-bearing) belt.
    //
    // Review fix round 1, Important #1: that `preventDefault()` must NOT
    // fire for an outside target that is ITSELF a real focusable control
    // (the composer's `<input>` is a SIBLING of this component, not a
    // descendant -- Composer.tsx -- so it counts as "outside" `containerRef`
    // too). Cancelling the mousedown's default action there would cancel
    // the composer's own native focus-transfer along with it, silently
    // stealing a click meant to focus the composer back onto the Skills
    // trigger instead. `isOrContainsFocusable` draws that line: only page
    // chrome that the browser was never going to focus anyway gets the
    // preventDefault treatment; a click on another real control just closes
    // the popover and lets that control receive focus normally.
    function handleOutsideMouseDown(event: MouseEvent): void {
      if (!containerRef.current || containerRef.current.contains(event.target as Node)) return;
      if (isOrContainsFocusable(event.target)) {
        setOpen(false);
        return;
      }
      event.preventDefault();
      closeAndReturnFocus();
    }
    document.addEventListener("mousedown", handleOutsideMouseDown);
    return () => document.removeEventListener("mousedown", handleOutsideMouseDown);
  }, [open, closeAndReturnFocus]);

  async function loadSkills(): Promise<void> {
    try {
      const body = await listSkills();
      setSkills(
        body.map((skill) => ({
          id: skill.id,
          name: skill.label,
          description: skill.description,
          example: EXAMPLES_BY_BARE_NAME[bareSkillName(skill.id)],
        })),
      );
    } catch {
      setSkills(FALLBACK_SKILLS);
    }
  }

  return (
    <div
      ref={containerRef}
      className="skills"
      onKeyDown={(event) => {
        if (event.key === "Escape") closeAndReturnFocus();
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className="skills-button"
        aria-haspopup="true"
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

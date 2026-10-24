// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Badge } from "./Badge";
import { Disclosure } from "./Disclosure";

afterEach(cleanup);

describe("Disclosure", () => {
  it("is closed by default and shows the summary and the body", () => {
    render(<Disclosure summary="How this was computed">the body</Disclosure>);
    const details = screen.getByText("How this was computed").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByText("the body")).toBeTruthy();
  });

  it("opens when asked, with a heading as its summary", () => {
    render(
      <Disclosure defaultOpen section summary={<h2>Contextualize</h2>}>
        the body
      </Disclosure>,
    );
    const heading = screen.getByText("Contextualize");
    expect(heading.tagName).toBe("H2");
    expect((heading.closest("details") as HTMLDetailsElement).open).toBe(true);
  });
});

describe("Badge", () => {
  it("renders its text inline", () => {
    render(<Badge>application/pdf</Badge>);
    expect(screen.getByText("application/pdf").tagName).toBe("SPAN");
  });

  it("accepts the warn tone", () => {
    render(<Badge tone="warn">Month to date</Badge>);
    expect(screen.getByText("Month to date")).toBeTruthy();
    expect(screen.getByText("Month to date").className).toContain("bg-warn-bg");
  });
});

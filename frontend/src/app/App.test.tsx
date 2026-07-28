import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders the app shell with brand and composer placeholder", () => {
  render(<App />);
  expect(screen.getByText("Poseidon")).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/message poseidon/i)).toBeInTheDocument();
});

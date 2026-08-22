import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Avatar from "./avatar";

describe("Avatar", () => {
  it("renders the author's picture and name", () => {
    render(<Avatar name="Jane Doe" picture="/assets/blog/authors/jane.jpeg" />);

    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Jane Doe" })).toHaveAttribute(
      "src",
      "/assets/blog/authors/jane.jpeg",
    );
  });
});

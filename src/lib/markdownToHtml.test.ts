import { describe, expect, it } from "vitest";

import markdownToHtml from "./markdownToHtml";

describe("markdownToHtml", () => {
  it("converts markdown syntax into html", async () => {
    await expect(markdownToHtml("# Hello\n\nThis is **bold**.")).resolves.toBe(
      "<h1>Hello</h1>\n<p>This is <strong>bold</strong>.</p>\n",
    );
  });
});

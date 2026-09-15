// Author: elecvoid243
// Date: 2026-09-15
// Fork-specific Shiki languages: verilog / system-verilog / matlab.
//
// These three are the languages this fork added on top of upstream's limited
// bundle (FPGA/Vivado and scientific work). Upstream's
// `shikiLimitedBundle.test.mjs` only covers its own expanded set, so a future
// merge that resolves `shikiLimitedBundle.js` towards upstream would silently
// drop them. This test is the guard: it pins the *union* — the three fork
// languages must stay loadable and renderable next to upstream's additions
// (c, cpp, csharp, dart, go, kotlin, lua, php, r, ruby, rust, scala, swift).
//
// Run with: cd dashboard && node --test tests/shikiForkLanguages.test.mjs

import assert from "node:assert/strict";
import test from "node:test";

import {
  LIMITED_SHIKI_SUPPORTED_LANGUAGES,
  createHighlighter,
  normalizeLimitedShikiLanguage,
} from "../src/utils/shikiLimitedBundle.js";

const FORK_LANGUAGE_CASES = {
  matlab: ["matlab"],
  verilog: ["verilog", "v", "vh"],
  "system-verilog": ["system-verilog", "sv", "svh", "systemverilog"],
};

const UNION_LANGUAGES = [
  "c",
  "cpp",
  "csharp",
  "dart",
  "go",
  "kotlin",
  "lua",
  "php",
  "r",
  "ruby",
  "rust",
  "scala",
  "swift",
  ...Object.keys(FORK_LANGUAGE_CASES),
];

test("keeps the fork languages alongside upstream's expanded set", () => {
  for (const [language, aliases] of Object.entries(FORK_LANGUAGE_CASES)) {
    assert.equal(normalizeLimitedShikiLanguage(language), language, language);
    for (const alias of aliases) {
      assert.equal(normalizeLimitedShikiLanguage(alias), language, alias);
    }
  }

  for (const language of UNION_LANGUAGES) {
    assert.ok(LIMITED_SHIKI_SUPPORTED_LANGUAGES.has(language), language);
  }
});

test("highlights every union language with Shiki tokens", async () => {
  const highlighter = await createHighlighter({ themes: ["github-light"] });
  const samples = {
    matlab: "x = 1:10;",
    verilog: "module top; endmodule",
    "system-verilog": "module top; logic a; endmodule",
    csharp: "public class Sample {}",
    dart: "void main() {}",
    kotlin: "fun main() {}",
    lua: "local value = 1",
    php: "<?php echo 'ok';",
    r: "value <- 1",
    ruby: "puts 'ok'",
    scala: "object Main extends App {}",
    swift: "let value = 1",
  };

  for (const [language, code] of Object.entries(samples)) {
    assert.equal(highlighter.getLanguage(language)?.name, language, language);
    const html = highlighter.codeToHtml(code, {
      lang: language,
      theme: "github-light",
    });
    assert.match(html, /<span[^>]+style=/, language);
  }
});

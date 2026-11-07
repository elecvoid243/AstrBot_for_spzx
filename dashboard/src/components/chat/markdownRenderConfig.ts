import "./chatMarkdown.css";

export const MARKDOWN_RENDER_MAX_LIVE_NODES = 320;

// 2026-11-07 (elecvoid243): one type step down from the previous 1.5 / 1.25 /
// 1.125 / 1 / 1 / 1rem scale. Body text moved 15 -> 14px (desktop) in
// chatMarkdown.css; keeping h4 at 1rem would have flattened the bottom of the
// hierarchy, so the whole ladder shifts with it.
export const CHAT_MARKDOWN_HEADING_STYLE = {
  "--ms-text-h1": "1.375rem",
  "--ms-text-h2": "1.125rem",
  "--ms-text-h3": "1rem",
  "--ms-text-h4": "0.9375rem",
  "--ms-text-h5": "0.9375rem",
  "--ms-text-h6": "0.9375rem",
  "--ms-flow-heading-1-mt": "0",
  "--ms-flow-heading-1-mb": "0.5em",
  "--ms-flow-heading-2-mt": "1em",
  "--ms-flow-heading-2-mb": "0.4em",
  "--ms-flow-heading-3-mt": "0.8em",
  "--ms-flow-heading-3-mb": "0.3em",
  "--ms-flow-heading-4-mt": "0.6em",
  "--ms-flow-heading-4-mb": "0.2em",
  "--ms-flow-heading-5-mt": "0.4em",
  "--ms-flow-heading-5-mb": "0.15em",
  "--ms-flow-heading-6-mt": "0.4em",
  "--ms-flow-heading-6-mb": "0.15em",
};

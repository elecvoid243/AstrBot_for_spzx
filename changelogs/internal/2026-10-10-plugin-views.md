# Plugin Views and scoped asset tokens

- Integrate upstream #10415 with the required Views naming changes, preserving the local sidebar layout and plugin extension APIs.
- Rename Plugin Pages to Plugin Views, Open Plugin UI to Open View, and `/plugin-page/:pluginName/:pageName` to `/plugin-view/:pluginName/:pageName`. Existing page links redirect with their query parameters.
- Prefer `views/`, `views` metadata, and `views.*` translations while retaining their `pages` aliases and `AstrBotPluginPage` bridge alias.
- Serve static assets through scoped path tokens without rewriting relative HTML, CSS, or JavaScript asset references. Keep the upstream seven-day token lifetime and remove obsolete asset routes.
- Preserve the local iframe permissions for storage, authenticated fetches, nested panels, and popups, including existing memory plugin panels.
- Regenerate the API client, OpenAPI document, and bilingual scope references.

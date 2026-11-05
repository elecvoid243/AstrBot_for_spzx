# Context Compression

Starting from v4.11.0, AstrBot introduced an automatic context compression feature.

On the `Config` page, select the profile to edit, open `AI → Advanced → Context Management Strategy`, set the compression strategy, and click `Save Configuration` at the bottom right.

AstrBot checks context before every model request, including tool continuations. A positive provider `compress_threshold_tokens` overrides the normal trigger of 82% of the model window. Input estimation includes system instructions, messages, and tool definitions. Input plus the effective output budget must fit the window. Summaries can omit details.

## Compression Strategies

There are currently two compression strategies:

1. Truncate by conversation rounds, removing older complete rounds. Token-budget truncation targets 40% of the model window by default, retaining the latest round and identified summaries.
2. LLM summaries, the default for new configurations. Choose a summary model or leave it empty to try the session's chat model. The default `keep_recent_ratio` of `0.15` budgets recent exact content. You can also customize the compression instruction:

```
Based on our full conversation history, produce a concise summary of key takeaways and/or project progress.
1. Systematically cover all core topics discussed and the final conclusion/outcome for each; clearly highlight the latest primary focus.
2. If any tools were used, summarize tool usage (total call count) and extract the most valuable insights from tool outputs.
3. If there was an initial user goal, state it first and describe the current progress/status.
4. Write the summary in the user's language.
```

If pressure remains after compression, AstrBot removes the oldest ordinary rounds while preserving system messages, summaries, and the current complete round. Exceeding only the compression trigger does not force protected content to be removed. If protected content, tool definitions, and the output budget still exceed the model window, the request stops with an explicit capacity error. Reduce the current input, tool definitions, or output limit, or select a larger model window.

- AstrBot will invoke the compressor for checking before each conversation request.
- A long round containing multiple tool batches can summarize earlier complete batches while retaining the current user request, latest tool batch, and recent exact content. Parallel calls and their results stay together. Unconfirmed outcomes and system instructions remain intact. Compression never executes a tool again.

## Tool-output pruning and summary retention

When a reading tool and output directory are available, the built-in Runner first tries to shorten oversized historical tool results under context pressure. It saves the full text and keeps a head/tail preview, a readable file reference, and non-text content in order. The complete preview fits approximately 7,000 estimated tokens. Failed writes leave that historical result unchanged. If pruning releases enough space, no summary request is needed. Immediate offloading of new results above approximately 27,500 estimated tokens remains in effect.

Generated summaries carry an internal identity that survives history storage, recovery, and model changes without being sent to providers. A successful smaller summary may consolidate older summaries. Empty summaries, growing summaries, and inputs changed during generation do not replace history. Failed summarization preserves existing identities; ordinary old rounds may still be truncated.

Unmarked older summaries do not receive protection based on their wording, which could misidentify user messages. When useful history exists, send `/compact` to generate an identified replacement manually. A round with only one tool batch may have no safe useful range to summarize.

## ‼️ Important: Model Context Window Settings

By default, when you add a model, AstrBot automatically retrieves the model's context window size from the API provided by [MODELS.DEV](https://models.dev/) based on the model's ID. However, due to the wide variety of models and the fact that some providers even modify the model IDs, AstrBot cannot automatically infer the context window size for all models you add.

Open `Providers → Chat Completion`, select the model's provider in the left panel, and click an added model in the model list on the right. Edit `max_context_tokens` in the model configuration and save it.

When a window is unspecified, AstrBot tries model metadata from MODELS.DEV and the configured fallback window. Custom model names may not be recognized, so configure their capacity explicitly. The summary model also needs enough space for its input and output budget.

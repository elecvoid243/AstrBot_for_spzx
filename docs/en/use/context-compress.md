# Context Compression

Starting from v4.11.0, AstrBot introduced an automatic context compression feature.

On the `Config` page, select the profile to edit, open `AI → Advanced → Context Management Strategy`, configure context management, and click `Save Configuration` at the bottom right.

AstrBot checks context before every model request, including tool continuations. The trigger is `min(window × 0.8, window − output budget − headroom)`; a positive provider `compress_threshold_tokens` overrides it, but is still clamped to the same pressure budget. Input estimation includes system instructions, messages, and tool definitions. Input plus the effective output budget must fit the window. Summaries can omit details.

## Compression method

The default method is an LLM-generated structured summary. Choose a summary model or leave it empty to try the session's chat model. The compression instruction is a built-in fixed template and is no longer configurable.

Selecting `Token Budget` (taken from Codex's implementation) replaces the window without a separate model call. It keeps the system instructions and the current round (the current user request plus its tool batches) and moves older rounds out of the model context into the archive. Window state (window number, the water level already announced, whether the exhausted notice was sent) is stored with the history, so a restart resumes the same window. Round granularity keeps a tool call and its results together; a current round that alone exceeds the window still stops with a capacity error. A reset only changes the history the model sees — WebChat transcripts are untouched.

Each window has four budget reminders. They are English control messages sent to the model and saved in its conversation history:

- **25%**: Plan the remaining work and start `task-checkpoint.md` if the task may span windows.
- **50%**: Limit what you read and update the checkpoint with verified progress and decisions.
- **75%**: Finish or safely pause the current step. Update the checkpoint before any tool call likely to produce a large result. If a fresh window would help, confirm the write and switch windows.
- **90%**: Hand off any unfinished task immediately. Stop starting new work and use `notes_write` to record the goal, constraints, verification results, decisions, unverified work, blockers, next action, completion criteria, and references. Confirm the write succeeded before resetting and continuing the task.

To resume after a reset, read `task-checkpoint.md` if it applies to the current task, then use history tools for missing details. If a request crosses several levels at once, only the highest new notice is sent. Percentages are measured against the compression trigger. If trimming still leaves the request above that trigger, the exhausted notice takes priority and asks the model to report its current result, remaining work, and blocker.

Truncation by conversation rounds is no longer a separate strategy, and there is no "discard N turns / drop to 40%" setting. If pressure remains after the summary, AstrBot removes the oldest ordinary rounds one at a time, preserving system messages, summaries, and the current complete round. Exceeding only the compression trigger does not force protected content to be removed. If protected content, tool definitions, and the output budget still exceed the model window, the request stops with an explicit capacity error. Reduce the current input, tool definitions, or output limit, or select a larger model window.

- AstrBot will invoke the compressor for checking before each conversation request.
- A long round containing multiple tool batches can summarize earlier complete batches while retaining the current user request, latest tool batch, and recent exact content. Parallel calls and their results stay together. Unconfirmed outcomes and system instructions remain intact. Compression never executes a tool again.

## Threshold, target, and retries

- Trigger: `min(window × 0.8, window − output budget − headroom)`. A provider-level `compress_threshold_tokens` still overrides it, but is clamped to the same pressure budget.
- Retained tail: `keep_recent_ratio` (default `0.15`) of the message budget, always on a balanced tool-call/result boundary; a batch is never split.
- Target: compaction aims at 55% of the window. If one summary is not enough, the same step retries once; a remaining gap is reported as insufficient and falls back to round dropping or a capacity error.
- The full-history checkpoint is a single user message carrying the preamble and `<compacted-summary>` tags; in-round compaction keeps an assistant checkpoint so the exact current user request stays untouched.
- Provider-level `compaction_headroom_tokens` (default `16384`, scaled down for small windows) and `compress_threshold_ratio` (default `0.8`) control the trigger position.

## Tool-output pruning and summary retention

When a reading tool and output directory are available, the built-in Runner first tries to shorten oversized historical tool results under context pressure. It saves the full text and keeps a head/tail preview, a readable file reference, and non-text content in order. The complete preview fits approximately 7,000 estimated tokens. Failed writes leave that historical result unchanged. If pruning releases enough space, no summary request is needed. Immediate offloading of new results above approximately 27,500 estimated tokens remains in effect.

Generated summaries carry an internal identity that survives history storage, recovery, and model changes without being sent to providers. A successful smaller summary may consolidate older summaries. Empty summaries, growing summaries, and inputs changed during generation do not replace history. Failed summarization preserves existing identities; ordinary old rounds may still be truncated.

Unmarked older summaries do not receive protection based on their wording, which could misidentify user messages. When useful history exists, send `/compact` to generate an identified replacement manually. A round with only one tool batch may have no safe useful range to summarize.

## ‼️ Important: Model Context Window Settings

By default, when you add a model, AstrBot automatically retrieves the model's context window size from the API provided by [MODELS.DEV](https://models.dev/) based on the model's ID. However, due to the wide variety of models and the fact that some providers even modify the model IDs, AstrBot cannot automatically infer the context window size for all models you add.

Open `Providers → Chat Completion`, select the model's provider in the left panel, and click an added model in the model list on the right. Edit `max_context_tokens` in the model configuration and save it.

When a window is unspecified, AstrBot tries model metadata from MODELS.DEV and the configured fallback window. Custom model names may not be recognized, so configure their capacity explicitly. The summary model also needs enough space for its input and output budget.

# Built-in Agent model-boundary recovery

Interrupted workflow tasks can continue the original Attempt from a saved model request or complete response. Effective temporary context, multimodal references, provider thinking/signatures, configuration references and counters survive process termination. A complete saved response is reused before executing its original pending calls. Missing responses may be regenerated; partial generation and remote billing deduplication are not guaranteed.

Execution ownership rejects late checkpoint, process, file and result writes. Required persistence failures stop dependent tools. Started tools retain their evidence and block replay; tool-result recovery and reconciliation remain separate work. Default text submissions can finish idempotently at their durable boundary, preserving one artifact version and one completion event.

Entry mapping: an interrupted task previously exposed only saved process records. Open **Independent Runs → View Run → select task → Continue same attempt** for supported boundaries and explicit blocking reasons. Existing asset, role and legacy team entries are unchanged. The authenticated recovery endpoint and generated frontend client are synchronized; English/Chinese workflow documentation and actual WebUI screenshots describe this operation.

export function sessionRowKey({
  session_id,
  run_conversation,
}: {
  session_id: string;
  run_conversation?: { run_id: string };
}): string {
  return run_conversation ? `run:${run_conversation.run_id}` : `chat:${session_id}`;
}

export function selectedSessionIds(keys: ReadonlySet<string>) {
  const session_ids: string[] = [];
  const run_ids: string[] = [];
  for (const key of keys) {
    if (key.startsWith("run:")) run_ids.push(key.slice(4));
    else session_ids.push(key.slice(5));
  }
  return { session_ids, run_ids };
}

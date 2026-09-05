// Stable color helpers shared by the agent-teams UI (extracted verbatim from
// the retired useAgentCollab composable so member coloring survives collab
// removal).

const COLLAB_COLORS = [
  '#e53935',
  '#8e24aa',
  '#3949ab',
  '#00897b',
  '#f4511e',
  '#d81b60',
  '#6d4c41',
  '#43a047',
];

function colorOf(key: string) {
  const hash = [...key].reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  return COLLAB_COLORS[hash % COLLAB_COLORS.length];
}

export function collabGroupColor(groupId: string) {
  return colorOf(groupId);
}

export function collabMemberColor(sessionId: string) {
  return colorOf(sessionId);
}

export function collabWithAlpha(hex: string, alpha: number) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

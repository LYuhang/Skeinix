export function readSelectedNodeId(): string | null {
  const element = document.querySelector<HTMLElement>('.react-flow__node.selected[data-id]');
  return element?.getAttribute('data-id') ?? null;
}

export function readSelectedEdge(): { source: string; target: string } | null {
  const element = document.querySelector<HTMLElement>('.react-flow__edge.selected[data-id]');
  const id = element?.getAttribute('data-id');
  if (!id) return null;
  const separator = id.indexOf('->');
  if (separator < 0) return null;
  return { source: id.slice(0, separator), target: id.slice(separator + 2) };
}

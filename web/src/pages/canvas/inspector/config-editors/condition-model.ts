export interface ConditionEntry {
  condition_name?: string;
  condition_str?: string;
  next_node_id?: string | null;
  advanced?: boolean;
  field?: string;
  operator?: string;
  value?: string;
}

export function isOthers(condition: ConditionEntry): boolean {
  return condition.condition_str?.trim() === 'others' || condition.condition_name === 'others';
}

export function moveCondition(
  conditions: ConditionEntry[],
  from: number,
  to: number,
): ConditionEntry[] {
  const regular = conditions.filter((condition) => !isOthers(condition));
  const fallback = conditions.filter(isOthers);
  if (from >= 0 && from < regular.length && to >= 0 && to < regular.length && from !== to) {
    const [moved] = regular.splice(from, 1);
    regular.splice(to, 0, moved);
  }
  return [...regular, ...fallback];
}

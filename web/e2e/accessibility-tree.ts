import type { Page } from '@playwright/test';

interface AccessibilityValue {
  value?: unknown;
}

export interface AccessibilityTreeNode {
  ignored?: boolean;
  role?: AccessibilityValue;
  name?: AccessibilityValue;
  properties?: Array<{
    name: string;
    value?: AccessibilityValue;
  }>;
}

/**
 * Read Chromium's computed accessibility tree, rather than inferring
 * accessibility from DOM attributes alone. This catches nodes that exist in
 * markup but are ignored, unnamed, or exposed with an unexpected role.
 */
export async function readAccessibilityTree(page: Page): Promise<AccessibilityTreeNode[]> {
  const session = await page.context().newCDPSession(page);
  try {
    await session.send('Accessibility.enable');
    const result = await session.send('Accessibility.getFullAXTree');
    return result.nodes as AccessibilityTreeNode[];
  } finally {
    await session.detach();
  }
}

export function findAccessibilityNode(
  nodes: AccessibilityTreeNode[],
  role: string,
  name?: string | RegExp,
): AccessibilityTreeNode | undefined {
  return nodes.find((node) => {
    if (node.ignored || node.role?.value !== role) return false;
    if (name === undefined) return true;
    const accessibleName = String(node.name?.value ?? '');
    return typeof name === 'string'
      ? accessibleName === name
      : name.test(accessibleName);
  });
}

export function accessibilityProperty(
  node: AccessibilityTreeNode | undefined,
  propertyName: string,
): unknown {
  return node?.properties?.find((property) => property.name === propertyName)?.value?.value;
}

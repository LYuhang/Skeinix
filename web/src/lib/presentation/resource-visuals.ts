import {
  BookOpen,
  Building2,
  Folder,
  Gauge,
  KeyRound,
  Library,
  ListChecks,
  MessageSquare,
  Plug,
  Rocket,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

export type ResourceKind =
  | 'chat'
  | 'workflow'
  | 'task'
  | 'deployment'
  | 'credential'
  | 'mcp'
  | 'skill'
  | 'knowledge'
  | 'storage'
  | 'organization'
  | 'management';

export interface ResourceVisual {
  icon: LucideIcon;
  foregroundClass: string;
  surfaceClass: string;
}

export const RESOURCE_VISUALS: Readonly<Record<ResourceKind, ResourceVisual>> = {
  chat: {
    icon: MessageSquare,
    foregroundClass: 'text-resource-chat',
    surfaceClass: 'bg-resource-chat/10 ring-resource-chat/15',
  },
  workflow: {
    icon: Workflow,
    foregroundClass: 'text-resource-workflow',
    surfaceClass: 'bg-resource-workflow/10 ring-resource-workflow/15',
  },
  task: {
    icon: ListChecks,
    foregroundClass: 'text-resource-task',
    surfaceClass: 'bg-resource-task/10 ring-resource-task/15',
  },
  deployment: {
    icon: Rocket,
    foregroundClass: 'text-resource-deployment',
    surfaceClass: 'bg-resource-deployment/10 ring-resource-deployment/15',
  },
  credential: {
    icon: KeyRound,
    foregroundClass: 'text-resource-credential',
    surfaceClass: 'bg-resource-credential/10 ring-resource-credential/15',
  },
  mcp: {
    icon: Plug,
    foregroundClass: 'text-resource-mcp',
    surfaceClass: 'bg-resource-mcp/10 ring-resource-mcp/15',
  },
  skill: {
    icon: BookOpen,
    foregroundClass: 'text-resource-skill',
    surfaceClass: 'bg-resource-skill/10 ring-resource-skill/15',
  },
  knowledge: {
    icon: Library,
    foregroundClass: 'text-resource-knowledge',
    surfaceClass: 'bg-resource-knowledge/10 ring-resource-knowledge/15',
  },
  storage: {
    icon: Folder,
    foregroundClass: 'text-resource-storage',
    surfaceClass: 'bg-resource-storage/10 ring-resource-storage/15',
  },
  organization: {
    icon: Building2,
    foregroundClass: 'text-resource-organization',
    surfaceClass: 'bg-resource-organization/10 ring-resource-organization/15',
  },
  management: {
    icon: Gauge,
    foregroundClass: 'text-resource-management',
    surfaceClass: 'bg-resource-management/10 ring-resource-management/15',
  },
};

export function resourceVisual(kind: ResourceKind): ResourceVisual {
  return RESOURCE_VISUALS[kind];
}

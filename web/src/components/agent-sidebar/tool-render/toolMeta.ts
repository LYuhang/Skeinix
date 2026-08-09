/**
 * Tool `name` → friendly presentation metadata (icon + i18n label key).
 *
 * The collapsed tool-call header shows a calm, human-language chip rather
 * than the raw machine tool name. This map provides the icon
 * and a translatable label per known tool; unknown tools fall back to a
 * generic wrench + the raw name (see `getToolMeta`).
 *
 * Keep this list additive: a metadata entry gives new tools a useful collapsed
 * chip before they need a dedicated renderer.
 */
import type { LucideIcon } from 'lucide-react';
import {
  Bot,
  Camera,
  Clock,
  Code,
  Columns,
  CornerDownLeft,
  Eye,
  FilePen,
  FileText,
  FolderTree,
  Globe,
  Image,
  ListChecks,
  ListFilter,
  LogIn,
  MousePointerClick,
  MoveVertical,
  PenLine,
  Play,
  Power,
  Search,
  Send,
  Tag,
  Terminal,
  Type,
  Wrench,
  Zap,
} from 'lucide-react';

export interface ToolMeta {
  icon: LucideIcon;
  /** i18n key under the `tool.meta.*` namespace. */
  labelKey: string;
}

const TOOL_META: Record<string, ToolMeta> = {
  shell: { icon: Terminal, labelKey: 'tool.meta.shell' },
  run_command: { icon: Terminal, labelKey: 'tool.meta.shell' },
  read_file: { icon: FileText, labelKey: 'tool.meta.read_file' },
  write_file: { icon: FilePen, labelKey: 'tool.meta.write_file' },
  ls: { icon: FolderTree, labelKey: 'tool.meta.ls' },
  list_files: { icon: FolderTree, labelKey: 'tool.meta.ls' },
  grep: { icon: Search, labelKey: 'tool.meta.grep' },
  run_subagent: { icon: Bot, labelKey: 'tool.meta.run_subagent' },
  // ── Run / test the workflow ────────────────────────────────────────────
  run_workflow: { icon: Play, labelKey: 'tool.meta.run_workflow' },
  node_execute: { icon: Zap, labelKey: 'tool.meta.node_execute' },
  // ── Media (vision) ─────────────────────────────────────────────────────
  read_images: { icon: Image, labelKey: 'tool.meta.read_images' },
  // ── Conversation-native interactive artifacts ─────────────────────────
  render_interactive: { icon: Eye, labelKey: 'tool.meta.render_interactive' },
  // ── Browser-automation tools ───────────────────────────────────────────
  browser_navigate: { icon: Globe, labelKey: 'tool.meta.browser_navigate' },
  browser_snapshot: { icon: Eye, labelKey: 'tool.meta.browser_snapshot' },
  browser_read_text: { icon: FileText, labelKey: 'tool.meta.browser_read_text' },
  browser_read_fields: { icon: ListChecks, labelKey: 'tool.meta.browser_read_fields' },
  browser_query: { icon: Search, labelKey: 'tool.meta.browser_query' },
  browser_get_attribute: { icon: Tag, labelKey: 'tool.meta.browser_get_attribute' },
  browser_get_html: { icon: Code, labelKey: 'tool.meta.browser_get_html' },
  browser_take_screenshot: { icon: Camera, labelKey: 'tool.meta.browser_take_screenshot' },
  browser_scroll: { icon: MoveVertical, labelKey: 'tool.meta.browser_scroll' },
  browser_wait_for: { icon: Clock, labelKey: 'tool.meta.browser_wait_for' },
  browser_list_tabs: { icon: ListFilter, labelKey: 'tool.meta.browser_list_tabs' },
  browser_tab: { icon: Columns, labelKey: 'tool.meta.browser_tab' },
  browser_click: { icon: MousePointerClick, labelKey: 'tool.meta.browser_click' },
  browser_type: { icon: Type, labelKey: 'tool.meta.browser_type' },
  browser_fill: { icon: PenLine, labelKey: 'tool.meta.browser_fill' },
  browser_select_option: { icon: ListFilter, labelKey: 'tool.meta.browser_select_option' },
  browser_press_key: { icon: CornerDownLeft, labelKey: 'tool.meta.browser_press_key' },
  browser_submit: { icon: Send, labelKey: 'tool.meta.browser_submit' },
  browser_check_login: { icon: LogIn, labelKey: 'tool.meta.browser_check_login' },
  browser_start_session: { icon: Power, labelKey: 'tool.meta.browser_start_session' },
};

/**
 * Resolve presentation metadata for a tool name. Returns a known entry, or a
 * generic fallback whose `labelKey` is empty (signalling the caller to show
 * the raw tool name verbatim).
 */
export function getToolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? { icon: Wrench, labelKey: '' };
}

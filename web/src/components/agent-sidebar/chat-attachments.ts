import type { components } from '@/lib/api/schema';

export type ChatAttachment = components['schemas']['Attachment'];
export type FileAttachment = ChatAttachment & {
  type: 'file' | 'image' | 'video';
  name: string;
  path: string;
};

export function isFileAttachment(value: ChatAttachment): value is FileAttachment {
  return (
    (value.type === 'file' || value.type === 'image' || value.type === 'video') &&
    typeof value.name === 'string' && value.name.length > 0 &&
    typeof value.path === 'string' && value.path.length > 0
  );
}

export function attachmentEmoji(type: ChatAttachment['type']): string {
  if (type === 'image') return '🖼️';
  if (type === 'video') return '🎬';
  if (type === 'file') return '📄';
  return '📎';
}

export function inferredAttachmentType(file: File): FileAttachment['type'] {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';
  return 'file';
}

export interface MentionQuery {
  start: number;
  end: number;
  query: string;
}

/** Find the unfinished @ token immediately before the caret. */
export function findAttachmentMention(value: string, caret: number): MentionQuery | null {
  const before = value.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  const query = before.slice(at + 1);
  if (query.includes('\n') || query.length > 160) return null;
  return { start: at, end: caret, query };
}

export function insertAttachmentMention(
  value: string,
  mention: MentionQuery,
  name: string,
): { value: string; caret: number } {
  const replacement = `@${name} `;
  const next = value.slice(0, mention.start) + replacement + value.slice(mention.end);
  return { value: next, caret: mention.start + replacement.length };
}

export interface EmphasizedTextPart {
  text: string;
  emphasized: boolean;
  kind?: 'attachment';
}

const MENTION_END = /[\s,.;:!?，。！？、)\]}]/u;

/**
 * Tokenize a sent user message without treating arbitrary @text as markup.
 * Attachment names come from the durable message metadata, so only references
 * to files actually attached to this turn are emphasized.
 */
export function emphasizeUserText(
  text: string,
  attachments: readonly ChatAttachment[],
): EmphasizedTextPart[] {
  const names = [...new Set(attachments.filter(isFileAttachment).map((item) => item.name))]
    .sort((a, b) => b.length - a.length);
  const parts: EmphasizedTextPart[] = [];
  let cursor = 0;

  const pushPlain = (end: number) => {
    if (end > cursor) parts.push({ text: text.slice(cursor, end), emphasized: false });
    cursor = end;
  };

  while (cursor < text.length) {
    const at = text.indexOf('@', cursor);
    if (at < 0) break;
    pushPlain(at);
    const match = names.find((name) => {
      if (!text.startsWith(name, at + 1)) return false;
      const after = text[at + 1 + name.length];
      return after === undefined || MENTION_END.test(after);
    });
    if (!match) {
      cursor = at;
      // Advance past this @ while retaining it in the next plain segment.
      const nextAt = text.indexOf('@', at + 1);
      if (nextAt < 0) break;
      parts.push({ text: text.slice(cursor, nextAt), emphasized: false });
      cursor = nextAt;
      continue;
    }
    const token = `@${match}`;
    parts.push({ text: token, emphasized: true, kind: 'attachment' });
    cursor = at + token.length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), emphasized: false });
  return parts.length > 0 ? parts : [{ text, emphasized: false }];
}

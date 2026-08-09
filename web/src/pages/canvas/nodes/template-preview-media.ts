import { useEffect, useMemo, useState } from 'react';
import { signVfs } from '@/lib/api/vfs';

const SRC_ATTRS = ['src', 'poster'] as const;
const MEDIA_SELECTOR = 'img, video, audio, source';

export function parseRenderedResult(
  result: string | undefined,
): { rendered: string; format: string } | null {
  if (!result) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(result);
  } catch {
    return null;
  }
  if (
    parsed &&
    typeof parsed === 'object' &&
    'rendered' in parsed &&
    typeof (parsed as { rendered: unknown }).rendered === 'string'
  ) {
    const value = parsed as { rendered: string; format?: unknown };
    return {
      rendered: value.rendered,
      format: typeof value.format === 'string' && value.format ? value.format : '',
    };
  }
  return null;
}

export function stripGradioWrapper(raw: string): string {
  for (const marker of ['/gradio_api/file=', '/file=']) {
    const index = raw.indexOf(marker);
    if (index === -1) continue;
    const tail = raw.slice(index + marker.length);
    try {
      return decodeURIComponent(tail);
    } catch {
      return tail;
    }
  }
  return raw;
}

export interface LocalMediaRef {
  path: string;
  isRun: boolean;
  runId?: string;
}

export function classifyMediaSrc(rawSrc: string): LocalMediaRef | null {
  const source = rawSrc.trim();
  if (!source || /^https?:\/\//i.test(source) || /^data:/i.test(source)) return null;
  const path = stripGradioWrapper(source);
  if (!path.startsWith('/run/') && !path.startsWith('/mount/') && !path.startsWith('/data/')) {
    return null;
  }
  const isRun = path.startsWith('/run/');
  const runId = isRun ? path.slice('/run/'.length).split('/')[0] || undefined : undefined;
  return { path, isRun, runId };
}

export type SignMedia = (args: {
  path: string;
  wf_id?: string;
  run_id?: string;
}) => Promise<{ url: string }>;

export async function rewriteRenderedHtml(
  html: string,
  options: { wfId?: string; runId?: string; sign: SignMedia },
): Promise<string> {
  const document = new DOMParser().parseFromString(html, 'text/html');
  const elements = Array.from(document.querySelectorAll(MEDIA_SELECTOR));
  const references = new Map<string, LocalMediaRef>();
  for (const element of elements) {
    for (const attribute of SRC_ATTRS) {
      const value = element.getAttribute(attribute);
      if (!value) continue;
      const reference = classifyMediaSrc(value);
      if (reference) references.set(reference.path, reference);
    }
  }

  const signed = new Map<string, string>();
  await Promise.all(Array.from(references.values()).map(async (reference) => {
    const { url } = await options.sign(
      reference.isRun
        ? { path: reference.path, run_id: reference.runId ?? options.runId }
        : { path: reference.path, wf_id: options.wfId },
    );
    signed.set(reference.path, url);
  }));

  for (const element of elements) {
    for (const attribute of SRC_ATTRS) {
      const value = element.getAttribute(attribute);
      if (!value) continue;
      const reference = classifyMediaSrc(value);
      const url = reference ? signed.get(reference.path) : undefined;
      if (url) element.setAttribute(attribute, url);
    }
  }
  return document.body.innerHTML;
}

export function useSignedMediaSrc(
  src: string | undefined,
  options: { wfId?: string; runId?: string; signFn?: SignMedia },
): string {
  const { wfId, runId, signFn } = options;
  const sign = signFn ?? signVfs;
  const reference = useMemo(() => (src ? classifyMediaSrc(src) : null), [src]);
  const requestKey = reference
    ? `${reference.path}|${reference.runId ?? runId ?? ''}|${wfId ?? ''}`
    : '';
  const [resolution, setResolution] = useState<{ key: string; url: string } | null>(null);

  useEffect(() => {
    if (!reference) return;
    let cancelled = false;
    void sign(
      reference.isRun
        ? { path: reference.path, run_id: reference.runId ?? runId }
        : { path: reference.path, wf_id: wfId },
    ).then(
      ({ url }) => {
        if (!cancelled) setResolution({ key: requestKey, url });
      },
      () => {
        if (!cancelled) setResolution({ key: requestKey, url: '' });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [reference, requestKey, runId, sign, wfId]);

  if (!reference) return src ?? '';
  return resolution?.key === requestKey ? resolution.url : '';
}

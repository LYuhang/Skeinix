/**
 * RenderedPreview — preview a TemplateNode's `rendered` output per its declared
 * `output_format` ∈ {'html','markdown','text'}.
 *
 *   - 'html'     → the sandboxed-iframe `<TemplateHoverPreview>` (which also
 *                  signs local /run|/mount|/data media). The media-rich path.
 *   - 'markdown' → `react-markdown` + `remark-gfm` in a bounded prose box.
 *                  Markdown `![](…)` images are signed exactly like the html
 *                  path: local `/run|/mount|/data` (and `/gradio_api/file=`
 *                  wrapped) srcs go through `useSignedMediaSrc`; http(s) pass
 *                  through. Headings/lists/tables (gfm) are unaffected.
 *   - else       → a bounded, scrollable `<pre>` of the raw text.
 *
 * Used below the raw output `<pre>` on the Run-node panel (NodeExecutePanel).
 */
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  useSignedMediaSrc,
} from '@/pages/canvas/nodes/template-preview-media';
import { TemplateHoverPreview } from '@/pages/canvas/nodes/TemplateHoverPreview';

/** Injectable sign fn (test seam — defaults to the real `signVfs` in the hook). */
type SignFn = (args: {
  path: string;
  wf_id?: string;
  run_id?: string;
}) => Promise<{ url: string }>;

/**
 * Markdown `<img>` renderer that signs local VFS-path srcs via the shared
 * `useSignedMediaSrc` hook. http(s)/data URLs pass through unchanged.
 */
function SignedMarkdownImg({
  src,
  alt,
  wfId,
  runId,
  signFn,
}: {
  src?: string;
  alt?: string;
  wfId?: string;
  runId?: string;
  signFn?: SignFn;
}) {
  const resolved = useSignedMediaSrc(src, { wfId, runId, signFn });
  // Local VFS media is signed asynchronously. Rendering `src=""` while the
  // signature is pending can make browsers request the current document as an
  // image, so keep the slot empty until a usable URL exists.
  if (!resolved) return null;
  return <img src={resolved} alt={alt ?? ''} style={{ maxWidth: '100%' }} />;
}

export function RenderedPreview({
  rendered,
  format,
  wfId,
  runId,
  signFn,
}: {
  rendered: string;
  format: string;
  wfId?: string;
  runId?: string;
  /**
   * Test seam: inject a sign fn for markdown-image signing. Defaults to the
   * real `signVfs` (used by the html path too). Injected rather than `vi.mock`'d
   * so this stays safe under vitest `isolate:false` (no shared-module-graph mock
   * clobbering — see feedback_vitest_isolate_false).
   */
  signFn?: SignFn;
}) {
  if (format === 'html') {
    return (
      <div data-testid="rendered-preview">
        <TemplateHoverPreview
          rendered={rendered}
          wfId={wfId}
          runId={runId}
          signFn={signFn}
        />
      </div>
    );
  }

  if (format === 'markdown') {
    return (
      <div
        data-testid="rendered-preview"
        className="prose prose-sm max-h-64 overflow-auto rounded-md border bg-background p-2 text-xs dark:prose-invert"
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={
            {
              img: (props) => (
                <SignedMarkdownImg
                  src={props.src}
                  alt={props.alt}
                  wfId={wfId}
                  runId={runId}
                  signFn={signFn}
                />
              ),
            } as Components
          }
        >
          {rendered}
        </ReactMarkdown>
      </div>
    );
  }

  return (
    <pre
      data-testid="rendered-preview"
      className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted p-2 text-xs select-text"
    >
      {rendered}
    </pre>
  );
}

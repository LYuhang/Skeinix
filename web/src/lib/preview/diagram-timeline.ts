export type DiagramPreviewStage = 'T0' | 'T1' | 'T2' | 'T3';

export interface DiagramPreviewTimelineEntry {
  stage: DiagramPreviewStage;
  path: string;
  revision: string;
  timestamp: number;
  eventId?: number;
}

declare global {
  interface Window {
    __VIBECANVAS_DIAGRAM_TIMELINE__?: DiagramPreviewTimelineEntry[];
  }
}

const MAX_TIMELINE_ENTRIES = 200;

/** Record release evidence without coupling product behavior to the test. */
export function recordDiagramPreviewTimeline(entry: DiagramPreviewTimelineEntry) {
  if (typeof window === 'undefined') return;
  const timeline = window.__VIBECANVAS_DIAGRAM_TIMELINE__ ?? [];
  const duplicate = timeline.some((item) => (
    item.stage === entry.stage
    && item.path === entry.path
    && item.revision === entry.revision
    && item.eventId === entry.eventId
  ));
  if (duplicate) return;
  timeline.push(entry);
  if (timeline.length > MAX_TIMELINE_ENTRIES) {
    timeline.splice(0, timeline.length - MAX_TIMELINE_ENTRIES);
  }
  window.__VIBECANVAS_DIAGRAM_TIMELINE__ = timeline;
  window.dispatchEvent(new CustomEvent('vibecanvas:diagram-preview-stage', {
    detail: entry,
  }));
}

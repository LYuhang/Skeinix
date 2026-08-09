import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NODE_LABELS } from '@/pages/canvas/nodes/NODE_TYPES';
import { NODE_CATALOG, nodeDescKey } from './nodeCatalog';
import { NodeCard } from './NodeCard';
import { Input } from '@/components/ui/input';

/**
 * Explorer "Nodes" palette (#11) — lists every base node type as a card the
 * user can DRAG onto the canvas or CLICK to insert at viewport center. The
 * type list + labels come from the canonical `NODE_LABELS`; descriptions
 * resolve via i18n `nodes_palette.desc.*`.
 * A filterable draggable node palette, kept flat (no
 * grouping) since every entry is already a single node type.
 */
export function NodesSection({ readOnly }: { readOnly: boolean }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return NODE_CATALOG;
    return NODE_CATALOG.filter((nt) => {
      const label = NODE_LABELS[nt] ?? nt;
      const desc = t(nodeDescKey(nt), '');
      return [nt, label, desc].join(' ').toLowerCase().includes(q);
    });
  }, [filter, t]);

  return (
    <div>
      <Input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder={t('nodes_palette.filter', 'Filter nodes…')}
        className="mx-2 mb-1 h-8 w-[calc(100%-1rem)] px-2 text-xs"
      />
      {filtered.length === 0 ? (
        <div className="px-3 py-1 text-xs text-muted-foreground">
          {t('nodes_palette.no_match', 'No nodes match your filter.')}
        </div>
      ) : (
        <div className="px-1">
          {filtered.map((nt) => (
            <NodeCard key={nt} nodeType={nt} readOnly={readOnly} />
          ))}
        </div>
      )}
    </div>
  );
}
